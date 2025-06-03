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
            browser.sb.driver.uc_click("button.mat-btn-lg")
            browser.sb.sleep(5)
            while True:
                try:
                    
                    browser.sb.click("#mat-select-0",scroll=True)
                    browser.sb.sleep(2)
                    browser.sb.cdp.gui_click_element("#NAKH")
                    browser.sb.sleep(5)
                    browser.sb.cdp.gui_click_element("#mat-select-0")
                    browser.sb.sleep(5)
                    browser.sb.cdp.gui_click_element("#NAKN")
                    browser.sb.sleep(5)
                    browser.sb.click("#mat-select-2", scroll=True)
                    browser.sb.sleep(5)
                    browser.sb.cdp.gui_click_element("#TA")
                    try:
                        alert_text = browser.sb.get_text("div.alert-info")
                        print("Alert text:", alert_text)
                        if "no appointment slots" in alert_text.lower():
                            print("Slots are not available.")
                    except Exception as e:
                        print("No alert found or error occurred:", e)
                    time.sleep(220)
                except Exception as e:
                    print(f"Monitoring error: {e}")
                    browser.sb.sleep(60)
                    return



    def _handle_available_slot(self, city, date_str):
        message = f"{self.country} {city} earliestDate {date_str}"
        print(message)
        self.notifier.send_sms(f"VFS Appointments {message} available")

if __name__ == "__main__":
    scraper = VfsScraper("nld", "vfs@thesemantics.co", "P@ssword123")
    scraper.start_monitoring()
