# slot_status_cache.py
"""Continuous background loop backing the public slot-checker's
availability page (dashboard/templates/availability.html).

Each country in config.COUNTRIES gets one long-lived OS process (a
"worker", spawned by _ensure_worker() and run by
slot_check_service.run_country_worker) that logs in to VFS *once* and then
answers repeated "check" commands on that same authenticated session,
instead of logging in fresh for every single check:

- VFS's own block message ("too many login attempts... try again in one
  hour") is triggered by login frequency, not check frequency — the old
  design opened a brand new browser and did a full login + OTP wait on
  every cycle, for every country, on one shared account. That's exactly
  the pattern VFS's anti-bot logic is watching for. Reusing one session
  across many cycles (re-authenticating only when VFS's own
  SESSION_EXPIRED signal says to — see BrowserClient._navigate_to_booking_
  form and slot_check_service.run_country_worker) cuts login frequency by
  roughly the same factor as the cycle count, while still checking just as
  often.
- It also means each country sits on an authenticated, ready
  /application-detail session for as long as it runs, instead of nothing
  existing between checks — useful groundwork for a future "click a slot,
  go straight to booking" flow that wants to hand off a live session
  rather than open a second browser from scratch.
- Trade-off: every country's Chrome instance now stays open continuously
  (idle between checks) rather than only existing for the few seconds it's
  actively checking, so idle memory/CPU usage is higher than the old
  spawn-fresh-every-cycle design. Worth it for the login-frequency fix.

Workers run as separate OS processes (multiprocessing, not threading) for
the same reasons as before:

- Selenium + pyautogui's screen-driven Cloudflare solving is heavy, mostly
  synchronous work. Running several of those as Python *threads* in the
  same process as the Flask dashboard means they all fight over one GIL,
  and the dashboard's own request-handling thread can get starved badly
  enough that the frontend looks stuck on "loading" even though the backend
  is working fine. Separate OS processes each get their own interpreter and
  GIL, so the dashboard stays responsive no matter how much automation work
  is running — and a browser refresh can never affect the backend
  processes at all, since the request-handling code never touches them
  directly, only the in-memory cache below.
- On Linux, each process also gets its own isolated virtual display
  (browser_client.py passes xvfb=True to SeleniumBase), so simultaneous
  Cloudflare-solving clicks across countries can never land on the wrong
  window — process-per-country gives this for free, since DISPLAY is a
  process-wide setting Xvfb hands out per process.

That second point is Linux-only: SeleniumBase's xvfb support auto-disables
itself everywhere else (confirmed in its source), so on Windows or macOS
every Chrome window would share the one real desktop. In practice this
caused undetected-chromedriver's window-focus/reconnect logic to get
confused with multiple simultaneous UC sessions on one screen — observed as
random "NoSuchWindowException: Active window was already closed" crashes.
MAX_CONCURRENT_COUNTRIES below caps how many workers are actively
*checking* at once to 1 on any non-Linux box (plain sequential, same as the
original design) and only parallelizes where Xvfb actually isolates each
session — every worker still exists as its own idle process the rest of the
time, just not actively driving its browser unless commanded to check.

The one thing that can't run fully in parallel even on Linux is login:
every country shares one Gmail inbox for OTPs, so the window from "click
login" (which makes VFS send the email) through "OTP verified" is
serialized across all of them via a single multiprocessing.Lock — one
country logs in at a time, but everything else (typing credentials, and
the whole slot-check after login) runs concurrently.

The loop doesn't start on its own — it only begins once a visitor clicks
"Start Bot" on /availability (see start()/is_running() and
dashboard/app.py's /api/start-bot), and runs until a visitor explicitly
stops it (stop()/dashboard/app.py's /api/stop-bot) or the process exits.
stop() tears down every worker (closing its Chrome session) rather than
leaving them running idle, so a later start() always begins from a clean
slate. Every page load and /api/status poll just reads the resulting
cache, and visitor traffic can never block or be blocked by it. See
DEPLOYMENT.md.
"""
import json
import multiprocessing as mp
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from queue import Queue as ThreadQueue

from config import COUNTRIES
from slot_check_service import run_country_worker

_PERSIST_FILE = Path(__file__).parent / "slot_results.json"

# One full pass over all countries, then wait a random amount of time in
# this range before the next pass. A range instead of one fixed number so
# the loop's timing doesn't look as mechanically regular to VFS — same
# reasoning as COUNTRY_BREAK_SECONDS_MIN/MAX below. Widened to 3-5 minutes
# (was 45-90s): checking persists a logged-in session now rather than
# re-authenticating every cycle, so login frequency (what VFS's own block
# message specifically calls out) no longer scales with this — but each
# check is still a real, visible interaction with VFS's app, and a slot
# opening up isn't materially less useful to know about 4 minutes later
# than 1 minute later on a dashboard that's informational, not
# auto-booking. Cuts total request volume ~3-4x for negligible real loss.
REFRESH_INTERVAL_SECONDS_MIN = 180
REFRESH_INTERVAL_SECONDS_MAX = 300

