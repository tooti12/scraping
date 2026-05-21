# notification_handler.py
import email as _email_lib
import imaplib
import re
import time

import requests

from config import GMAIL_CONFIG


class SMSNotifier:
    def __init__(self):
        self.url = "https://api.callmebot.com/whatsapp.php"
        self.payload = {"phone": "447724267222", "apikey": 1122425}

    def send_sms(self, message):
        self.payload["text"] = message
        response = requests.get(self.url, params=self.payload)
        print("Status Code:", response.status_code)
        print("Response Text:", response.text)


class EmailClient:
    """
    Reads the VFS OTP from Gmail via IMAP.
    Interface kept identical to the original so auth_handler.py needs no changes:
        EmailClient().get_otp(email=email)
    """

    _OTP_RE = re.compile(r"\b(\d{6})\b")

    def get_otp(self, email, retries: int = 8, wait_seconds: int = 10):
        """
        Poll Gmail until the VFS OTP email arrives.
        `email` is used as the IMAP login username (the Gmail address).
        Returns the 6-digit OTP string, or None if not found.
        """
        sender = GMAIL_CONFIG["otp_sender"]
        app_password = GMAIL_CONFIG["app_password"]

        print(
            f"[EmailClient] Polling Gmail for OTP from {sender} "
            f"(up to {retries * wait_seconds}s)...",
            flush=True,
        )

        for attempt in range(1, retries + 1):
            try:
                with imaplib.IMAP4_SSL(GMAIL_CONFIG["imap_server"], GMAIL_CONFIG["imap_port"]) as mail:
                    mail.login(email, app_password)
                    mail.select("inbox")
                    status, messages = mail.search(None, f'FROM "{sender}"')
                    if status == "OK" and messages[0]:
                        latest_id = messages[0].split()[-1]
                        _, data = mail.fetch(latest_id, "(RFC822)")
                        otp = self._extract_otp(data[0][1])
                        if otp:
                            print(f"[EmailClient] OTP found on attempt {attempt}: {otp}", flush=True)
                            return otp
            except Exception as exc:
                print(f"[EmailClient] Attempt {attempt}/{retries} error: {exc}", flush=True)

            if attempt < retries:
                print(f"[EmailClient] Attempt {attempt}/{retries}: not yet, waiting {wait_seconds}s...", flush=True)
                time.sleep(wait_seconds)

        print("[EmailClient] OTP not found after all retries.", flush=True)
        return None

    def _extract_otp(self, raw_bytes: bytes):
        msg = _email_lib.message_from_bytes(raw_bytes)
        parts = list(msg.walk()) if msg.is_multipart() else [msg]
        for part in parts:
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    m = self._OTP_RE.search(payload.decode(errors="ignore"))
                    if m:
                        return m.group(1)
        return None