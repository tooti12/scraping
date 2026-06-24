# VFS Slot Checker & Booking Bot

## What this is, in plain words

VFS Global is the company that handles UK visa appointment bookings for
several countries. This tool watches it for us, automatically, so nobody
has to sit there refreshing the website all day waiting for a slot to
open.

How it actually works:

1. A bot logs into VFS Global, the same way a person would.
2. It goes through every country we care about, one at a time, and checks
   whether any appointment slot is currently open.
3. Whatever it finds shows up on a simple website (the "dashboard") —
   anyone can open it in a browser and see, at a glance, which countries
   currently have a slot open and which don't.
4. It keeps doing this in the background, all day, every couple of
   minutes, so the website is always showing a fresh result without
   anyone needing to trigger a check by hand.
5. There's also a separate flow being built for actually completing a
   booking once a slot is found — filling in the applicant's details,
   picking a date and time, and paying — with a person reviewing and
   approving each step rather than the bot doing it unsupervised.

A fair amount of care has gone into making the bot behave like a genuine
visitor rather than an obvious automated script, because VFS actively
detects and blocks accounts/addresses it suspects of being bots. In
practice that means: checking from real residential internet addresses
(not the kind of address real visitors would never use), spacing checks
out instead of hammering the site back-to-back, automatically backing off
for a while if VFS ever shows a "you've been blocked" message, and
staying logged in rather than logging in and out constantly — logging in
too often, on its own, is the single biggest thing that gets an account
flagged.

VFS also sometimes sends the login verification code as a deliberately
distorted, hard-to-read image instead of plain text, specifically to stop
tools like this from reading it automatically. We built a way to read
that image correctly too.

None of this touches anything private or anyone else's data — it only
checks the same publicly-visible appointment availability any visitor
could see by opening the site themselves; this just does it continuously
and puts the result in one place instead of someone checking by hand.

## Technical overview

Automates checking and booking VFS Global visa appointment slots, with a
local web dashboard for the human-in-the-loop steps booking requires
(applicant details, OTP, date/time, payment).

- `main.py` — continuous monitor (`VfsScraper`) that logs in to VFS and
  polls slot availability per country, firing a WhatsApp/SMS alert when a
  slot or waitlist opens.
- `dashboard/` — Flask app with three surfaces: `/` (marketing homepage),
  `/availability` (public, live per-country slot status, backed by
  `slot_status_cache.py`'s background loop), and `/booking` (internal
  booking console, talks to the automation via `FrontendBridge`).
- `browser_client.py` / `auth_handler.py` — SeleniumBase/undetected-Chrome
  driver for VFS: login, OTP, Cloudflare solving, slot checks, booking.
- `booking_flow.py` — the human-in-the-loop booking state machine (applicant
  form → OTP → date/time → review → payment).

See `DEPLOYMENT.md` for production rollout notes and `BOOKING_FLOW_PLAN.md`
for the booking flow's design history.

## Proxy

All VFS traffic goes through one shared residential proxy, configured via
the `VFS_PROXY_URL` env var. Every `BrowserClient` instance (one per
country, per check) opens its own local forwarding proxy (`LocalAuthProxy`
in `browser_client.py`) that injects the upstream credentials, so Chrome
never sees a proxy-auth prompt.

The proxy username encodes a sticky-session id, e.g.:

```
user-<id>-type-residential-session-av7gkchx-country-gb-rotation-0
```

Any connection presenting that exact username gets handed the **same** exit
IP for as long as that session id is reused. Left as a fixed value, every
country's browser would share one exit IP — `browser_client.py`'s
`_randomize_proxy_session()` replaces the `session-<id>` segment with a
fresh random id on every `BrowserClient` launch, so each country (and each
new check cycle) gets its own exit IP from the provider instead. The
`country-gb` segment is left untouched, since that's what keeps traffic
geo-targeted to the UK for VFS.

Still open (see `DEPLOYMENT.md` §8): confirming with the proxy provider
whether there's a cap on how many distinct sessions can run concurrently or
per billing period under one credential.

## OTP

VFS sends the login OTP by email, read via IMAP in `notification_handler.GmailOTPClient`.
It randomly serves one of two templates from the same sender:

- **Plain text** — "VFS Global is 873647" — handled by a regular regex.
- **Image CAPTCHA** — the OTP is rendered as a distorted, multi-colored
  image with 1-2 decoy 6-digit codes stacked alongside the real one, with
  only a small green "OTP" tag marking which one is correct (clearly meant
  to resist exactly this kind of automated reading). Handled by OCR
  (`pytesseract` + Tesseract): the code finds the green tag's position and
  reads whichever number lines up with it. Both paths are tried on every
  email — whichever template it turns out to be, one of them will match.

Requires the **Tesseract OCR engine** installed separately from
`pip install -r requirements.txt` (that only gets `pytesseract`, the
Python wrapper) — `winget install tesseract-ocr.tesseract` on Windows,
`apt install tesseract-ocr` on the production Linux droplet (see
`DEPLOYMENT.md` §4). Without it, the image-CAPTCHA template just fails to
yield an OTP; the plain-text template is unaffected either way.

OCR on deliberately-distorted digits isn't 100% reliable — validated
against real captured VFS OTP images at ~75% exact-match (the rest were
off by a single digit, never a wrong row/code entirely), which is why
`GmailOTPClient.get_otp()` also rejects any email older than the current
login attempt (`since=`) rather than ever falling back to a stale OTP from
an earlier one — that previously-silent fallback was the actual cause of
several "VFS blocked us" -looking failures that were really just an
expired code being resubmitted.
