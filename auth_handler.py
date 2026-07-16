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
        # Set when authenticate() returns None specifically because VFS's
        # own block page/message was detected, as opposed to some other
        # failure (timeout, missing element, etc.) — slot_check_service.py
        # reads this to tell slot_status_cache.py whether to apply a long
        # cooldown for this country instead of just retrying next cycle.
        self.blocked = False

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
            self.blocked = True
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
                self.blocked = True
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
        self.browser.sb.wait_for_element("button.mat-btn-lg", timeout=80)

    def _submit_login(self):
        # Solves the pre-login Cloudflare captcha (a real OS-level click —
        # safe here because each session has its own isolated virtual
        # display, see browser_client.py) and clicks Login, which is what
        # makes VFS send the OTP email. Called inside login_lock.
        # solve_captcha() handles a visible Cloudflare dialog if one appears.
        # The VFS login page also embeds an *invisible* Turnstile iframe that
        # keeps the submit button disabled until it silently validates browser
        # signals. On Xvfb/datacenter IPs this check is stricter and takes
        # longer — uc_gui_click_captcha() finds and clicks the Turnstile
        # iframe directly, which is what triggers it to run its validation.
        print("[AuthHandler] Solving captcha (post-credential)...")
        self.browser.solve_captcha()

        # Immediately attempt a UC-level click on the Turnstile iframe so it
        # begins its validation without waiting for the polling loop below.
        print("[AuthHandler] Clicking Turnstile iframe (inline, not a dialog)...")
        try:
            self.browser.sb.uc_gui_click_captcha()
            print("[AuthHandler] Turnstile click sent.")
        except Exception as e:
            print(f"[AuthHandler] Turnstile click: {type(e).__name__}: {e}")

        # Poll until the button is enabled (Turnstile validated) or we give
        # up. Retry the Turnstile click at t=5 and t=15 if still blocked.
        print("[AuthHandler] Waiting for login button to become enabled...")
        btn_enabled = False
        for i in range(30):
            try:
                btn = self.browser.sb.driver.find_element("css selector", "button.mat-btn-lg")
                disabled_attr = btn.get_attribute("disabled")
                btn_enabled = btn.is_enabled() and not disabled_attr
                print(f"[AuthHandler] Login button — enabled={btn_enabled} disabled_attr={disabled_attr!r} (t={i}s)")
                if btn_enabled:
                    break
                if i in (5, 15):
                    print(f"[AuthHandler] Still disabled at t={i}s — retrying Turnstile click...")
                    try:
                        self.browser.sb.uc_gui_click_captcha()
                    except Exception as e:
                        print(f"[AuthHandler] Turnstile retry: {type(e).__name__}: {e}")
            except Exception as e:
                print(f"[AuthHandler] Could not read button state: {e}")
            self.browser.sb.sleep(1)

        if not btn_enabled:
            print("[AuthHandler] WARNING: button still disabled after 30s — Turnstile did not complete. Clicking anyway.")

        print("[AuthHandler] Clicking login button...")
        self.browser.sb.driver.uc_click("button.mat-btn-lg")
        self.browser.sb.sleep(5)


    def _enter_virtual_keyboard(self, key_sequence=None):
        for key in key_sequence:
            self.browser.sb.cdp.gui_click_element(f"button[name='{key}']")
            time.sleep(0.5)




    def _convert_to_key_sequence(self, text):
        # Implement logic to convert text to virtual keyboard sequence
        pass

    def _submit_otp(self, email):
        # Captured before waiting for the email so GmailOTPClient can reject
        # any inbox match older than this — otherwise, on a slow/garbled OTP
        # email, it can silently fall back to a *stale* OTP from an earlier
        # login attempt, which VFS rejects in a way that looks just like a
        # block in the logs (see GmailOTPClient.get_otp's docstring).
        requested_at = time.time()
        is_gmail = "@gmail.com" in email.lower()
        if is_gmail:
            from notification_handler import GmailOTPClient
            otp_client = GmailOTPClient()
            print(f"[AuthHandler] Using GmailOTPClient for {email}")
        else:
            from notification_handler import EmailClient
            otp_client = EmailClient()
            print(f"[AuthHandler] Using legacy EmailClient for {email}")

        # Log current page state so we can verify whether the login form
        # submission actually went through before we start waiting for OTP.
        _url = self.browser.sb.get_current_url()
        print(f"[AuthHandler] URL before OTP wait: {_url}")
        try:
            _inputs = self.browser.sb.find_elements('input[id^="mat-input"]')
            _ids = [el.get_attribute("id") for el in _inputs]
            print(f"[AuthHandler] Visible mat-input fields: {_ids}")
            # 2 inputs (email+password) = still on login form → submission failed
            # 1 input (mat-input-3) = OTP step → correct
        except Exception:
            pass
        try:
            _err = self.browser.sb.get_text(".mat-error")
            if _err and _err.strip():
                print(f"[AuthHandler] Error on page: {_err.strip()!r}")
        except Exception:
            pass

        print("[AuthHandler] Waiting for OTP input field to appear (#mat-input-3)...")
        self.browser.sb.wait_for_element("#mat-input-3", timeout=80)
        print("[AuthHandler] OTP field found. Sleeping 20s to let email arrive...")
        self._emit("awaiting_otp")
        self.browser.sb.sleep(20)

        print("[AuthHandler] Fetching OTP from email inbox...")
        otp = otp_client.get_otp(email, since=requested_at) if is_gmail else otp_client.get_otp(email)

        if otp is None:
            print("[AuthHandler] Failed to retrieve OTP — cannot complete login.")
            return

        print("[AuthHandler] OTP received.")  # value intentionally not logged
        self._emit("verifying_otp")
        print("[AuthHandler] Solving captcha before OTP entry...")
        self.browser.solve_captcha()
        print("[AuthHandler] Typing OTP into field...")
        self.browser.sb.type("#mat-input-3", str(otp))

        # The OTP submit button has its own invisible Turnstile — same fix as
        # _submit_login: click the iframe then wait for the button to enable.
        print("[AuthHandler] Clicking Turnstile iframe before OTP submit...")
        try:
            self.browser.sb.uc_gui_click_captcha()
            print("[AuthHandler] Turnstile click sent.")
        except Exception as e:
            print(f"[AuthHandler] Turnstile click: {type(e).__name__}: {e}")

        print("[AuthHandler] Waiting for OTP submit button to become enabled...")
        otp_btn_enabled = False
        for i in range(20):
            try:
                btn = self.browser.sb.driver.find_element("css selector", "button.mat-btn-lg")
                disabled_attr = btn.get_attribute("disabled")
                otp_btn_enabled = btn.is_enabled() and not disabled_attr
                print(f"[AuthHandler] OTP submit button — enabled={otp_btn_enabled} disabled_attr={disabled_attr!r} (t={i}s)")
                if otp_btn_enabled:
                    break
                if i == 8:
                    print("[AuthHandler] Still disabled — retrying Turnstile click...")
                    try:
                        self.browser.sb.uc_gui_click_captcha()
                    except Exception as e:
                        print(f"[AuthHandler] Turnstile retry: {type(e).__name__}: {e}")
            except Exception as e:
                print(f"[AuthHandler] Could not read OTP button state: {e}")
            self.browser.sb.sleep(1)

        if not otp_btn_enabled:
            print("[AuthHandler] WARNING: OTP submit button still disabled after 20s — clicking anyway.")

        print("[AuthHandler] Clicking submit button...")
        self.browser.sb.driver.uc_click("button.mat-btn-lg")
        print("[AuthHandler] OTP submitted. Waiting 10s for session to establish...")
        self.browser.sb.sleep(10)
        print("[AuthHandler] OTP flow complete.")
