# otp_step.py
"""Drives the booking flow's own OTP panel (part of the 'your-details'
step, after the applicant form is saved) - distinct from the login OTP in
auth_handler.py, but reusing the same IMAP fetch via
notification_handler.EmailClient instead of duplicating that polling logic.

VFS caps OTP generation at 3 attempts per its own UI copy ("You have left 3
attempts of generating new OTP. After crossing limit you will be logged
off.") - this stops at MAX_OTP_ATTEMPTS rather than relying on VFS to log us
out first.
"""
import json
import time

from notification_handler import EmailClient

MAX_OTP_ATTEMPTS = 3


class OtpStep:
    def __init__(self, browser_client, email, bridge=None):
        self.browser = browser_client
        self.email = email
        self.bridge = bridge

    def run(self):
        sb = self.browser.sb
        self._click_button("Generate OTP")

        for attempt in range(1, MAX_OTP_ATTEMPTS + 1):
            self._push_status("otp_requested", {"attempt": attempt})

            sb.wait_for_element('input[placeholder="OTP"]', timeout=30)
            otp = EmailClient().get_otp(email=self.email)

            if not otp:
                self._push_status("otp_fetch_failed", {"attempt": attempt})
                if attempt < MAX_OTP_ATTEMPTS:
                    self._click_span("Regenerate one-time password")
                    continue
                raise RuntimeError("Failed to fetch booking OTP from email after max attempts")

            sb.cdp.press_keys('input[placeholder="OTP"]', str(otp))
            self._wait_until_button_enabled("Verify", timeout=10)
            self._click_button("Verify")

            if self._wait_for_text("OTP verification successful", timeout=15):
                self._push_status("otp_verified")
                self._wait_until_button_enabled("Continue", timeout=10)
                self._click_button("Continue")
                return True

            self._push_status("otp_verification_failed", {"attempt": attempt})
            if attempt < MAX_OTP_ATTEMPTS:
                self._click_span("Regenerate one-time password")
                continue
            raise RuntimeError("OTP verification failed after max attempts")

        return False

    # --- helpers ---

    def _click_button(self, text):
        self.browser.sb.uc_click(f'button:contains("{text}")')

    def _click_span(self, text):
        self.browser.sb.uc_click(f'span:contains("{text}")')

    def _wait_for_text(self, text, timeout):
        try:
            self.browser.sb.wait_for_text(text, timeout=timeout)
            return True
        except Exception:
            return False

    def _wait_until_button_enabled(self, text, timeout):
        """Angular toggles the `disabled` attribute on these buttons rather
        than removing them, so a plain wait_for_element doesn't tell us
        whether it's actually clickable yet - poll via JS instead."""
        sb = self.browser.sb
        deadline = time.time() + timeout
        script = f"""
            const target = {json.dumps(text)};
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => b.textContent.trim().includes(target));
            return btn ? btn.disabled : null;
        """
        while time.time() < deadline:
            disabled = sb.execute_script(script)
            if disabled is False:
                return
            time.sleep(0.5)
        raise TimeoutError(f"Button '{text}' did not become enabled within {timeout}s")

    def _push_status(self, event, payload=None):
        if self.bridge:
            self.bridge.push_status(event, payload)
