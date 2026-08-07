# booking_flow.py
"""Orchestrates the booking flow once a slot has been detected and the
browser is sitting on VFS's 'application-detail' page with that slot held
(this is wired in right after main.py's existing action == "book"
detection). Drives: Continue -> your-details form -> OTP -> book-appointment
(date/time) -> review/consent -> payment disclaimer -> Kalixa payment.

This is a state machine keyed off the current URL plus which key element is
visible (the app is an Angular SPA, so most of these steps don't reload the
page) - dispatching to the module that already implements each step.
Unknown states stop the bot and push a status event rather than guessing: a
wrong click here risks double-submitting a booking or a payment.
"""
import time

from applicant_form_filler import ApplicantFormFiller
from calendar_slot_picker import CalendarSlotPicker
from captcha_handler import CaptchaHandler
from otp_step import OtpStep
from payment_handler import PaymentHandler
from review_step import ReviewStep


class BookingFlow:
    def __init__(self, browser_client, bridge, email):
        self.browser = browser_client
        self.bridge = bridge
        self.email = email
        self.captcha = CaptchaHandler(browser_client)

    def run(self, max_steps=30):
        sb = self.browser.sb
        self.bridge.push_status("booking_flow_started")

        sb.sleep(2)
        sb.uc_click("button.mat-btn-lg")  # "Continue" on application-detail
        self._wait_until_url_contains("your-details", timeout=30)
        self._simulate_idle(seconds=30)

        for _ in range(max_steps):
            url = sb.get_current_url()

            if "kalixa.com" in url:
                self.bridge.push_status("step_payment")
                PaymentHandler(self.browser, self.bridge).run()
                self.bridge.push_status("booking_flow_finished")
                return True

            elif self._payment_disclaimer_visible():
                self.bridge.push_status("step_payment_disclaimer")
                sb.uc_click('button:contains("Continue")')

            elif "your-details" in url and self._otp_panel_visible():
                self.bridge.push_status("step_otp")
                OtpStep(self.browser, self.email, self.bridge).run()

            elif "your-details" in url and self._summary_visible():
                self.bridge.push_status("step_applicant_summary")
                sb.uc_click('button:contains("Continue")')

            elif "your-details" in url and self._applicant_form_visible():
                self.bridge.push_status("step_applicant_form")
                self._fill_applicant_form()

            elif "book-appointment" in url:
                self.bridge.push_status("step_book_appointment")
                self._handle_book_appointment()

            elif "review" in url or self._review_heading_visible():
                self.bridge.push_status("step_review")
                ReviewStep(self.browser, self.bridge).run()

            else:
                self.bridge.push_status("step_unknown", {"url": url})
                raise RuntimeError(f"booking_flow reached an unrecognized page: {url}")

            self.captcha.solve_if_present(initial_wait=5)
            sb.sleep(1)

        raise RuntimeError("booking_flow exceeded max_steps without finishing")

    # --- step detection helpers ---

    def _otp_panel_visible(self):
        return self.browser.sb.is_element_visible('h2:contains("One-time Password")')

    def _summary_visible(self):
        return self.browser.sb.is_element_visible('h1:contains("Your Details Summary")')

    def _applicant_form_visible(self):
        return self.browser.sb.is_element_visible('button:contains("Save")')

    def _payment_disclaimer_visible(self):
        return self.browser.sb.is_element_visible('h1:contains("Payment Disclaimer")')

    def _review_heading_visible(self):
        return self.browser.sb.is_element_visible('h1:contains("Review")')

    # --- step implementations ---

    def _fill_applicant_form(self):
        filler = ApplicantFormFiller(self.browser)

        self.browser._log("  Fetching required fields from applicant form...")
        # Angular may still be rendering the field controls the instant the
        # Save button (our visibility trigger) appears, so a single
        # immediate read can race it and come back empty — retry briefly
        # before giving up and showing the user an empty form.
        fields = filler.collect_fields()
        for attempt in range(2, 4):
            if fields:
                break
            self.browser._log(f"  No fields detected yet — retrying ({attempt}/3)...")
            self.browser.sb.sleep(2)
            fields = filler.collect_fields()
        self.browser._log(
            "  Found %d field(s): %s"
            % (len(fields), [f"{f['label']}{'*' if f.get('required') else ''}" for f in fields])
        )
        self.bridge.push_status("applicant_fields_fetched", {"fields": fields})

        values = self.bridge.ask(
            "enter_applicant_details", {"login_user": self.email, "fields": fields}
        ) or {}

        self.browser._log("  Applicant details received from dashboard — filling form...")
        unmatched = filler.fill_dynamic(fields, values)
        if unmatched:
            self.browser._log(f"  Field(s) left blank: {unmatched}")

        filler.save()

    def _handle_book_appointment(self):
        picker = CalendarSlotPicker(self.browser)
        picker.select_appointment_type()

        dates = picker.get_available_dates()
        date_choice = self.bridge.ask("select_date", {"dates": dates})
        picker.select_date(date_choice)
        self.bridge.push_status("date_selected", {"date": date_choice})

        times = picker.get_available_times()
        time_choice = self.bridge.ask("select_time", {"date": date_choice, "times": times})
        picker.select_time(time_choice)
        self.bridge.push_status("time_selected", {"row_id": time_choice})

        self.browser.sb.uc_click('button:contains("Continue")')

    def _wait_until_url_contains(self, substring, timeout):
        sb = self.browser.sb
        deadline = time.time() + timeout
        while time.time() < deadline:
            if substring in sb.get_current_url():
                return
            sb.sleep(0.5)
        raise TimeoutError(f"URL never contained '{substring}' within {timeout}s")

    def _simulate_idle(self, seconds):
        # Best-effort only: VFS's idle-detection may rely on real OS-level
        # input rather than synthetic DOM events, in which case this should
        # be swapped for SeleniumBase's CDP-based mouse movement instead.
        sb = self.browser.sb
        end = time.time() + seconds
        while time.time() < end:
            try:
                sb.execute_script(
                    "document.dispatchEvent(new MouseEvent('mousemove', "
                    "{clientX: Math.random()*500, clientY: Math.random()*500}));"
                )
            except Exception:
                pass
            sb.sleep(2)
