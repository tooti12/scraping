# VFS Slot Checker & Booking Bot

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
