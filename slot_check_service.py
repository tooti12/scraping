# slot_check_service.py
"""Booking-free VFS slot check used by slot_status_cache.py's continuous
background loop, which keeps the public slot-checker homepage's per-country
status cache fresh.

Runs headed (not headless) — Cloudflare's challenge is solved via
SeleniumBase's uc_gui_click_captcha(), which drives a real OS-level mouse
click and does not work in a headless browser.

Deliberately separate from main.py's continuous monitor and from
booking_flow.py's human-in-the-loop booking pipeline — this module only
logs in and checks London-centre availability for one country, returning a
plain result dict. It never books anything. Sub-category scope is Tourism
when the mission offers it, otherwise every sub-category the mission does
offer (see BrowserClient.check_slot_only) — there's no single "Tourism" to
fall back to for every country.

Two entry points:
- check_country_slot() — one-shot: opens a browser, logs in, checks once,
  closes. Simple, but means every call pays for a full login + OTP wait,
  which is exactly the "too many login attempts" pattern that gets VFS
  accounts blocked if done on every poll. Kept around for one-off/manual
  use (it's what the proxy/session diagnostics in this project have used).
- run_country_worker() — long-lived: logs in once, then answers a "check"
  command with another check_slot_only() call on that *same* session,
  re-authenticating only when VFS's own SESSION_EXPIRED signal says to.
  This is what slot_status_cache.py actually runs now, since it's the
  same login-frequency fix call_country_slot() can't give you on its own.
  It also means each country sits on an authenticated, ready
  /application-detail session the whole time it runs — useful groundwork
  for a future "click a slot, go straight to booking" flow, which would
  otherwise need to open a second browser from scratch at exactly the
  moment speed matters most (a slot that's still there now might not be by
  the time a fresh login finishes).
"""
import threading

from auth_handler import AuthHandler
from browser_client import BrowserClient
from config import SHARED_VFS_ACCOUNT


def _classify_outcome(outcome: dict) -> dict:
    """Turns a BrowserClient.check_slot_only() outcome into the public
    result shape: {"status", "centre"?, "appt_cat"?, "combos"?, "message"?}.
    Shared by both entry points below so they can never drift apart."""
    combos = outcome.get("combos")
    if combos is not None:
        combo_results = [
            {
                "sub_cat": c["sub_cat"]["text"],
                "status": c["result"],
                "slot_details": c.get("slot_details", ""),
            }
            for c in combos
        ]
        if any(c["status"] == "slots_available" for c in combo_results):
            overall_status = "slots_available"
        elif any(c["status"] == "no_slots" for c in combo_results):
            overall_status = "no_slots"
        else:
            overall_status = "error"
        return {
            "status": overall_status,
            "centre": outcome["centre"]["text"],
            "appt_cat": outcome["appt_cat"]["text"],
            "combos": combo_results,
        }

    reason = outcome.get("reason", "unexpected_error")
    return {"status": "error", "message": f"Could not complete the check ({reason})."}


def check_country_slot(country: str, on_status=None, on_browser=None, login_lock=None) -> dict:
    """Run one slot check for `country`. Always returns a dict with a
    "status" key: "slots_available" | "no_slots" | "error". Non-error
    results also carry "centre", "appt_cat", and "combos" — a list of
    {"sub_cat", "status", "slot_details"} dicts, one per sub-category
    checked (e.g. "Tourism", or "Long Stay"/"Short Stay" for missions
    without Tourism); "status" is overall across all of them. An "error"
    result may also carry "blocked": True when VFS's own block page/message
    was detected during login (see AuthHandler.blocked) — slot_status_cache
    reads this to apply a long cooldown for that country instead of
    retrying it again next cycle.

    on_status, if given, is called with a short event-name string at each
    real milestone (connecting, logging in, OTP wait, etc.) — this is what
    lets dashboard/app.py mirror actual backend progress to the frontend
    instead of a generic/disconnected loading message.

    on_browser, if given, is called once with the live BrowserClient as soon
    as it exists — dashboard/app.py uses this to stash a handle to it so a
    user-initiated cancel can force-quit the Chrome session mid-check.

    login_lock, if given, is a lock (a multiprocessing.Lock in practice)
    shared across every concurrently-running call (see slot_status_cache.py)
    — passed straight
    through to AuthHandler, which holds it only for the part of login that
    touches the one shared Gmail inbox (click login through OTP verified),
    so two countries' sessions can never have an OTP email in flight at the
    same time."""

    def emit(event):
        if on_status:
            try:
                on_status(event)
            except Exception:
                pass

    email = SHARED_VFS_ACCOUNT["email"]
    password = SHARED_VFS_ACCOUNT["password"]

    try:
        emit("connecting")
        with BrowserClient(country, proxy=True, headless=False) as browser:
            if on_browser:
                try:
                    on_browser(browser)
                except Exception:
                    pass
            auth = AuthHandler(country, email, password, browser, on_status=emit, login_lock=login_lock)
            token = auth.authenticate()
            if token is None:
                result = {"status": "error", "message": "Could not log in to VFS right now."}
                if auth.blocked:
                    # Tells slot_status_cache.py to cool this country down
                    # for a while instead of retrying next cycle, which
                    # would just keep hitting the same block.
                    result["blocked"] = True
                return result

            browser.sb.sleep(3)
            emit("checking_centres")
            outcome = browser.check_slot_only()
            emit("finalizing")
    except Exception as e:
        print(f"[slot_check_service] Check failed for {country}: {e}")
        return {"status": "error", "message": "Something went wrong while checking. Please try again."}

    return _classify_outcome(outcome)


