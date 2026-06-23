# auth_handler.py
import contextlib
import time


class AuthHandler:
    def __init__(self, country, email, password, browser_client, on_status=None, login_lock=None):
        self.country = country
        self.email = email
        self.password = password
        self.browser = browser_client
        # Optional callback(event_name: str) used by slot_check_service.py to
        # mirror real login/OTP progress to the public checker's frontend.
        # None for every other caller (main.py's monitor) — zero behavior
        # change there.
        self.on_status = on_status
        # Optional lock (a multiprocessing.Lock in practice — see
        # slot_status_cache.py, which runs each country in its own process)
        # shared across every concurrently-running country session. All
        # countries share one Gmail inbox for OTPs, so the window from
        # "click login" (which triggers VFS to send the OTP email) through
        # "OTP verified" must run for one country at a time — otherwise two
        # sessions' OTP emails could arrive close together and get
        # attributed to the wrong session. None for every other caller,
        # which only ever runs one session at a time anyway.
        self.login_lock = login_lock

    def _emit(self, event):
        if self.on_status:
            try:
                self.on_status(event)
            except Exception:
                pass

    def authenticate(self):
        print(f"[AuthHandler] Opening VFS login page for country={self.country} ...")
        self._emit("logging_in")
        self.browser.open_login_page()
        print("[AuthHandler] Login page loaded.")

        if self.browser.check_is_ip_blocked():
            print("[AuthHandler] IP is blocked — aborting authentication.")
            self.browser._log_state("Authentication aborted — IP blocked")
            return None

        print("[AuthHandler] IP not blocked. Handling cookie banner...")
        self.browser.handle_cookies()
        print("[AuthHandler] Typing credentials...")
        self._type_credentials()

        # Everything from here through OTP verification touches the one
        # shared Gmail inbox (clicking login is what makes VFS send the
        # email) — serialized across concurrent country sessions so two
        # sessions' OTP emails can never be in flight at the same time.
        # Typing credentials above doesn't touch Gmail, so it stays outside
        # the lock and can run fully in parallel with other sessions.
        lock = self.login_lock if self.login_lock is not None else contextlib.nullcontext()
        with lock:
            print("[AuthHandler] Submitting login...")
            self._submit_login()
            print("[AuthHandler] Credentials submitted. Checking for IP block after login...")
            self.browser._log_state("After submitting credentials")

            if self.browser.check_is_ip_blocked():
                print("[AuthHandler] IP blocked after credential entry — aborting.")
                self.browser._log_state("Authentication aborted — IP blocked after credentials")
                return None

            print("[AuthHandler] No IP block. Waiting for OTP prompt and fetching OTP from email...")
            self._submit_otp(email=self.email)
            self.browser._log_state("After OTP submission")
        try:
            self.browser.sb.sleep(3)
            print("[AuthHandler] Reading JWT from sessionStorage...")
            auth_token = self.browser.get_auth_token()
            if auth_token:
                print(f"[AuthHandler] JWT obtained successfully (length={len(auth_token)}).")
                self._emit("session_ready")
            else:
                print("[AuthHandler] JWT not found in sessionStorage after OTP submission.")
            return auth_token
        except Exception as e:
            print(f"[AuthHandler] Exception reading JWT: {e}")
            self.browser.sb.sleep(200)
            return None

    def _type_credentials(self):
        # Pure text input, no shared resource and no OS-level click — safe
        # to run for every country session in parallel, outside login_lock.
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

    def _submit_login(self):
        # Solves the pre-login Cloudflare captcha (a real OS-level click —
        # safe here because each session has its own isolated virtual
        # display, see browser_client.py) and clicks Login, which is what
        # makes VFS send the OTP email. Called inside login_lock.
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
        self._emit("awaiting_otp")
        self.browser.sb.sleep(20)

        print("[AuthHandler] Fetching OTP from email inbox...")
        otp = otp_client.get_otp(email)

        if otp is None:
            print("[AuthHandler] Failed to retrieve OTP — cannot complete login.")
            return

        print(f"[AuthHandler] OTP received: {otp}")
        self._emit("verifying_otp")
        print("[AuthHandler] Solving captcha before OTP entry...")
        self.browser.solve_captcha()
        print("[AuthHandler] Typing OTP into field...")
        self.browser.sb.type("#mat-input-3", str(otp))
        print("[AuthHandler] Clicking submit button...")
        self.browser.sb.driver.uc_click("button.mat-btn-lg")
        print("[AuthHandler] OTP submitted. Waiting 10s for session to establish...")
        self.browser.sb.sleep(10)
        print("[AuthHandler] OTP flow complete.")
