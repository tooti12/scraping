# notification_handler.py
import email as email_lib
import imaplib
import re
import time

import requests

from config import EMAIL_CONFIG, GMAIL_CONFIG


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
    """Legacy IMAP client for thesemantics.co accounts."""

    def get_otp(self, email):
        try:
            with imaplib.IMAP4_SSL(EMAIL_CONFIG["imap_server"]) as mail:
                mail.login(email, EMAIL_CONFIG["password"])
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
        msg = email_lib.message_from_bytes(raw_email)
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    return re.search(r"\d{6}", part.get_payload()).group()
        return re.search(r"\d{6}", msg.get_payload()).group()


class GmailOTPClient:
    """Reads the VFS OTP from a Gmail inbox using IMAP + App Password."""

    def get_otp(self, email_address, max_wait=90, poll_interval=5):
        """Poll Gmail until a fresh OTP email from VFS arrives (up to max_wait seconds)."""
        app_password = GMAIL_CONFIG["app_password"].replace(" ", "")
        imap_server = GMAIL_CONFIG["imap_server"]
        start = time.time()

        print(f"[GmailOTPClient] Connecting to {imap_server} as {email_address} ...")

        while time.time() - start < max_wait:
            elapsed = int(time.time() - start)
            try:
                with imaplib.IMAP4_SSL(imap_server) as mail:
                    mail.login(email_address, app_password)
                    print(f"[GmailOTPClient] Logged in. Searching inbox for VFS email... ({elapsed}s elapsed)")
                    mail.select("inbox")

                    # Search by sender (donotreply@vfshelpline.com)
                    status, messages = mail.search(None, 'FROM "donotreply@vfshelpline.com"')

                    if messages[0]:
                        email_ids = messages[0].split()
                        print(f"[GmailOTPClient] Found {len(email_ids)} VFS email(s) in inbox. Checking most recent...")
                        # Check the 5 most recent emails to avoid stale OTPs
                        for eid in reversed(email_ids[-5:]):
                            status, msg_data = mail.fetch(eid, "(RFC822)")
                            if msg_data and msg_data[0]:
                                print(f"[GmailOTPClient] Fetched email id={eid.decode()}. Extracting OTP...")
                                otp = self._extract_otp(msg_data[0][1])
                                if otp:
                                    print(f"[GmailOTPClient] OTP extracted successfully: {otp}")
                                    return otp
                                else:
                                    print(f"[GmailOTPClient] No OTP pattern found in email id={eid.decode()}, trying next...")
                    else:
                        print(f"[GmailOTPClient] No VFS emails found yet. ({elapsed}s elapsed)")

            except Exception as exc:
                print(f"[GmailOTPClient] Error checking Gmail: {exc}")

            print(f"[GmailOTPClient] Waiting {poll_interval}s before next check...")
            time.sleep(poll_interval)

        print(f"[GmailOTPClient] Timed out after {max_wait}s — no OTP email received.")
        return None

    def _extract_otp(self, raw_email):
        msg = email_lib.message_from_bytes(raw_email)
        sender = msg.get("From", "unknown")
        subject = msg.get("Subject", "unknown")
        print(f"[GmailOTPClient] Email from: {sender} | Subject: {subject}")

        body = self._get_body(msg)
        print(f"[GmailOTPClient] Email body preview: {body[:120].strip()!r}")

        # Primary pattern: "VFS Global is 873647"
        match = re.search(r"VFS Global is\s+(\d{6})", body, re.IGNORECASE)
        if match:
            print(f"[GmailOTPClient] OTP matched via primary pattern.")
            return match.group(1)

        # Generic fallback: any standalone 6-digit number
        match = re.search(r"\b(\d{6})\b", body)
        if match:
            print(f"[GmailOTPClient] OTP matched via fallback 6-digit pattern.")
            return match.group(1)

        print("[GmailOTPClient] Could not extract OTP from this email.")
        return None

    def _get_body(self, msg):
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    try:
                        body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                elif ct == "text/html" and not body:
                    try:
                        body += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            except Exception:
                body = str(msg.get_payload())
        return body
