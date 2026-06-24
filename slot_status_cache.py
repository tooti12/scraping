# slot_status_cache.py
"""Continuous background loop backing the public slot-checker's
availability page (dashboard/templates/availability.html).

Every cycle, countries in config.COUNTRIES are checked concurrently, each in
its own OS process (multiprocessing, not threading) — this is the piece
that matters:

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
MAX_CONCURRENT_COUNTRIES below caps concurrency to 1 on any non-Linux box
(plain sequential, same as the original design) and only parallelizes
where Xvfb actually isolates each session.

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
Every page load and /api/status poll just reads the resulting cache, and
visitor traffic can never block or be blocked by it. See DEPLOYMENT.md.
"""
import multiprocessing as mp
import sys
import threading
import time
from datetime import datetime, timezone

from config import COUNTRIES
from slot_check_service import check_country_slot

# One full pass over all countries, then wait this long before the next pass.
REFRESH_INTERVAL_SECONDS = 60

# Pause this long after one country's check finishes before starting the
# next one, so checks don't slam VFS back-to-back.
COUNTRY_BREAK_SECONDS = 35

# See module docstring — full parallelism is only safe where every Chrome
# session gets its own real (virtual) display.
MAX_CONCURRENT_COUNTRIES = len(COUNTRIES) if sys.platform.startswith("linux") else 1

# Same rationale as the watchdog this replaced in dashboard/app.py: a wedged
# Selenium call has no timeout of its own. Without this, one stuck country
# would freeze the whole loop and every other country's cached status would
# go stale forever instead of just that one country reporting an error. This
# bounds how long a single country's *process* runs before its own internal
# watchdog (in _check_one) force-quits its browser and lets it exit. Sized
# generously because check_slot_only() may now submit several sub-category
# combinations (one captcha-solve + banner-wait each) for missions that
# don't offer Tourism, not just one.
CHECK_TIMEOUT_SECONDS = 900

# Extra time the parent process gives a country's process to actually exit
# after CHECK_TIMEOUT_SECONDS, before concluding the process itself (not
# just the browser) is stuck and killing it outright. Covers the rare case
# where even browser.sb.driver.quit() hangs.
PROCESS_JOIN_GRACE_SECONDS = 60

_lock = threading.Lock()
_status: dict = {}


def get_all_status() -> dict:
    """Safe-to-render snapshot of the current cache, keyed by country code."""
    with _lock:
        return {code: dict(data) for code, data in _status.items()}


def _check_one(code: str, login_lock) -> dict:
    """Runs the actual check in an inner thread so this can give up after
    CHECK_TIMEOUT_SECONDS and force-quit the browser rather than block
    forever — Python can't force-kill a thread, but force-quitting the
    browser unblocks whatever Selenium call is stuck, so the abandoned
    thread exits shortly after anyway. Process-agnostic: this runs inside
    whichever OS process calls it (see _process_entry)."""
    browser_holder = {}
    holder = {}
    done = threading.Event()

    def on_browser(browser):
        browser_holder["browser"] = browser

    def run():
        try:
            holder["result"] = check_country_slot(code, on_browser=on_browser, login_lock=login_lock)
        except Exception:
            holder["result"] = {"status": "error", "message": "Something went wrong while checking."}
        done.set()

    threading.Thread(target=run, daemon=True).start()
    finished = done.wait(timeout=CHECK_TIMEOUT_SECONDS)

    if not finished:
        browser = browser_holder.get("browser")
        if browser is not None:
            try:
                browser.sb.driver.quit()
            except Exception:
                pass
        return {"status": "error", "message": "Check timed out (possibly a captcha that couldn't be solved)."}

    return holder.get("result", {"status": "error", "message": "Something went wrong while checking."})


def _process_entry(code: str, login_lock, result_queue) -> None:
    """Entry point for one country's dedicated OS process."""
    try:
        result = _check_one(code, login_lock)
    except Exception:
        result = {"status": "error", "message": "Something went wrong while checking."}
    result_queue.put((code, result))


def _run_one_cycle() -> None:
    login_lock = mp.Lock()
    result_queue = mp.Queue()
    pending = [c["code"] for c in COUNTRIES]
    processes: dict = {}
    started_at: dict = {}

    def spawn_next():
        if not pending or _stop_event.is_set():
            return
        code = pending.pop(0)
        p = mp.Process(target=_process_entry, args=(code, login_lock, result_queue), daemon=True)
        p.start()
        processes[code] = p
        started_at[code] = time.monotonic()

    for _ in range(min(MAX_CONCURRENT_COUNTRIES, len(pending))):
        spawn_next()

    while processes or (pending and not _stop_event.is_set()):
        try:
            code, result = result_queue.get(timeout=1)
        except Exception:
            code = None

        if code is not None:
            with _lock:
                _status[code] = {**result, "checked_at": datetime.now(timezone.utc).isoformat()}
            processes.pop(code, None)
            started_at.pop(code, None)
            # A stop request stops new countries from starting, but never
            # force-quits a check already in flight — those finish (or hit
            # their own CHECK_TIMEOUT_SECONDS watchdog) naturally.
            if pending and not _stop_event.is_set():
                _stop_event.wait(timeout=COUNTRY_BREAK_SECONDS)
            spawn_next()

        # Any process that's blown past its own in-process watchdog plus
        # grace is stuck at the process level, not just the browser. Kill
        # it outright so it can't block this cycle (or pile up) forever,
        # and free its slot for the next pending country.
        now = time.monotonic()
        for stuck_code in [c for c in processes if now - started_at[c] > CHECK_TIMEOUT_SECONDS + PROCESS_JOIN_GRACE_SECONDS]:
            try:
                processes[stuck_code].terminate()
                processes[stuck_code].join(timeout=5)
            except Exception:
                pass
            with _lock:
                _status[stuck_code] = {
                    "status": "error",
                    "message": "Check process became unresponsive and was stopped.",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            processes.pop(stuck_code, None)
            started_at.pop(stuck_code, None)
            spawn_next()


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
        with _started_lock:
            _first_cycle_done = True
            _next_cycle_at = time.time() + REFRESH_INTERVAL_SECONDS
        _stop_event.wait(timeout=REFRESH_INTERVAL_SECONDS)
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
    global _started, _first_cycle_done, _is_checking, _next_cycle_at
    with _started_lock:
        if _started:
            return False
        _started = True
        _is_checking = False
        _next_cycle_at = None
        _first_cycle_done = False
        _stop_event.clear()
        threading.Thread(target=_run_loop, daemon=True).start()
        return True


def stop() -> None:
    """Signals the background loop to stop: no new country checks get
    started, but whatever's already running in-flight is left to finish on
    its own rather than force-quit mid-check. is_running() flips to False
    immediately so the frontend can offer "Start Bot" again right away;
    a fresh start() after that begins a brand new cycle from scratch. Safe
    to call even when nothing is running."""
    global _started
    _stop_event.set()
    with _started_lock:
        _started = False
