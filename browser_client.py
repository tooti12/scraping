# browser_client.py
from selenium.common.exceptions import TimeoutException
from seleniumbase import SB


class BrowserClient:
    def __init__(self, country, proxy=True, uc=True, headless=False):
        soax_proxy = "package-282130-sessionid-7mYcBNNXpJOr6TP6-sessionlength-60-bindttl-60-opt-wb:M5QKXRF1mQoB7edV@proxy.soax.com:5000"
        self.country = country
        self.sb = None
        browser_params = {
            "uc": uc,
            "headless": headless,
            "incognito": True,
            "proxy": soax_proxy if proxy else None,
        }
        self._sb_ctx = SB(**browser_params)

    def __enter__(self):
        self.sb = self._sb_ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._sb_ctx.__exit__(exc_type, exc_val, exc_tb)

    def open_login_page(self):
        self.sb.activate_cdp_mode(
            f"https://visa.vfsglobal.com/gbr/en/{self.country}/login"
        )
        # self.sb.uc_open_with_reconnect(
        #     f"https://visa.vfsglobal.com/gbr/en/{self.country}/login",
        #     reconnect_time=3.7
        # )

    def handle_cookies(self):
        try:
            self.sb.wait_for_element("#onetrust-accept-btn-handler", timeout=100)
            self.sb.driver.uc_click("#onetrust-accept-btn-handler")
        except TimeoutException:
            print("No cookie alert appeared within the timeout.")
            return

    def solve_captcha(self):
        print(self.sb.uc_gui_click_captcha())

        # self.sb.assert_element("svg#success-i", timeout=5)

    def check_is_ip_blocked(self):
        try:
            alert_selector = 'div[role="alert"].alert-info'
            # Try to wait for alert for up to 5 seconds
            self.sb.wait_for_element(alert_selector, timeout=5)
            alert_text = self.sb.get_text(alert_selector)
            print("ALERT TEXT:", alert_text)
            return True

        except Exception:
            print("No alert appeared within the timeout.")
            return False

    def check_is_account_blocked(self):
        try:
            alert_selector = "div.alert.alert-info"
            self.sb.wait_for_element(alert_selector, timeout=5)
            alert_text = self.sb.get_text(alert_selector)
            print("ALERT TEXT:", alert_text)
            return True
        except Exception as e:
            print("No alert appeared or error occurred:", e)
            return False

    def get_auth_token(self):
        return self.sb.execute_script(
            """
        return sessionStorage.getItem('JWT');
        """
        )
