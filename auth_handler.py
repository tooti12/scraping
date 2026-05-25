# auth_handler.py
import time


class AuthHandler:
    def __init__(self, country, email, password,browser_client):
        self.country = country
        self.email = email
        self.password = password
        self.browser = browser_client

    def authenticate(self):
        print(f"[AuthHandler] Opening VFS login page for country={self.country} ...")
        self.browser.open_login_page()
        print("[AuthHandler] Login page loaded.")

        if self.browser.check_is_ip_blocked():
            print("[AuthHandler] IP is blocked — aborting authentication.")
            return None

        print("[AuthHandler] IP not blocked. Handling cookie banner...")
        self.browser.handle_cookies()
        print("[AuthHandler] Entering credentials...")
        self._enter_credentials()
        print("[AuthHandler] Credentials submitted. Checking for IP block after login...")

        if self.browser.check_is_ip_blocked():
            print("[AuthHandler] IP blocked after credential entry — aborting.")
            return None

        print("[AuthHandler] No IP block. Waiting for OTP prompt and fetching OTP from email...")
        self._submit_otp(email=self.email)
        try:
            self.browser.sb.sleep(3)
            print("[AuthHandler] Reading JWT from sessionStorage...")
            auth_token = self.browser.get_auth_token()
            if auth_token:
                print(f"[AuthHandler] JWT obtained successfully (length={len(auth_token)}).")
            else:
                print("[AuthHandler] JWT not found in sessionStorage after OTP submission.")
            return auth_token
        except Exception as e:
            print(f"[AuthHandler] Exception reading JWT: {e}")
            self.browser.sb.sleep(200)
            return None

    def _enter_credentials(self):
        self.browser.sb.sleep(2)
        print(f"[AuthHandler] Typing email: {self.email}")
        try:
            self.browser.sb.wait_for_element("#email", timeout=10)
            self.browser.sb.type("#email", self.email)
            print("[AuthHandler] Email entered.")
            self.browser.sb.wait_for_element("#password", timeout=5)
            self.browser.sb.type("#password", self.password)
            print("[AuthHandler] Password entered.")
        except Exception as e:
            print(f"[AuthHandler] Primary selectors failed ({e}), trying name-based fallback...")
            self.browser.sb.type('input[name="email"]', self.email)
            self.browser.sb.type('input[name="password"]', self.password)
        print("[AuthHandler] Waiting for login button...")
        self.browser.sb.wait_for_element("button.mat-btn-lg", timeout=50)
        print("[AuthHandler] Solving captcha (post-credential)...")
        self.browser.solve_captcha()
        print("[AuthHandler] Clicking login button...")
        self.browser.sb.driver.uc_click("button.mat-btn-lg")


    def _enter_virtual_keyboard(self, key_sequence=None):
        for key in key_sequence:
            self.browser.sb.cdp.gui_click_element(f"button[name='{key}']")
            time.sleep(0.5)




    def _convert_to_key_sequence(self, text):
        # Implement logic to convert text to virtual keyboard sequence
        pass

    def _submit_otp(self, email):
        if "@gmail.com" in email.lower():
            from notification_handler import GmailOTPClient
            otp_client = GmailOTPClient()
            print(f"[AuthHandler] Using GmailOTPClient for {email}")
        else:
            from notification_handler import EmailClient
            otp_client = EmailClient()
            print(f"[AuthHandler] Using legacy EmailClient for {email}")

        print("[AuthHandler] Waiting for OTP input field to appear (#mat-input-3)...")
        self.browser.sb.wait_for_element("#mat-input-3", timeout=50)
        print("[AuthHandler] OTP field found. Sleeping 20s to let email arrive...")
        self.browser.sb.sleep(20)

        print("[AuthHandler] Fetching OTP from email inbox...")
        otp = otp_client.get_otp(email)

        if otp is None:
            print("[AuthHandler] Failed to retrieve OTP — cannot complete login.")
            return

        print(f"[AuthHandler] OTP received: {otp}")
        print("[AuthHandler] Solving captcha before OTP entry...")
        self.browser.solve_captcha()
        print("[AuthHandler] Typing OTP into field...")
        self.browser.sb.type("#mat-input-3", str(otp))
        print("[AuthHandler] Clicking submit button...")
        self.browser.sb.driver.uc_click("button.mat-btn-lg")
        print("[AuthHandler] OTP submitted. Waiting 10s for session to establish...")
        self.browser.sb.sleep(10)
        print("[AuthHandler] OTP flow complete.")
