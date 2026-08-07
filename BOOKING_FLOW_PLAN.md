# VFS Auto-Booking: End-to-End Implementation Plan

## 1. Where the project stands today

The bot currently does exactly one job well: log in, hold a session, poll
`CheckIsSlotAvailable`, classify the response, and fire a WhatsApp alert when a
slot or waitlist opens (`main.py` → `VfsScraper.start_monitoring`). Once a slot
is detected, the bot does nothing else — a human has to take over in the
browser from that point on, and there is no UI of any kind, just terminal
prints. Everything below is new: the booking flow itself (applicant form →
OTP → captcha → appointment date/time → review → payment) **and** a local
web dashboard so the human decision points in that flow happen through a
proper frontend instead of a blocking `input()` call in a terminal.

## 2. The flow we now have to automate (from the attached walkthrough)

Each step below is keyed off a real URL/page change or a modal, because the
app is an Angular SPA — we drive it by waiting for the right element to
appear, not by sleeping a fixed amount of time.

| # | Page / trigger | What happens | Captcha? | Needs user input? |
|---|---|---|---|---|
| 1 | `…/application-detail` → click **Continue** | Navigates to `…/your-details`. Page idles ~30s with synthetic mouse movement (anti-idle) | No | No |
| 2 | `…/your-details` | Dynamic applicant form (`app-dynamic-form` / `app-dynamic-control`). Fields vary **by destination country** — NLD's form is shorter (no Gender/DOB/Nationality blocks shown) than PRT's. Click **Save** | Yes (`Verify Captcha` modal, Cloudflare Turnstile) | No |
| 3 | Returns to "Your Details Summary" | Read-only recap card, Edit/Delete, service-fee notice. Click **Continue** | No | No |
| 4 | OTP panel appears (still `your-details` step) | Click **Generate OTP** → email arrives → enter 6-digit code → **Verify** → success banner → **Continue** enabled | No | No (OTP fetched same as login, via IMAP) |
| 5 | `…/book-appointment` | Modal **Verify Captcha** appears immediately on load | Yes | No |
| 6 | Book Appointment page | Select "Choose a slot" radio, then a `full-calendar` grid where only some `<td>` cells carry `date-availiable` + `data-date` | No | **Yes (dashboard)** — present available dates, user picks one |
| 7 | After date click | "Choose an appointment time" section appears: a period filter dropdown (`All`/`Morning`/`Afternoon`/`Evening`) + a table of time rows, each with a hidden allocation token and a `Select` toggle | No | **Yes (dashboard)** — present available times, user picks one |
| 8 | Click **Continue** | `…/review` (Review & Payment) page: applicant recap, appointment recap, fees, two checkboxes (T&Cs required, marketing optional) | No | **Yes (dashboard)** — show full summary, ask for go-ahead before ticking T&Cs |
| 9 | Tick T&Cs → **Pay Online** enabled → click it | "Payment Disclaimer" interstitial page, Cancel/Continue | No | No |
| 10 | Click **Continue** | **Verify Captcha** modal again, then redirect off-domain to `payments.kalixa.com/iCheckout/...` (PXP Financial) | Yes | No |
| 11 | Kalixa/PXP card form | Holder Name, Card Number, Expiry Month/Year, CVV, optional "save card" checkbox, **Pay now** | No | **Yes (dashboard)** — card entry form, submitted from the browser dashboard, not the terminal |

Decision confirmed with you on payment: **console/dashboard-prompted
auto-fill** — the bot asks for card details interactively at the point of
payment (now via the web dashboard rather than a terminal prompt) and
submits the Kalixa form itself. Card data is never written to disk or to the
JSON logs.

## 3. The core design problem: the form is not static

The biggest risk in this project is hard-coding `#mat-input-6` style
selectors. Angular Material regenerates these IDs per render, and — more
importantly — **the field set itself changes per destination country and
visa category** (compare the NLD `your-details` fields against PRT's, which
adds Gender, Date of Birth, and a 263-option Nationality dropdown). Building
one fixed sequence of `.type()` calls will break on the very first country
switch.

**Design principle:** build one generic, label-driven form filler instead of
per-field selectors:

1. Query the DOM (via a single JS snippet, not dozens of Python calls) for
   every visible `app-input-control` / `app-dropdown` / `app-ngb-datepicker`
   inside the active form, and for each one capture its **label text**
   (e.g. "First Name", "Passport Expiry Date") together with a stable handle
   (the control's own generated `id`, fetched at fill-time, never cached
   across renders).
