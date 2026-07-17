# notification_handler.py
import email as email_lib
import imaplib
import io
import re
import time
from email.utils import parsedate_to_datetime

import requests

from config import EMAIL_CONFIG, GMAIL_CONFIG, WHATSAPP_CONFIG

# Some VFS OTP emails (same sender/subject as the plain-text ones — VFS
# appears to randomly serve either template) embed the OTP as a distorted,
# multi-colored CAPTCHA-style image instead of plain text, specifically to
# resist exactly this kind of automated extraction: the image shows 2-3
# decoy 6-digit codes stacked vertically, and only a small green "OTP" tag
# positioned beside the *real* one tells a human which is correct. Optional
# import — OCR only kicks in for this image-only template; the existing
# plain-text extraction below still handles the other template on its own
# and doesn't need this at all. Requires the Tesseract OCR binary installed
# separately from its `pip install pytesseract` (see README.md).
try:
    import os as _os

    import pytesseract
    from PIL import Image

    # pytesseract calls the tesseract binary by name via PATH, which the
    # Windows installer doesn't always add — point at the default install
    # location if it's there and not already resolvable. No-op on
    # Linux/macOS, where `apt install tesseract-ocr` etc. puts it on PATH.
    _DEFAULT_WIN_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if _os.name == "nt" and _os.path.exists(_DEFAULT_WIN_TESSERACT):
        pytesseract.pytesseract.tesseract_cmd = _DEFAULT_WIN_TESSERACT

    _TESSERACT_AVAILABLE = True
except ImportError:
    _TESSERACT_AVAILABLE = False


# Scale factor that empirically gave the most exact reads against real VFS
# OTP CAPTCHA images during development (3x: 3/4 exact matches vs. fewer at
# 1x or 5x) — Tesseract's accuracy on small, heavily distorted/multi-colored
# digits is very sensitive to this, in both directions.
_OTP_IMAGE_OCR_SCALE = 3


