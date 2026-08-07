# payment_handler.py
"""Fills and submits the Kalixa/PXP card payment form. Unlike the VFS
Angular app, this is a plain server-rendered page, so its field ids
(HolderName, CardNumber, ExpiryMonth, ExpiryYear, CardVerificationCode) are
static and safe to hard-code.

Card details come from FrontendBridge.ask("enter_card_details", ...) - this
module only ever holds them in local variables long enough to type them in
and submit; it never logs the full number/CVV/holder name, never writes
them to config or to the JSON appointment logs (only a masked last-4 digits
goes into push_status for the dashboard's status feed).
"""


class PaymentHandler:
    def __init__(self, browser_client, bridge):
        self.browser = browser_client
        self.bridge = bridge

    def run(self):
        total = self._read_total()
        card = self.bridge.ask("enter_card_details", {"total": total})
        if not card:
            raise RuntimeError("No card details received from dashboard")

        self._fill(card)
        self.bridge.push_status(
            "payment_submitted", {"card_last4": self._last4(card.get("card_number", ""))}
        )
        self.browser.sb.uc_click("#payBtn")

    def _read_total(self):
        try:
            return self.browser.sb.get_text("#paymentAmount")
        except Exception:
            return ""

    def _fill(self, card):
        sb = self.browser.sb
        sb.type("#HolderName", card.get("holder_name", ""))
        sb.type("#CardNumber", card.get("card_number", ""))
        sb.select_option_by_text("#ExpiryMonth", self._normalize_month(card.get("expiry_month", "")))
        sb.select_option_by_text("#ExpiryYear", str(card.get("expiry_year", "")).strip())
        sb.type("#CardVerificationCode", card.get("cvv", ""))

    @staticmethod
    def _normalize_month(month_value):
        # The dashboard's free-text "MM" field may come in as "06" or "6";
        # the <select>'s option values are unpadded ("1".."12").
        try:
            return str(int(str(month_value).strip()))
        except (TypeError, ValueError):
            return str(month_value).strip()

    @staticmethod
    def _last4(card_number):
        digits = "".join(ch for ch in str(card_number) if ch.isdigit())
        return digits[-4:] if len(digits) >= 4 else ""
