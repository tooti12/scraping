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
        with BrowserClient(self.country) as browser:
            self.auth_token = AuthHandler(self.country, self.email, self.password,browser).authenticate()
            if self.auth_token is None:
                print("Auth token is none. Exiting.")
            return
        with BrowserClient(self.country,proxy=False,uc=False,headless=True) as browser:
            api_client = APIClient(self.auth_token,self.config,self.email,browser)
            while True:
                try:
                    response = api_client.check_slot_availability()
                    print("Response:", response)
                    time.sleep(280)
                except Exception as e:
                    print(f"Monitoring error: {e}")
                    browser.sb.sleep(60)
                    return



    def _handle_available_slot(self, city, date_str):
        message = f"{self.country} {city} earliestDate {date_str}"
        print(message)
        self.notifier.send_sms(f"VFS Appointments {message} available")

if __name__ == "__main__":
    scraper = VfsScraper("nld", "umar.javed@thesemantics.co", "P@ssword123")
    scraper.start_monitoring()
