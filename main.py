# main.py
import time

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

    def start_monitoring(self):
        with BrowserClient(self.country,proxy=False) as browser:
            self.auth_token = AuthHandler(self.country, self.email, self.password,browser).authenticate()
            if self.auth_token is None:
                print("Auth token is none. Exiting.")
                return
            browser.sb.sleep(5)
            browser.sb.driver.uc_click("button.mat-btn-lg")
            browser.sb.sleep(5)
            while True:
                try:
                    response = browser.call_check_slot(jwt_token=self.auth_token,login_user=self.email)
                    if response['status']==200:
                        print(response)
                        if slots:=(response['body']["earliestSlotLists"]):
                            self._handle_available_slot("Amsterdam", str(slots))
                        else:
                            time.sleep(220)
                    else:
                        browser.switch_tabs()
                except Exception as e:
                    print(f"Monitoring error: {e}")
                    return



    def _handle_available_slot(self, city, date_str):
        message = f"{self.country} {city} earliestDate {date_str}"
        print(message)
        self.notifier.send_sms(f"VFS Appointments {message} available")

if __name__ == "__main__":
    scraper = VfsScraper("nld", "vfs@thesemantics.co", "P@ssword123")
    scraper.start_monitoring()





