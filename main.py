# main.py
import json
import os
import threading
import time
from datetime import datetime
from typing import Any

from auth_handler import AuthHandler
from browser_client import BrowserClient
from notification_handler import SMSNotifier

# ──────────────────────────────────────────────
# Countries to scan — same credentials, different VFS destinations.
# Add or remove entries here to change which countries are monitored.
# ──────────────────────────────────────────────
ACCOUNTS = [
    ("dnk", "umar.jwork@gmail.com", "P@ssword123"), #Denmark
    ("bgr", "umar.jwork@gmail.com", "P@ssword123"), #Bulgaria
    ("svn", "umar.jwork@gmail.com", "P@ssword123"), #Slovenia
    ("che", "umar.jwork@gmail.com", "P@ssword123"), #Switzerland

]

# How long to wait between countries in the same cycle (seconds)
BETWEEN_COUNTRY_WAIT = 90

# How long to wait after all countries are done before the next full cycle (seconds)
BETWEEN_CYCLE_WAIT = 180

# Seconds between individual combo checks within a scan — kept generous to
# avoid VFS rate-limiting / 401-session-expired errors from too-frequent requests
COMBO_DELAY = 20

# How long to wait before retrying a country after a session-expiry (401) error
SESSION_RETRY_WAIT = 60

# Max consecutive session-expiry retries for a single country before giving up
# on it for this cycle and moving on to the next country
MAX_SESSION_RETRIES = 5


# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────

def _human_wait(seconds: int, reason: str = "") -> None:
    """Sleep for `seconds` while jiggling the mouse every 15 s."""
    label = f" — {reason}" if reason else ""
    mins, secs = divmod(seconds, 60)
    print(f"[VfsScraper] Waiting {mins}m {secs:02d}s{label}...")
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                x, y = pyautogui.position()
                pyautogui.moveTo(x + 2, y + 1, duration=0.15)
                pyautogui.moveTo(x,     y,     duration=0.15)
            except Exception:
                pass
            time.sleep(min(15, remaining))
    except ImportError:
        time.sleep(seconds)


