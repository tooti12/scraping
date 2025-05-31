# notification_handler.py
import email
import imaplib
import re

import requests

from config import EMAIL_CONFIG


class SMSNotifier:
    def __init__(self):

        self.url = "https://api.callmebot.com/whatsapp.php"

        self.payload = {
            "phone": "447724267222",
            "apikey": 1122425,
        }

    def send_sms(self, message):
        self.payload["text"] = message
        response = requests.get(self.url, params=self.payload)
        print("Status Code:", response.status_code)
        print("Response Text:", response.text)


class EmailClient:
    def get_otp(self):
        try:
            with imaplib.IMAP4_SSL(EMAIL_CONFIG["imap_server"]) as mail:
                mail.login(EMAIL_CONFIG["username"], EMAIL_CONFIG["password"])
                mail.select("inbox")
                status, messages = mail.search(None, 'SUBJECT "One Time Password"')

                if messages[0]:
                    latest_email_id = messages[0].split()[-1]
                    status, msg_data = mail.fetch(latest_email_id, "(RFC822)")
                    return self._extract_otp(msg_data[0][1])
        except Exception as e:
            print(f"Error retrieving OTP: {e}")
            return None

    def _extract_otp(self, raw_email):
        msg = email.message_from_bytes(raw_email)
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    return re.search(r"\d{6}", part.get_payload()).group()
        return re.search(r"\d{6}", msg.get_payload()).group()
