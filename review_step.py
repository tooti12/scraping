# review_step.py
"""Drives the Review & Payment page: reads the recap, asks the human to
confirm via the dashboard, ticks the T&Cs checkbox, and clicks Pay Online.
A 'cancel' answer clicks Go Back instead of proceeding - it does not stop
the bot, since the booking flow may want to re-pick a date/time and retry.
"""
import json
import time


class ReviewStep:
    def __init__(self, browser_client, bridge):
        self.browser = browser_client
        self.bridge = bridge

    def run(self):
        recap = self._collect_recap()
        self.bridge.push_status("review_ready", recap)
        answer = self.bridge.ask("confirm_review", recap)

        if not answer or not answer.get("confirmed"):
            self.bridge.push_status("review_cancelled")
            self.browser.sb.uc_click('button:contains("Go Back")')
            return False

        self._check_checkbox_by_label("I accept the")
        self.bridge.push_status("terms_accepted")
        self._wait_until_button_enabled("Pay Online", timeout=10)
        self.browser.sb.uc_click('button:contains("Pay Online")')
        return True

    def _collect_recap(self):
        script = """
            function pairs() {
                const result = {};
                document.querySelectorAll('ul.list-group li.list-group-item').forEach(item => {
                    const label = item.querySelector('.c-brand-grey-para');
                    const value = item.querySelector('.fs-18');
                    if (label && value) result[label.textContent.trim()] = value.textContent.trim();
                });
                return result;
            }
            function totalAmount() {
                let total = '';
                document.querySelectorAll('.card-body .row').forEach(row => {
                    const cols = row.querySelectorAll('.col, .col-auto');
                    if (cols.length >= 2 && cols[0].textContent.trim() === 'Total') {
                        total = cols[1].textContent.trim();
                    }
                });
                return total;
            }
            const nameEl = document.querySelector('.reviewpayment-applicantname .text-overflow');
            return {
                details: pairs(),
                applicant_name: nameEl ? nameEl.textContent.trim() : '',
                total: totalAmount()
            };
        """
        return self.browser.sb.execute_script(script) or {}

    def _check_checkbox_by_label(self, label_text):
        script = f"""
            const target = {json.dumps(label_text)};
            const labels = Array.from(document.querySelectorAll('mat-checkbox label.mdc-label'));
            const label = labels.find(l => l.textContent.includes(target));
            return label ? label.getAttribute('for') : null;
        """
        checkbox_id = self.browser.sb.execute_script(script)
        if not checkbox_id:
            raise ValueError(f"Checkbox labelled '{label_text}' not found")
        self.browser.sb.click(f"#{checkbox_id}")

    def _wait_until_button_enabled(self, text, timeout):
        sb = self.browser.sb
        deadline = time.time() + timeout
        script = f"""
            const target = {json.dumps(text)};
            const btns = Array.from(document.querySelectorAll('button'));
            const btn = btns.find(b => b.textContent.trim().includes(target));
            return btn ? btn.disabled : null;
        """
        while time.time() < deadline:
            if sb.execute_script(script) is False:
                return
            sb.sleep(0.5)
        raise TimeoutError(f"Button '{text}' did not become enabled within {timeout}s")
