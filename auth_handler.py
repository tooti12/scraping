# auth_handler.py
import time


class AuthHandler:
    def __init__(self, country, email, password,browser_client):
        self.country = country
        self.email = email
        self.password = password
        self.browser = browser_client

    def authenticate(self):
            self.browser.open_login_page()
            if not self.browser.check_is_ip_blocked():
                self.browser.handle_cookies()
                self._enter_credentials()
                if not  self.browser.check_is_account_blocked():
                    self._submit_otp()

                    try:
                        self.browser.sb.sleep(3)
                        auth_token = self.browser.get_auth_token()
                        return auth_token
                    except Exception as e:
                        print("Error: Exception", e)
                        self.browser.sb.sleep(200)

    def _enter_credentials(self):
        self.browser.sb.cdp.press_keys("#email", self.email)
        self.browser.sb.cdp.press_keys("#password", self.password)
        self.browser.sb.wait_for_element("button.mat-btn-lg", timeout=50)
        self.browser.solve_captcha()
        self.browser.sb.driver.uc_click("button.mat-btn-lg")


    def _enter_virtual_keyboard(self, key_sequence=None):
        for key in key_sequence:
            self.browser.sb.cdp.gui_click_element(f"button[name='{key}']")
            time.sleep(0.5)




    def _convert_to_key_sequence(self, text):
        # Implement logic to convert text to virtual keyboard sequence
        pass

    def _submit_otp(self):
        from notification_handler import EmailClient
        self.browser.sb.cdp.gui_click_element("#mat-input-5")
        self.browser.sb.sleep(40)
        otp = EmailClient().get_otp()
        self.browser.solve_captcha()
        print("OTP received:", otp)
        if otp is not None:
            self.browser.sb.cdp.press_keys("#mat-input-5", str(otp))
            # self._enter_virtual_keyboard(str(otp))
            self.browser.sb.driver.uc_click("button.mat-btn-lg")
            self.browser.sb.sleep(10)