# Pause a random amount of time in this range after one country's check
# finishes before commanding the next one, so checks don't slam VFS
# back-to-back at an exact, easily-fingerprinted interval.
COUNTRY_BREAK_SECONDS_MIN = 30
COUNTRY_BREAK_SECONDS_MAX = 50

# How long to stop retrying a country after it hits VFS's own block page
# (see browser_client.check_is_ip_blocked() / auth_handler.AuthHandler.blocked).
# VFS's own block message says "try again in one hour" — padded a bit since
# retrying right at the hour mark risks immediately re-triggering it.
BLOCK_COOLDOWN_SECONDS = 90 * 60

# See module docstring — full parallelism is only safe where every Chrome
# session gets its own real (virtual) display.
MAX_CONCURRENT_COUNTRIES = 1

# Same rationale as the watchdog this replaced in dashboard/app.py: a wedged
# Selenium call has no timeout of its own. Without this, one stuck country
# would freeze the whole loop and every other country's cached status would
# go stale forever instead of just that one country reporting an error. This
# bounds how long a single "check" command may run before slot_check_service
# itself force-quits that country's wedged browser (see
# slot_check_service._run_with_watchdog) and reports a timeout — the worker
# process survives this and rebuilds a fresh browser next check. Sized
# generously because check_slot_only() may now submit several sub-category
# combinations (one captcha-solve + banner-wait each) for missions that
# don't offer Tourism, not just one.
CHECK_TIMEOUT_SECONDS = 1200

# Extra time the main loop gives a worker to actually respond after
# CHECK_TIMEOUT_SECONDS before concluding the *process* itself (not just its
# browser) is stuck and killing it outright. Covers the rare case where even
# browser.sb.driver.quit() hangs. A replacement worker is spawned right
# after, so this only costs one cycle's check for that country.
PROCESS_JOIN_GRACE_SECONDS = 60

_lock = threading.Lock()
_status: dict = {}

# ── Live log stream ───────────────────────────────────────────────────────
# Worker processes write to _logs_queue (mp.Queue). A relay thread reads
# from it and fans out to each connected SSE client's ThreadQueue.
_logs_queue = None
_log_subs: list[ThreadQueue] = []
_log_subs_lock = threading.Lock()


def subscribe_logs() -> ThreadQueue:
    q: ThreadQueue = ThreadQueue(maxsize=500)
    with _log_subs_lock:
        _log_subs.append(q)
    return q


def unsubscribe_logs(q: ThreadQueue) -> None:
    with _log_subs_lock:
        try:
            _log_subs.remove(q)
        except ValueError:
            pass


def _broadcast_log(line: str) -> None:
    with _log_subs_lock:
        for q in list(_log_subs):
            try:
                q.put_nowait(line)
            except Exception:
                pass


def _log_relay() -> None:
    """Reads log lines from workers (via mp Queue) and fans out to SSE clients."""
    while not _stop_event.is_set():
        try:
            line = _logs_queue.get(timeout=1)
            _broadcast_log(line)
        except Exception:
            pass
# country code -> epoch seconds until which _run_one_cycle() won't retry it.
# country code -> {"process": mp.Process, "command_queue": mp.Queue}, one
# entry per country's long-lived worker (see module docstring). Both dicts
# below are only ever touched from the main process's loop thread
# (_run_loop / _run_one_cycle) *or* from stop(), which runs on whichever
# Flask request thread handles /api/stop-bot — _workers_lock guards against
# those two racing on the same dict.
_blocked_until: dict = {}
_workers: dict = {}
_workers_lock = threading.Lock()
# Shared across every worker for the lifetime of one start()/stop() cycle —
# see module docstring on why login itself stays serialized across countries.
_login_lock = None
_result_queue = None


def get_all_status() -> dict:
    """Safe-to-render snapshot of the current cache, keyed by country code."""
    with _lock:
        return {code: dict(data) for code, data in _status.items()}


def _save_results() -> None:
    """Write current slot results to disk so they survive server restarts."""
    try:
        with _lock:
            snapshot = {k: dict(v) for k, v in _status.items()}
        with open(_PERSIST_FILE, "w") as f:
            json.dump(snapshot, f)
    except Exception as e:
        print(f"[slot_status_cache] Failed to save results to disk: {e}")


