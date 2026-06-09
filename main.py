# main.py
import time
import itertools
import json
import os
import threading
from datetime import datetime
from typing import Dict, Any

from api_client import APIClient
from auth_handler import AuthHandler
from browser_client import BrowserClient
from config import COUNTRY_CONFIG
from notification_handler import SMSNotifier


def _keep_screen_awake():
    """Move the mouse by 1 px every 30 s to prevent the screen from sleeping."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        while True:
            try:
                x, y = pyautogui.position()
                pyautogui.moveTo(x + 1, y + 1, duration=0.1)
                pyautogui.moveTo(x, y, duration=0.1)
            except Exception:
                pass
            time.sleep(30)
    except ImportError:
        # pyautogui not available — fall back to xdotool if present
        import subprocess
        while True:
            try:
                subprocess.run(
                    ["xdotool", "mousemove_relative", "--", "1", "0"],
                    capture_output=True, check=False
                )
                time.sleep(0.3)
                subprocess.run(
                    ["xdotool", "mousemove_relative", "--", "-1", "0"],
                    capture_output=True, check=False
                )
            except Exception:
                pass
            time.sleep(30)


class VfsScraper:
    def __init__(self, country, email, password, persist_session=True):
        self.country = country
        self.email = email
        self.password = password
        self.config = COUNTRY_CONFIG[country]
        self.notifier = SMSNotifier()
        self.auth_token = None
        self.start_time = time.time()
        self.max_runtime = 30 * 60  # seconds
        self.persist_session = persist_session
        self.max_retries = 3
        self.retry_count = 0
        self.session_valid = True
        
        # Create logs directory
        os.makedirs("logs", exist_ok=True)
        self.log_file = f"logs/{country}_appointments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
    def log_appointment_data(self, data: Dict[str, Any]):
        """Log appointment data to file"""
        try:
            with open(self.log_file, "a") as f:
                log_entry = {
                    "timestamp": datetime.now().isoformat(),
                    "email": self.email,
                    "country": self.country,
                    "data": data
                }
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            print(f"Error logging appointment data: {e}")

    def start_monitoring(self):
        with BrowserClient(self.country, proxy=True) as browser:
            print(f"[VfsScraper] Browser launched. Navigating to VFS login page...")

            # Always open the login page first — this is what loads the VFS URL.
            # Checking get_auth_token() before any navigation returns nothing useful.
            auth = AuthHandler(self.country, self.email, self.password, browser)
            self.auth_token = auth.authenticate()

            if self.auth_token is None:
                print("[VfsScraper] Authentication failed — no JWT obtained. Exiting.")
                return

            print(f"[VfsScraper] Authenticated. JWT length={len(self.auth_token)}.")
            browser.sb.sleep(3)

            print(f"[VfsScraper] Starting slot monitoring for {self.country} / {self.email}")
            print(f"[VfsScraper] Max runtime: {self.max_runtime // 60} minutes")

            while True:
                if time.time() - self.start_time > self.max_runtime:
                    print("[VfsScraper] 30-minute limit reached. Exiting.")
                    return

                try:
                    # Test every (centre × appt_category × sub_category) combination
                    # with a 5-second pause between each.
                    results = browser.check_all_combinations(delay_secs=5)

                    self.log_appointment_data({
                        "type": "combo_scan",
                        "results": results,
                    })

                    slots_found = [r for r in results if r["result"] == "slots_available"]
                    errors      = [r for r in results if r["result"] == "error"]

                    if slots_found:
                        for r in slots_found:
                            msg = (
                                f"VFS {self.country.upper()} SLOT AVAILABLE: "
                                f"{r['centre']['text']} / "
                                f"{r['appt_cat']['text']} / "
                                f"{r['sub_cat']['text']}"
                            )
                            print(f"[VfsScraper] {msg}")
                            self.notifier.send_sms(msg)
                        # Pause for 30 min so user can act before re-scanning
                        print("[VfsScraper] Waiting 30 min before next full scan.")
                        time.sleep(1800)

                    elif errors and len(errors) == len(results):
                        # Every single combo errored — likely session/page problem
                        print("[VfsScraper] All combos returned error — retrying in 60 s.")
                        self.retry_count += 1
                        if self.retry_count >= self.max_retries:
                            print("[VfsScraper] Max retries reached. Exiting.")
                            return
                        time.sleep(60)

                    else:
                        print(f"[VfsScraper] Scan done — no slots. "
                              f"Waiting 150 s before next full scan.")
                        time.sleep(150)

                except RuntimeError as e:
                    if "SESSION_EXPIRED" in str(e):
                        print(f"[VfsScraper] Session expired mid-scan — exiting to re-authenticate. ({e})")
                        return
                    print(f"[VfsScraper] Monitoring error: {e}")
                    self.log_appointment_data({"type": "error", "error": str(e)})
                    self.retry_count += 1
                    if self.retry_count >= self.max_retries:
                        print("[VfsScraper] Max retries reached due to errors. Exiting.")
                        return
                    time.sleep(60)
                except Exception as e:
                    print(f"[VfsScraper] Monitoring error: {e}")
                    self.log_appointment_data({"type": "error", "error": str(e)})
                    self.retry_count += 1
                    if self.retry_count >= self.max_retries:
                        print("[VfsScraper] Max retries reached due to errors. Exiting.")
                        return
                    time.sleep(60)

    def _handle_available_slot(self, city=None, date_str=None, waitlist=False):
        if waitlist:
            self.notifier.send_sms("VFS Appointments waitlist Open")
        else:
            self.notifier.send_sms(
                f"VFS Appointments {self.country} {city} earliestDate {date_str} available"
            )


if __name__ == "__main__":
    # DNK (Denmark) account — OTP arrives at Gmail inbox
    accounts = [
        ("dnk", "umar.jwork@gmail.com", "P@ssword123"),
    ]

    # BGR (Bulgaria) — commented out, switch back by swapping the accounts list above
    # accounts = [
    #     ("bgr", "umar.jwork@gmail.com", "P@ssword123"),
    # ]

    # Keep screen awake during long browser sessions
    mouse_thread = threading.Thread(target=_keep_screen_awake, daemon=True)
    mouse_thread.start()
    print("Screen-awake thread started.")
    print("VFS Appointment Scraper - GBR -> DNK (Denmark)")
    print("=" * 50)

    for country, email, password in itertools.cycle(accounts):
        print(f"\n=== Starting session for {email} ({country.upper()}) ===")
        scraper = VfsScraper(country, email, password)
        scraper.start_monitoring()
        print(f"=== Finished session for {email} — pausing 60 s ===")
        time.sleep(60)
