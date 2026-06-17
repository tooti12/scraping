# captcha_handler.py
"""Consolidated Cloudflare Turnstile modal handling.

The VFS booking flow shows the same `app-cloudflare-dialog` modal at several
points (after saving the applicant form, on landing on book-appointment, and
after continuing from the review page). This wraps detection + solve + retry
in one place instead of duplicating ad-hoc solve_captcha() calls per step.
"""


class CaptchaHandler:
    def __init__(self, browser_client, max_retries=3):
        self.browser = browser_client
        self.max_retries = max_retries

    def is_modal_present(self, timeout=3):
        try:
            self.browser.sb.wait_for_element("app-cloudflare-dialog", timeout=timeout)
            return True
        except Exception:
            return False

    def solve_if_present(self, initial_wait=10):
        """Wait briefly for the captcha modal; if it shows up, solve it.

        Returns True if a modal was found and solved, False if no modal
        appeared within initial_wait seconds.
        """
        if not self.is_modal_present(timeout=initial_wait):
            return False
        self._solve_modal()
        return True

    def _solve_modal(self):
        sb = self.browser.sb
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                sb.uc_gui_click_captcha()
                sb.sleep(2)
                sb.uc_click('app-cloudflare-dialog button:contains("Submit")')
                sb.sleep(2)
                if not sb.is_element_visible("app-cloudflare-dialog"):
                    return
            except Exception as e:
                last_error = e
                print(f"Captcha attempt {attempt}/{self.max_retries} failed: {e}")
            sb.sleep(2)
        raise RuntimeError(
            f"Failed to solve captcha modal after {self.max_retries} attempts: {last_error}"
        )