2. Normalize each label to a config key (`"passport expiry date"` →
   `passport_expiry_date`).
3. Look the key up in a per-applicant config dict; if present, fill it using
   the matching strategy for that control type (plain type for text inputs,
   "click + filter + click option" for `mat-select` dropdowns, "type
   dd/mm/yyyy" for the `ngb-datepicker` inputs).
4. Skip any label not present in config and log it — this makes the system
   self-documenting when VFS adds/removes a field for a given country.

This one piece of work (the generic filler) is what makes steps 2, and to a
lesser extent 6–7, tractable without a fork per country.

## 4. Backend modules

Keeping the existing flat, single-purpose-file layout (`api_client.py`,
`auth_handler.py`, `browser_client.py`, `notification_handler.py`):

- **`captcha_handler.py`** — extracts and generalizes the existing
  `BrowserClient.solve_captcha()` into one routine that:
  - waits for `app-cloudflare-dialog` to appear (it shows up after Save,
    after reaching book-appointment, and after the review Continue — three
    different points, identical markup),
  - runs `uc_gui_click_captcha()` (this already does real mouse-path
    simulation, which is required since the Turnstile widget lives in a
    closed shadow root we can't reach via plain DOM),
  - clicks the modal's **Submit** button,
  - retries a bounded number of times before bailing with a clear error.
  This replaces the current ad-hoc `solve_captcha()` calls scattered through
  `auth_handler.py`.

- **`applicant_form_filler.py`** — implements the generic label-driven
  filler from §3. Used for the `your-details` step.

- **`otp_step.py`** — thin wrapper reusing `notification_handler.EmailClient`
  (already used for login OTP) to drive the **Generate OTP → wait for
  email → enter code → Verify** sequence on the booking flow's own OTP
  panel. Same polling/retry logic as login, factored out so both call sites
  share it instead of duplicating IMAP polling.

- **`calendar_slot_picker.py`** — two responsibilities:
  - `get_available_dates(browser)` — one JS query returning
    `[{date: "2026-06-24", label: "24"}]` for every `td.date-availiable`.
  - `get_available_times(browser)` — after a date is clicked, query the
    `table.ba-slot-table` rows that are *not* `d-none`/hidden, returning
    `[{time: "09:45", row_id: "d4"}]`.
  Both return plain data; the actual "ask the user" step is delegated to
  `frontend_bridge.py` (§5) so this module stays a pure DOM-reading utility.

- **`payment_handler.py`** — drives the Kalixa/PXP page (it's a normal
  server-rendered page, not embedded — simpler than the VFS Angular app).
  Receives card details from the dashboard via `frontend_bridge.py`, fills
  the plain `<input>` fields by `id` (`HolderName`, `CardNumber`,
  `ExpiryMonth`, `ExpiryYear`, `CardVerificationCode` — these are static
  server-rendered IDs, not Angular-generated, so they're safe to hard-code),
  and clicks `#payBtn`. No persistence of card data anywhere — not in
  `config.py`, not in the JSON logs, not echoed back to the dashboard except
  masked (`**** **** **** 1234`).

- **`booking_flow.py`** — the orchestrator. Given a `BrowserClient` already
  past the "slot available" check, drives steps 1–11 by branching on
  `browser.sb.get_current_url()` and on which key element is present,
  calling into the modules above, and calling `frontend_bridge.ask(...)`
  whenever a human decision is needed instead of `input()`.

## 5. Frontend architecture (local web dashboard)

The bot and the browser it drives (VFS in a real Chrome window via
SeleniumBase) are one process. The dashboard is a **second, small process
running alongside it on localhost only** — it never touches VFS directly, it
only talks to `booking_flow.py` through an in-memory bridge.

### 5.1 Stack

- **Flask**, single app, no build step, no JS framework — `static/index.html`
  + one `app.js` + one `style.css`. This keeps the new surface area small and
  matches the project's otherwise minimal dependency footprint
  (`requirements.txt` currently has no web framework at all, so this is a
  new, deliberately lightweight addition).
- **Server → browser push**: Server-Sent Events (`/events`, a single
  long-lived `GET` the page opens once via `EventSource`). Simpler than a
  full WebSocket for a one-user, mostly-server-initiated flow, and avoids
  pulling in `flask-socketio`/`eventlet` as a dependency.
- **Browser → server answers**: plain `POST /api/answer` with a small JSON
  body (`{"prompt_id": "...", "value": "2026-06-24"}`). The bridge matches the
  `prompt_id` to whichever call is currently blocked waiting for it.

### 5.2 `frontend_bridge.py` — the synchronization point

```
class FrontendBridge:
    def push_status(self, event: dict): ...          # fire-and-forget, for the live log panel
    def ask(self, prompt_type: str, payload: dict) -> dict: ...  # blocks the calling thread
```

- `ask()` generates a `prompt_id`, puts `{prompt_id, prompt_type, payload}`
  onto the SSE outbound queue (so the dashboard renders the right widget:
  date list / time list / review recap / card form), then blocks on a
  `queue.Queue(maxsize=1)` keyed to that `prompt_id` until `/api/answer`
  delivers a matching response.
- `push_status()` is used for everything read-only — current monitoring
  state, "slot found", "applicant form filled", "captcha solved", etc. — so
  the dashboard also doubles as a live status view, not just a prompt queue.
- One `FrontendBridge` instance is constructed in `main.py`/`booking_flow.py`
  and passed into the Flask app at startup (`app.config["bridge"] = bridge`)
  so both sides share the same object without global state.

### 5.3 Threading model

- The Selenium/booking automation keeps running in the **main thread**,
  exactly as today.
- Flask runs in a **daemon background thread**
  (`threading.Thread(target=app.run, kwargs={"host": "127.0.0.1", "port": 5050}, daemon=True).start()`)
  started once at the top of `main.py`, before `VfsScraper`/`booking_flow`
  begin.
- Because both sides only ever touch the bridge's queues (never each
  other's internals), no further locking is needed beyond what
  `queue.Queue` already gives for free.

### 5.4 Dashboard pages/sections (single page, sections shown/hidden by prompt type)

1. **Status feed** — scrolling log of `push_status` events (monitoring
   started, slot found, form filled, captcha solved, OTP verified, etc.).
   Always visible.
2. **Date picker** — rendered as a button per available date when a
   `select_date` prompt arrives.
3. **Time picker** — period filter (`All`/`Morning`/`Afternoon`/`Evening`)
   plus a button per available time slot, for `select_time` prompts.
4. **Review & confirm** — applicant name, country, appointment date/time,
   fee total, with **Confirm** / **Cancel** buttons for `confirm_review`
   prompts. Cancel routes back to "go back" in the VFS flow rather than
   killing the bot.
5. **Payment form** — Holder Name / Card Number / Expiry Month / Expiry Year
   / CVV inputs + Submit, for the `enter_card_details` prompt. Submitted over
   `POST /api/answer` like everything else; the bridge hands the values
   straight to `payment_handler.py` and they're discarded after use.

### 5.5 Security notes specific to the dashboard

- Bind **only** to `127.0.0.1`, never `0.0.0.0` — this app will, at the
  payment step, briefly hold a real card number in memory and in an HTTP
  request body. There is no authentication layer, which is acceptable *only*
  because it's loopback-only and single-user; if this is ever exposed beyond
  localhost it needs auth added first.
- No request logging of the `/api/answer` body when `prompt_type ==
  "enter_card_details"` — Flask's default access log would otherwise happen
  to leave card data in a log file.
- The card form's `POST` is over plain HTTP (localhost), which is normal for
  loopback-only local tools but worth stating explicitly rather than leaving
  implicit.

## 6. Config additions

`config.py` currently has `COUNTRY_CONFIG` (route/visa params) and a
hardcoded applicant blob duplicated between `browser_client.py`'s
`call_add_applicant` and `waitlist.json`. Consolidate into one
`APPLICANT_CONFIG` dict keyed by account email, holding exactly the fields
the generic filler needs (name, DOB, passport details, nationality, contact
number, email) — single source of truth, no more drift between the two
existing copies. Card details are explicitly **not** added here — they only
ever exist transiently, entered through the dashboard at payment time.

## 7. Orchestration / state machine

`booking_flow.run(browser, auth_token, bridge)` becomes a small loop:

```
while not done:
    url = browser.sb.get_current_url()
    if "your-details" in url and otp_panel_visible(): step = OTP
    elif "your-details" in url: step = APPLICANT_FORM
    elif "book-appointment" in url: step = DATE_TIME_SELECTION
    elif "review" in url: step = REVIEW_AND_CONSENT
    elif "kalixa.com" in url: step = PAYMENT
    else: step = UNKNOWN  # log full page state, push_status to dashboard, stop for manual inspection
    dispatch(step)
    captcha_handler.solve_if_present(browser)  # checked after every step
```

Each step handler returns once it has clicked whatever "Continue"/"Save"
moves the flow forward, so the loop naturally re-evaluates the URL. Unknown
states stop the bot and dump the page (and push a status event so the
dashboard shows it, instead of failing silently in a terminal) rather than
guessing — booking a wrong slot or double-submitting a payment is much worse
than a bot that pauses.

## 8. Human decision points (now all in the dashboard, none in the terminal)

1. **Date** — `frontend_bridge.ask("select_date", {...dates})`, rendered as
   the date picker section.
2. **Time** — `ask("select_time", {...times})`, rendered as the time picker.
3. **Review confirmation** — `ask("confirm_review", {...recap})`; only on a
   `"confirmed": true` answer does the bot tick T&Cs and proceed to payment.
4. **Card details** — `ask("enter_card_details", {})`, answered from the
   dashboard's payment form.

Everything else (captcha solving, OTP retrieval, generic form filling,
navigating disclaimers) stays fully automatic — those are the parts a human
can't do faster or more reliably than the bot anyway, and routing them
through the dashboard would just add latency for no benefit.

## 9. Logging & error handling

- Extend `log_appointment_data` call sites with a `"type": "booking_step"`
  entry per step transition, so a failed run can be replayed/debugged from
  the JSON log alone — and mirror the same events to `push_status` so the
  dashboard's status feed matches the log file in real time.
- Captcha failures: retry up to 3x with fresh mouse-path attempts, then stop
  and notify (reuse `SMSNotifier`, and push a status event) rather than
  looping forever.
- OTP failures: VFS itself caps attempts at 3 ("You have left 3 attempts of
  generating new OTP... After crossing limit you will be logged off") — the
  bot must track this counter locally and stop before VFS forcibly logs out,
  not after.
- If the selected date/time becomes unavailable between selection and
  submit (race with other applicants), detect VFS's own error messaging and
  fall back to re-prompting the date list through the dashboard rather than
  crashing.

## 10. Security notes

- Card data lives only in local Python variables for the duration of the
  payment step, and in the dashboard's in-page form state until submitted;
  never written to `logs/*.json`, never passed to `notification_handler`,
  never logged by Flask (see §5.5).
- `config.py` already has plaintext secrets unrelated to this change
  (Twilio token, email password) — out of scope here, but worth fixing
  separately since this plan adds one more sensitive-data code path to a
  file/process that isn't currently isolated in any way.

## 11. Implementation phases

1. **Captcha + applicant form** — `captcha_handler.py`,
   `applicant_form_filler.py`, config consolidation. Verify against PRT
   (most complex form) and NLD (shortest form) without touching anything
   past Save. No dashboard needed yet — this phase has no human decision
   points.
2. **Dashboard skeleton** — `frontend_bridge.py`, the Flask app, the
   background thread wiring in `main.py`, and the status-feed page only
   (`push_status`, no prompts yet). Confirms the threading/SSE plumbing
   works before any real prompt depends on it.
3. **OTP step** — `otp_step.py`, wired into the flow right after the
   applicant summary's Continue; status events shown on the dashboard.
4. **Date/time selection** — `calendar_slot_picker.py` + the date/time
   dashboard sections and their `ask()` calls; verify the calendar's
   "available" detection against a real future month with mixed
   available/unavailable days.
5. **Review & consent** — recap rendering on the dashboard, checkbox
   handling, Pay Online.
6. **Payment** — `payment_handler.py` against the Kalixa form, plus the
   dashboard's card-entry section; test with the disclaimer/captcha hop in
   between.
7. **`booking_flow.py` orchestrator** wiring all of the above behind the
   URL-based dispatch loop, replacing the current "stop after slot found"
   ending in `main.py`.
8. **End-to-end dry run** on a real low-stakes booking (e.g. a slot you can
   afford to actually pay for) before trusting it unattended.

Each phase is independently testable before moving to the next — there's no
value in wiring the whole chain (or the whole dashboard) before confirming
the captcha/form-fill pieces actually work against the real Angular Material
widgets, which behave differently from plain HTML forms.
