# main.py
import time
import itertools

from api_client import APIClient
from auth_handler import AuthHandler
from browser_client import BrowserClient
from config import COUNTRY_CONFIG
from notification_handler import SMSNotifier


class VfsScraper:
    def __init__(self, country, email, password):
        self.country = country
        self.email = email
        self.password = password
        self.config = COUNTRY_CONFIG[country]
        self.notifier = SMSNotifier()
        self.auth_token = None
        self.start_time = time.time()
        self.max_runtime = 30 * 60  # seconds

    def start_monitoring(self):
        with BrowserClient(self.country, proxy=False) as browser:
            self.auth_token = AuthHandler(
                self.country, self.email, self.password, browser
            ).authenticate()
            if self.auth_token is None:
                print("Auth token is none. Exiting.")
                return
            browser.sb.sleep(5)
            browser.sb.driver.uc_click("button.mat-btn-lg")
            browser.sb.sleep(5)
            while True:

                if time.time() - self.start_time > self.max_runtime:
                    print("Reached 35-minute limit. Exiting...")
                    return
                try:
                    response = browser.call_check_slot(
                        jwt_token=self.auth_token, login_user=self.email
                    )
                    if response["status"] == 200:
                        print("successfull")
                        try:
                            if (
                                "waitlist"
                                in response["body"]["error"]["description"].lower()
                            ):
                                self._handle_available_slot(
                                    "Netherlands", str(slots), waitlist=True
                                )
                                time.sleep(1800)
                        except:
                            pass
                        if slots := (response["body"]["earliestSlotLists"]):
                            self._handle_available_slot("Amsterdam", str(slots))
                            time.sleep(1800)  # 30 minutes
                        else:
                            time.sleep(120)
                    else:
                        browser.switch_tabs()
                except Exception as e:
                    print(f"Monitoring error: {e}")
                    return

    def _handle_available_slot(self, city=None, date_str=None, waitlist=False):
        if waitlist:
            self.notifier.send_sms(f"VFS Appointments waitlist Open")
        else:
            message = f"{self.country} {city} earliestDate {date_str}"
            self.notifier.send_sms(f"VFS Appointments {message} available")


if __name__ == "__main__":
    # rotate through different accounts
    accounts = [
        ("nld", "vfs2@thesemantics.co", "P@ssword123"),
        ("nld", "vfs3@thesemantics.co", "P@ssword123"),
        ("nld", "vfs@thesemantics.co", "P@ssword123"),
    ]

    for country, email, password in itertools.cycle(accounts):
        print(f"\n=== Starting session for {email} ===")

        scraper = VfsScraper(country, email, password)
        scraper.start_monitoring()

        print(f"=== Finished 30 min session for {email} ===")
        time.sleep(200)  # short pause before restarting with next account