def _green_otp_badge_band(img) -> tuple[int, int] | None:
    """The correct code (vs. the 1-2 decoy codes also in the image) is the
    one positioned beside the small solid-green "OTP" tag on the left edge.
    Scans the left margin for green pixels and returns the (top, bottom) of
    that band in the image's own (unscaled) coordinates."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    green_rows = []
    for y in range(h):
        for x in range(0, min(40, w)):
            r, g, b = rgb.getpixel((x, y))
            if g > 120 and g > r * 1.4 and g > b * 1.4:
                green_rows.append(y)
                break
    if not green_rows:
        return None
    return min(green_rows), max(green_rows)


def _extract_otp_from_captcha_image(image_bytes: bytes) -> str | None:
    """OCRs a VFS OTP CAPTCHA image (2-3 stacked, distorted, multi-colored
    6-digit codes) and returns whichever one lines up with the green "OTP"
    tag — see the module docstring above _TESSERACT_AVAILABLE for why this
    exists at all. Returns None (never raises) if Tesseract isn't
    installed, the badge can't be found, or no clean 6-digit token lines up
    with it — callers should treat that the same as "no OTP found here"."""
    if not _TESSERACT_AVAILABLE:
        print("[GmailOTPClient] Tesseract/pytesseract not available — can't OCR the OTP image.")
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        print(f"[GmailOTPClient] OCR image size: {img.size[0]}x{img.size[1]}px")
        band = _green_otp_badge_band(img)
        if band is None:
            print("[GmailOTPClient] Couldn't locate the green OTP badge — trying full-image OCR fallback.")
            # No green badge found (different image variant) — just take the
            # first clean 6-digit token from the full image.
            scale = _OTP_IMAGE_OCR_SCALE
            big = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
            data = pytesseract.image_to_data(
                big,
                output_type=pytesseract.Output.DICT,
                config="--psm 11 -c tessedit_char_whitelist=0123456789",
            )
            for i in range(len(data["text"])):
                text = re.sub(r"\D", "", data["text"][i].strip())
                if len(text) == 6:
                    print("[GmailOTPClient] OCR fallback: found 6-digit token (no badge).")
                    return text
            print("[GmailOTPClient] OCR fallback found no 6-digit token either.")
            return None

        badge_top, badge_bottom = band
        badge_center = (badge_top + badge_bottom) / 2
        print(f"[GmailOTPClient] Green OTP badge found: y={badge_top}-{badge_bottom} center={badge_center:.1f}px")

        scale = _OTP_IMAGE_OCR_SCALE
        big = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        data = pytesseract.image_to_data(
            big,
            output_type=pytesseract.Output.DICT,
            config="--psm 11 -c tessedit_char_whitelist=0123456789",
        )

        candidates = []
        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            if len(text) < 6:
                continue
            top = data["top"][i] / scale
            height = data["height"][i] / scale
            token_center = top + height / 2
            distance = abs(token_center - badge_center)
            candidates.append((distance, text, token_center))

        if not candidates:
            print("[GmailOTPClient] OCR found no 6+ digit token near the OTP badge.")
            return None

        candidates.sort(key=lambda x: x[0])
        print(f"[GmailOTPClient] OCR candidates (distance, center_y): "
              f"{[(round(d,1), round(c,1)) for d, _, c in candidates[:5]]}")

        best_distance, best_token, _ = candidates[0]
        print(f"[GmailOTPClient] Best token distance={best_distance:.1f}px from badge center.")

        # The real OTP is always exactly 6 digits; OCR occasionally fuses a
        # neighboring decoy's leading/trailing digit onto the real one when
        # they're close together — take the 6 digits actually nearest the
        # badge's row rather than rejecting a longer match outright.
        digits = re.sub(r"\D", "", best_token)
        return digits[:6] if len(digits) >= 6 else None
    except Exception as e:
        print(f"[GmailOTPClient] OCR extraction failed: {type(e).__name__}: {e}")
        return None


class SMSNotifier:
    def __init__(self):
        self.url = "https://api.callmebot.com/whatsapp.php"
        self.payload = {
            "phone": WHATSAPP_CONFIG["phone"],
            "apikey": WHATSAPP_CONFIG["apikey"],
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

    def get_otp(self, email_address, max_wait=90, poll_interval=5, since: float | None = None):
        """Poll Gmail until a fresh OTP email from VFS arrives (up to max_wait
        seconds). `since`, if given (a time.time() value from right before
        this login attempt's OTP was requested), rejects any email older
        than that instead of falling back to it.

        That fallback used to be silent and is exactly what was breaking
        real logins: when the 1-2 newest emails fail OTP extraction (some
        VFS OTP emails apparently use a body format _extract_otp doesn't
        handle), the old code fell through to an *older* email further
        down the inbox and returned that email's OTP instead — a code
        already used (or expired) from a previous login attempt. Submitting
        a stale OTP gets rejected by VFS, which looks exactly like a
        session/IP block in the logs but is actually just an invalid code
        we handed it ourselves."""
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
                            if not msg_data or not msg_data[0]:
                                continue
                            msg = email_lib.message_from_bytes(msg_data[0][1])
                            print(f"[GmailOTPClient] Fetched email id={eid.decode()}. Extracting OTP...")
                            if since is not None and not self._received_after(msg, since):
                                print(f"[GmailOTPClient] Email id={eid.decode()} predates this login attempt — skipping (stale).")
                                continue
                            otp = self._extract_otp(msg)
                            if otp:
                                print("[GmailOTPClient] OTP extracted successfully.")  # value intentionally not logged
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

    @staticmethod
    def _received_after(msg, since: float) -> bool:
        date_str = msg.get("Date")
        if not date_str:
            return True  # can't tell — don't block a possibly-valid email on it
        try:
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                from datetime import timezone

                dt = dt.replace(tzinfo=timezone.utc)
            # 30s grace for mail-server queueing delay / clock skew, not for
            # genuinely stale emails from a prior attempt minutes earlier.
            return dt.timestamp() >= since - 30
        except Exception:
            return True

    def _extract_otp(self, msg):
        sender = msg.get("From", "unknown")
        subject = msg.get("Subject", "unknown")
        print(f"[GmailOTPClient] Email from: {sender} | Subject: {subject}")

        body = self._get_body(msg)
        print(f"[GmailOTPClient] Email body preview: {body[:120].strip()!r}")
        candidates = [body]
        stripped = re.sub(r"<[^>]+>", "", body)
        if stripped != body:
            candidates.append(stripped)

        # Primary pattern: "VFS Global is 873647"
        for candidate in candidates:
            match = re.search(r"VFS Global is\s+(\d{6})", candidate, re.IGNORECASE)
            if match:
                print(f"[GmailOTPClient] OTP matched via primary pattern.")
                return match.group(1)

        # Generic fallback: any standalone 6-digit number
        for candidate in candidates:
            match = re.search(r"\b(\d{6})\b", candidate)
            if match:
                print(f"[GmailOTPClient] OTP matched via fallback 6-digit pattern.")
                return match.group(1)

        # Neither plain-text pattern matched — this is most likely the
        # image-CAPTCHA template (see module docstring above _TESSERACT_AVAILABLE),
        # which has no plain-text OTP at all. Try OCR on any embedded image.
        image_bytes = self._find_embedded_image(msg)
        if image_bytes:
            otp = _extract_otp_from_captcha_image(image_bytes)
            if otp:
                print("[GmailOTPClient] OTP matched via image OCR.")  # value intentionally not logged
                return otp

        print("[GmailOTPClient] Could not extract OTP from this email.")
        return None

    @staticmethod
    def _find_embedded_image(msg) -> bytes | None:
        if not msg.is_multipart():
            return None
        for part in msg.walk():
            if part.get_content_type().startswith("image/"):
                try:
                    return part.get_payload(decode=True)
                except Exception:
                    pass
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