def _load_results() -> None:
    """Restore slot results from disk on startup (if the file exists)."""
    try:
        if _PERSIST_FILE.exists():
            with open(_PERSIST_FILE) as f:
                data = json.load(f)
            with _lock:
                _status.update(data)
            print(f"[slot_status_cache] Loaded persisted results for {len(data)} country(ies) from disk.")
    except Exception as e:
        print(f"[slot_status_cache] Failed to load persisted results: {e}")


def _spawn_worker(code: str) -> None:
    command_queue = mp.Queue()
    p = mp.Process(
        target=run_country_worker,
        args=(code, command_queue, _result_queue, _login_lock, CHECK_TIMEOUT_SECONDS, _logs_queue),
        daemon=True,
    )
    p.start()
    with _workers_lock:
        _workers[code] = {"process": p, "command_queue": command_queue}


def _ensure_worker(code: str) -> None:
    with _workers_lock:
        w = _workers.get(code)
        alive = w is not None and w["process"].is_alive()
    if not alive:
        _spawn_worker(code)


def _kill_worker(code: str) -> None:
    """For a worker that's stopped responding — no point asking it nicely."""
    with _workers_lock:
        w = _workers.pop(code, None)
    if w is None:
        return
    try:
        w["process"].terminate()
        w["process"].join(timeout=5)
    except Exception:
        pass


def _stop_all_workers() -> None:
    """Asks every worker to stop (closing its Chrome session) and waits for
    them in parallel rather than one at a time, so this takes roughly as
    long as the single slowest shutdown, not the sum of all of them."""
    with _workers_lock:
        workers = list(_workers.items())
        _workers.clear()
    for _, w in workers:
        try:
            w["command_queue"].put("stop")
        except Exception:
            pass
    for _, w in workers:
        try:
            w["process"].join(timeout=5)
            if w["process"].is_alive():
                w["process"].terminate()
        except Exception:
            pass


