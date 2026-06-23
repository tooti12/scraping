# slot_check_service.py
"""Booking-free VFS slot check used by the public slot-checker
homepage (dashboard/app.py's /api/check/<country> route).

Runs headed (not headless) — Cloudflare's challenge is solved via
SeleniumBase's uc_gui_click_captcha(), which drives a real OS-level mouse
click and does not work in a headless browser.

Deliberately separate from main.py's continuous monitor and from
booking_flow.py's human-in-the-loop booking pipeline — this module only
logs in, checks London-centre/Tourism-category availability for one
country, and returns a plain result dict. It never books anything.
"""
from auth_handler import AuthHandler
from browser_client import BrowserClient
from config import SHARED_VFS_ACCOUNT


def check_country_slot(country: str) -> dict:
    """Run one slot check for `country`. Always returns a dict
    with a "status" key: "slots_available" | "no_slots" | "error"."""
    email = SHARED_VFS_ACCOUNT["email"]
    password = SHARED_VFS_ACCOUNT["password"]

    try:
        with BrowserClient(country, proxy=True, headless=False) as browser:
            auth = AuthHandler(country, email, password, browser)
            token = auth.authenticate()
            if token is None:
                return {"status": "error", "message": "Could not log in to VFS right now."}

            browser.sb.sleep(3)
            outcome = browser.check_slot_only()
    except Exception as e:
        print(f"[slot_check_service] Check failed for {country}: {e}")
        return {"status": "error", "message": "Something went wrong while checking. Please try again."}

    result = outcome.get("result")
    if result == "slots_available":
        return {
            "status": "slots_available",
            "centre": outcome["centre"]["text"],
            "appt_cat": outcome["appt_cat"]["text"],
            "sub_cat": outcome["sub_cat"]["text"],
            "slot_details": outcome.get("slot_details", ""),
        }
    if result == "no_slots":
        return {"status": "no_slots", "slot_details": outcome.get("slot_details", "")}

    reason = outcome.get("reason", "unexpected_error")
    return {"status": "error", "message": f"Could not complete the check ({reason})."}