def _run_with_watchdog(browser, fn, timeout_seconds):
    """Runs fn() in an inner thread, force-quitting `browser` and raising
    TimeoutError if it doesn't finish in time. Same rationale as
    slot_status_cache's old per-cycle watchdog: Python can't force-kill a
    thread, but force-quitting the browser unblocks whatever Selenium call
    is stuck, so the abandoned thread exits shortly after anyway. Unlike
    the old watchdog, the caller (run_country_worker) survives this — only
    the wedged browser dies, so the worker process can rebuild a fresh one
    and keep going instead of needing a full process restart."""
    holder = {}
    done = threading.Event()

    def run():
        try:
            holder["value"] = fn()
        except BaseException as e:
            holder["error"] = e
        done.set()

    threading.Thread(target=run, daemon=True).start()
    if not done.wait(timeout=timeout_seconds):
        try:
            browser.sb.driver.quit()
        except Exception:
            pass
        raise TimeoutError("Check timed out (possibly a captcha that couldn't be solved).")
    if "error" in holder:
        raise holder["error"]
    return holder["value"]


def run_country_worker(country: str, command_queue, result_queue, login_lock=None, check_timeout_seconds=900) -> None:
    """Long-lived loop for one country, run in its own dedicated OS process
    by slot_status_cache.py. Logs in once, then answers each "check" command
    from `command_queue` with a result on `result_queue` (same shape as
    check_country_slot()'s, plus the worker never closing its browser
    between checks) — re-authenticating only when a check actually fails
    with VFS's SESSION_EXPIRED signal (see
    BrowserClient._navigate_to_booking_form), not on every single check.
    Exits cleanly on a "stop" command.
    """
    email = SHARED_VFS_ACCOUNT["email"]
    password = SHARED_VFS_ACCOUNT["password"]

    browser: BrowserClient | None = None
    authenticated = False

    def close_browser():
        nonlocal browser, authenticated
        if browser is not None:
            try:
                browser.__exit__(None, None, None)
            except Exception:
                pass
        browser = None
        authenticated = False

    def authenticate() -> dict | None:
        """Returns None on success, or an error result dict on failure."""
        nonlocal browser, authenticated
        if browser is None:
            browser = BrowserClient(country, proxy=True, headless=False)
            browser.__enter__()
        auth = AuthHandler(country, email, password, browser, login_lock=login_lock)
        token = auth.authenticate()
        if token is None:
            authenticated = False
            result = {"status": "error", "message": "Could not log in to VFS right now."}
            if auth.blocked:
                result["blocked"] = True
            return result
        browser.sb.sleep(3)
        authenticated = True
        return None

    def safe_authenticate() -> dict | None:
        """authenticate() can raise instead of cleanly returning an error
        dict — e.g. the OTP field never appearing at all raises a plain
        SeleniumBase timeout, not an AuthHandler-recognized failure. Either
        way the browser may now be stuck mid-login, so don't leave it
        around to be wrongly reused as if it were a good session — close
        it and report a normal error instead of crashing the worker loop."""
        try:
            return authenticate()
        except BaseException as e:
            import traceback
            print(f"[slot_check_service] {country}: login failed ({type(e).__name__}): {e}")
            traceback.print_exc()
            close_browser()
            if isinstance(e, Exception):
                return {"status": "error", "message": "Could not log in to VFS right now."}
            raise

    def do_check() -> dict:
        """One check on the current session, re-authenticating exactly once
        if VFS reports the session expired mid-check."""
        nonlocal authenticated
        if not authenticated:
            err = safe_authenticate()
            if err is not None:
                return err
        try:
            outcome = _run_with_watchdog(browser, browser.check_slot_only, check_timeout_seconds)
            return _classify_outcome(outcome)
        except TimeoutError as e:
            # _run_with_watchdog already force-quit the wedged browser —
            # rebuild from scratch next time instead of reusing it dead.
            close_browser()
            return {"status": "error", "message": str(e)}
        except Exception as e:
            if "SESSION_EXPIRED" not in str(e):
                print(f"[slot_check_service] {country}: check failed: {e}")
                return {"status": "error", "message": "Something went wrong while checking."}
            print(f"[slot_check_service] {country}: session expired, re-authenticating...")
            authenticated = False
            err = safe_authenticate()
            if err is not None:
                return err
            try:
                outcome = _run_with_watchdog(browser, browser.check_slot_only, check_timeout_seconds)
                return _classify_outcome(outcome)
            except TimeoutError as e2:
                close_browser()
                return {"status": "error", "message": str(e2)}
            except Exception as e2:
                print(f"[slot_check_service] {country}: check failed after re-auth: {e2}")
                return {"status": "error", "message": "Something went wrong while checking."}

    try:
        while True:
            cmd = command_queue.get()
            if cmd == "stop":
                break
            try:
                result = do_check()
            except Exception as e:
                print(f"[slot_check_service] {country}: unexpected worker error: {e}")
                result = {"status": "error", "message": "Something went wrong while checking."}
            result_queue.put((country, result))
    finally:
        close_browser()