def _run_one_cycle() -> None:
    now = time.time()
    on_cooldown = [c["code"] for c in COUNTRIES if _blocked_until.get(c["code"], 0) > now]
    pending = [c["code"] for c in COUNTRIES if c["code"] not in on_cooldown]

    # Don't even command a check for a country still cooling down from a
    # detected VFS block — just refresh its cached message so the
    # availability page explains the pause instead of looking stuck on a
    # stale error. Real retry happens once BLOCK_COOLDOWN_SECONDS elapses.
    for code in on_cooldown:
        remaining_min = max(1, round((_blocked_until[code] - now) / 60))
        with _lock:
            _status[code] = {
                "status": "error",
                "message": f"Paused after a VFS block — retrying in ~{remaining_min} min.",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        _save_results()

    busy: dict = {}  # country code -> time.monotonic() when "check" was sent

    def command_next():
        if not pending or _stop_event.is_set():
            return
        code = pending.pop(0)
        _ensure_worker(code)
        with _workers_lock:
            _workers[code]["command_queue"].put("check")
        busy[code] = time.monotonic()

    for _ in range(min(MAX_CONCURRENT_COUNTRIES, len(pending))):
        command_next()

    while busy or (pending and not _stop_event.is_set()):
        try:
            code, result = _result_queue.get(timeout=1)
        except Exception:
            code = None

        # Guards against a stale result from a worker we already gave up on
        # (killed as "stuck" below) arriving late and getting misattributed.
        if code is not None and code in busy:
            if result.get("blocked"):
                _blocked_until[code] = time.time() + BLOCK_COOLDOWN_SECONDS
            with _lock:
                _status[code] = {**result, "checked_at": datetime.now(timezone.utc).isoformat()}
            _save_results()
            busy.pop(code, None)
            # A stop request stops new countries from starting, but never
            # force-quits a check already in flight — those finish (or hit
            # their own CHECK_TIMEOUT_SECONDS watchdog) naturally.
            if pending and not _stop_event.is_set():
                _stop_event.wait(timeout=random.uniform(COUNTRY_BREAK_SECONDS_MIN, COUNTRY_BREAK_SECONDS_MAX))
            command_next()

        # Any worker that's blown past its own in-process watchdog plus
        # grace is stuck at the process level, not just the browser. Kill
        # it outright so it can't block this cycle (or pile up) forever; a
        # fresh replacement worker is spawned to take over for next time.
        now_m = time.monotonic()
        for stuck_code in [c for c in busy if now_m - busy[c] > CHECK_TIMEOUT_SECONDS + PROCESS_JOIN_GRACE_SECONDS]:
            _kill_worker(stuck_code)
            with _lock:
                _status[stuck_code] = {
                    "status": "error",
                    "message": "Check process became unresponsive and was stopped.",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            _save_results()
            busy.pop(stuck_code, None)
            command_next()


def _run_loop():
    global _first_cycle_done, _is_checking, _next_cycle_at
    while not _stop_event.is_set():
        with _started_lock:
            _is_checking = True
            _next_cycle_at = None
        _run_one_cycle()
        with _started_lock:
            _is_checking = False
        if _stop_event.is_set():
            # Stopped mid-cycle — not every country actually got checked,
            # so this pass doesn't count toward first_cycle_done.
            break
        # Computed once and reused for both the actual wait and the
        # frontend-facing countdown, so "Bot will start in Ns" always
        # matches when the next cycle really starts instead of drifting
        # against a fixed number while the real wait varies.
        wait_seconds = random.uniform(REFRESH_INTERVAL_SECONDS_MIN, REFRESH_INTERVAL_SECONDS_MAX)
        with _started_lock:
            _first_cycle_done = True
            _next_cycle_at = time.time() + wait_seconds
        _stop_event.wait(timeout=wait_seconds)
    _stop_all_workers()
    with _started_lock:
        _started = False
        _is_checking = False
        _next_cycle_at = None


_started = False
_first_cycle_done = False
_is_checking = False
_next_cycle_at: float | None = None
_started_lock = threading.Lock()
_stop_event = threading.Event()
# Tracked so start() can refuse to begin a second generation on top of one
# that's still tearing down its workers (e.g. a quick stop-then-start
# click before in-flight checks finished) — two _run_loop generations
# mutating the shared _workers/_login_lock/_result_queue at once would
# corrupt that state.
_loop_thread: threading.Thread | None = None


def is_running() -> bool:
    with _started_lock:
        return _started


def first_cycle_done() -> bool:
    """True once every country has been checked at least once since the
    most recent start() — what the frontend polls to know when it can stop
    showing the "checking all countries" loader and just read the cache."""
    with _started_lock:
        return _first_cycle_done


def get_cycle_state() -> dict:
    """What the loop is doing right now, for the frontend's "Bot is
    resting" countdown between cycles. "checking" is True while a pass over
    every country is actively running (including the very first one, before
    first_cycle_done); when it's False and the loop is running,
    "next_check_in_seconds" counts down to the next pass."""
    with _started_lock:
        if not _started:
            return {"checking": False, "next_check_in_seconds": None}
        if _is_checking or _next_cycle_at is None:
            return {"checking": True, "next_check_in_seconds": None}
        remaining = max(0, round(_next_cycle_at - time.time()))
        return {"checking": False, "next_check_in_seconds": remaining}


def start() -> bool:
    """Starts the background loop if it isn't already running — does
    nothing to launch Chrome on its own; it only ever runs once a visitor
    explicitly clicks "Start Bot" on /availability (dashboard/app.py's
    /api/start-bot). Safe to call more than once (e.g. every click, or from
    more than one browser tab) — only the first call actually starts
    anything. Returns True if this call is the one that started it."""
    global _started, _first_cycle_done, _is_checking, _next_cycle_at, _login_lock, _result_queue, _loop_thread, _logs_queue

    # stop() flips _started=False immediately so the UI re-enables "Start Bot",
    # but the loop thread keeps running _stop_all_workers() for a few more
    # seconds. Without this join, a quick re-click on Start silently returns
    # False (thread still alive) and the bot never restarts.
    with _started_lock:
        old_thread = _loop_thread
    if old_thread is not None and old_thread.is_alive():
        old_thread.join(timeout=20)

    with _started_lock:
        if _started:
            return False
        if _loop_thread is not None and _loop_thread.is_alive():
            return False  # still alive after 20s timeout — genuinely stuck
        _started = True
        _is_checking = False
        _next_cycle_at = None
        _first_cycle_done = False
        _stop_event.clear()
        _login_lock = mp.Lock()
        _result_queue = mp.Queue()
        _logs_queue = mp.Queue()
        threading.Thread(target=_log_relay, daemon=True).start()
        _loop_thread = threading.Thread(target=_run_loop, daemon=True)
        _loop_thread.start()
        return True


def stop() -> None:
    """Signals the background loop to stop: no new country checks get
    commanded, but whatever's already running in-flight is left to finish
    on its own rather than force-quit mid-check. is_running() flips to
    False immediately so the frontend can offer "Start Bot" again right
    away; the loop thread then tears down every worker (closing its Chrome
    session) in the background. A fresh start() after that spawns brand
    new workers from scratch. Safe to call even when nothing is running."""
    global _started
    _stop_event.set()
    with _started_lock:
        _started = False


# Restore any results persisted from a previous run so slot data survives
# server restarts — users see the last known state immediately on page load
# rather than a blank slate until the bot has finished a full cycle.
_load_results()