def _keep_screen_awake() -> None:
    """Background thread: nudge the mouse every 30 s to prevent sleep."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        while True:
            try:
                x, y = pyautogui.position()
                pyautogui.moveTo(x + 1, y + 1, duration=0.1)
                pyautogui.moveTo(x,     y,     duration=0.1)
            except Exception:
                pass
            time.sleep(30)
    except ImportError:
        pass


# ──────────────────────────────────────────────
# Main scraper
# ──────────────────────────────────────────────

class VfsScraper:
    def __init__(self, accounts: list[tuple[str, str, str]]) -> None:
        self.accounts = accounts          # [(country, email, password), ...]
        self.notifier = SMSNotifier()
        os.makedirs("logs", exist_ok=True)
        self._log_file = (
            f"logs/multi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        )

    # ── logging ──────────────────────────────

    def _log(self, data: dict[str, Any]) -> None:
        try:
            with open(self._log_file, "a") as f:
                f.write(json.dumps({"ts": datetime.now().isoformat(), **data}) + "\n")
        except Exception as e:
            print(f"[VfsScraper] Log error: {e}")

    # ── per-country scan ─────────────────────

    def _scan_country(
        self,
        browser: BrowserClient,
        country: str,
        email: str,
        password: str,
    ) -> list[dict] | None:
        """
        Log in to `country` and return all combo results tagged with country.

        Returns `None` if the VFS session expired mid-scan (401 / redirected to
        login) — the caller should retry this same country with a fresh session.
        Returns `[]` for auth failures or other non-recoverable scan errors.
        """
        browser.switch_country(country)
        browser.login_user = email

        auth = AuthHandler(country, email, password, browser)
        token = auth.authenticate()
        if token is None:
            print(f"[VfsScraper] Auth failed for {country.upper()} — skipping.")
            return []

        print(f"[VfsScraper] Authenticated for {country.upper()}. JWT length={len(token)}.")
        browser.sb.sleep(3)

        try:
            results = browser.check_all_combinations(delay_secs=COMBO_DELAY)
        except RuntimeError as e:
            msg = str(e)
            if "SESSION_EXPIRED" in msg:
                print(f"[VfsScraper] Session expired mid-scan for {country.upper()}.")
                return None
            print(f"[VfsScraper] Scan error for {country.upper()}: {e}")
            return []

        self._log({"country": country, "type": "scan", "results": results})

        # Tag each result with its country and send SMS for available slots.
        for r in results:
            r["country"] = country

        slots = [r for r in results if r["result"] == "slots_available"]
        for r in slots:
            details = r.get("slot_details", "")
            msg = (
                f"VFS {country.upper()} SLOT AVAILABLE: "
                f"{r['centre']['text']} / "
                f"{r['appt_cat']['text']} / "
                f"{r['sub_cat']['text']}"
            )
            if details:
                msg += f" | {details}"
            print(f"[VfsScraper] {msg}")
            self.notifier.send_sms(msg)

        for r in [r for r in results if r["result"] == "waitlist_joined"]:
            msg = (
                f"VFS {country.upper()} WAITLIST JOINED: "
                f"{r['centre']['text']} / {r['appt_cat']['text']}"
            )
            print(f"[VfsScraper] {msg}")
            self.notifier.send_sms(msg)

        return results

    # ── summary table ─────────────────────────

    @staticmethod
    def _print_summary(all_results: list[dict]) -> None:
        all_slots = [r for r in all_results if r["result"] == "slots_available"]
        waitlisted = [
            r for r in all_results
            if r["result"] in ("waitlist_joined", "waitlist_failed")
        ]

        w_country  = 8
        w_centre   = 44
        w_cat      = 18
        w_date     = 28
        total_w    = w_country + w_centre + w_cat + w_date + 6  # separators

        bar = "=" * total_w
        print(f"\n{bar}")
        print(f"  CONSOLIDATED RESULTS — {len(all_slots)} slot(s) across all countries")
        print(bar)

        if not all_slots:
            print("  No slots available across any country.")
        else:
            hdr = (
                f"  {'Country':<{w_country}} "
                f"{'Centre':<{w_centre}} "
                f"{'Sub-Category':<{w_cat}} "
                f"{'Earliest Date':<{w_date}}"
            )
            print(hdr)
            print("  " + "-" * (total_w - 2))

            for r in all_slots:
                country_str = r.get("country", "???").upper()
                centre      = r["centre"]["text"][:w_centre]
                sub_cat     = r["sub_cat"]["text"][:w_cat]
                details     = r.get("slot_details", "")
                # Extract just the date portion after "is :" if present
                if "is :" in details:
                    date_str = details.split("is :")[-1].strip()
                else:
                    date_str = details[:w_date] if details else "—"

                print(
                    f"  {country_str:<{w_country}} "
                    f"{centre:<{w_centre}} "
                    f"{sub_cat:<{w_cat}} "
                    f"{date_str:<{w_date}}"
                )

        if waitlisted:
            print(f"\n  Waitlist registrations ({len(waitlisted)}):")
            for r in waitlisted:
                status = "JOINED" if r["result"] == "waitlist_joined" else "FAILED"
                country_str = r.get("country", "???").upper()
                print(
                    f"    {country_str}: {r['centre']['text']} / "
                    f"{r['appt_cat']['text']} — {status}"
                )

        print(f"{bar}\n")

    # ── main loop ─────────────────────────────

    def start_monitoring(self) -> None:
        cycle = 0

        while True:
            cycle += 1
            n = len(self.accounts)
            print(f"\n[VfsScraper] {'=' * 50}")
            print(f"[VfsScraper]  CYCLE {cycle}  —  {n} country/countries")
            print(f"[VfsScraper] {'=' * 50}")

            all_results: list[dict] = []

            # Single browser session for the whole cycle
            with BrowserClient(self.accounts[0][0], proxy=True) as browser:
                for idx, (country, email, password) in enumerate(self.accounts):
                    print(
                        f"\n[VfsScraper] ── Country {idx + 1}/{n}: "
                        f"{country.upper()} ──"
                    )

                    # Retry the SAME country (fresh session) on a 401/session
                    # expiry, instead of skipping it or restarting the cycle.
                    retries = 0
                    while True:
                        results = self._scan_country(browser, country, email, password)
                        if results is not None:
                            break
                        retries += 1
                        if retries > MAX_SESSION_RETRIES:
                            print(
                                f"[VfsScraper] {country.upper()} hit session "
                                f"expiry {retries - 1}x in a row — giving up "
                                "on it for this cycle."
                            )
                            results = []
                            break
                        print(
                            f"[VfsScraper] Restarting {country.upper()} with a "
                            f"fresh session (attempt {retries + 1})..."
                        )
                        _human_wait(
                            SESSION_RETRY_WAIT,
                            f"before re-authenticating {country.upper()}",
                        )

                    all_results.extend(results)

                    # Wait between countries (skip after the last one)
                    if idx < n - 1:
                        next_country = self.accounts[idx + 1][0].upper()
                        _human_wait(
                            BETWEEN_COUNTRY_WAIT,
                            f"before switching to {next_country}",
                        )

            # Consolidated summary across all countries
            self._print_summary(all_results)

            # Wait before next full cycle
            _human_wait(BETWEEN_CYCLE_WAIT, "before next full cycle")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=_keep_screen_awake, daemon=True).start()
    print("Screen-awake thread started.")
    print("VFS Appointment Scraper — multi-country")
    print("=" * 50)
    print(f"Countries: {', '.join(c.upper() for c, _, _ in ACCOUNTS)}")
    print("=" * 50)

    VfsScraper(ACCOUNTS).start_monitoring()
