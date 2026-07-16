// How often this page re-polls the cached status, purely a read of
// slot_status_cache.py's in-memory cache, never triggers a live check.
const POLL_INTERVAL_MS = 30_000;

function timeAgo(isoString) {
  if (!isoString) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(isoString).getTime()) / 1000));
  if (seconds < 60) return "checked just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `checked ${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return `checked ${hours}h ago`;
}

const COMBO_BADGE = {
  slots_available: ["status-success", "Slot available"],
  no_slots: ["status-empty", "No slots"],
};

// Builds the status block for one country card from the same shape
// slot_status_cache.get_all_status() / GET /api/status returns:
// { status: "slots_available" | "no_slots" | "error", centre, appt_cat,
//   combos: [{ sub_cat, status, slot_details }, ...], message, checked_at }.
// combos has one entry per sub-category checked (just "Tourism" when the
// mission offers it, every sub-category it does offer otherwise) — see
// slot_check_service.check_country_slot. Pure discovery failures (couldn't
// log in, no centre/category found at all) have no combos, just "message".
function renderCountryStatus(container, s) {
  if (!s) {
    // No result cached yet for this country (first check still in
    // progress). Leave it blank, just the flag/code/name from the card
    // shell, no placeholder text.
    container.innerHTML = "";
    return;
  }

  if (!s.combos || !s.combos.length) {
    container.innerHTML = `
      <span class="status-badge status-error">Check failed</span>
      <p class="status-detail">${s.message || "Will retry on the next pass."}</p>
      <p class="status-time">${timeAgo(s.checked_at)}</p>
    `;
    return;
  }

  const rows = s.combos
    .map((c) => {
      const [badgeClass, badgeText] = COMBO_BADGE[c.status] || ["status-error", "Check failed"];
      const detail = c.slot_details ? `<p class="status-detail">${c.slot_details}</p>` : "";
      return `
        <div class="combo-row">
          <span class="combo-label">${c.sub_cat}:</span>
          <span class="status-badge ${badgeClass}">${badgeText}</span>
        </div>
        ${detail}
      `;
    })
    .join("");

  container.innerHTML = `${rows}<p class="status-time">${timeAgo(s.checked_at)}</p>`;
}

function refreshStatus() {
  fetch("/api/status")
    .then((r) => r.json())
    .then((statusByCountry) => {
      document.querySelectorAll(".country-card").forEach((card) => {
        const country = card.dataset.country;
        const container = card.querySelector(".country-status");
        if (container) renderCountryStatus(container, statusByCountry[country]);
      });
    })
    .catch(() => {});
}

refreshStatus();
setInterval(refreshStatus, POLL_INTERVAL_MS);

// ── Start Bot: kicks off slot_status_cache.py's background loop; it
// doesn't run on its own until this is clicked. The modal loader (spinner +
// stop button) only ever covers the very first cycle — slot_status_cache.py
// then keeps alternating "checking" / "resting" forever, which #bot-control
// reflects via a continuous /api/bot-status poll. Wrapped in an IIFE:
// coming-soon.js (loaded on this same page) already declares top-level
// `overlay`/`resultBox`/`closeOverlay`, and classic <script> tags share one
// global scope, so these names can't be redeclared at the top level here.
(function () {
const overlay = document.getElementById("result-overlay");
const resultBox = document.getElementById("result-box");
const control = document.getElementById("bot-control");

const STOP_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6 6 18"/></svg>';

const POLL_BOT_STATUS_MS = 4000;

let loaderPollTimer = null;
let statusPollTimer = null;
let countdownTimer = null;
let countdownRemaining = 0;
let logEventSource = null;
// Dedupe — avoid tearing down and rebuilding #bot-control (and its
// 1-second countdown ticker) on every single poll when nothing changed.
let renderedState = null;

function clearCountdown() {
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function bindStartButton(btn) {
  btn.addEventListener("click", () => {
    btn.disabled = true;
    btn.textContent = "Starting…";
    // This mutates the button directly instead of going through
    // renderStartButton(), so the dedupe cache no longer matches what's
    // actually on screen — invalidate it, otherwise a later
    // renderStartButton() call (e.g. from the stop button) would see
    // renderedState === "idle" and wrongly skip rebuilding this stale
    // disabled button back to a clickable one.
    renderedState = null;
    showChecking();
    fetch("/api/start-bot", { method: "POST" })
      .then(() => pollUntilFirstCycle())
      .catch(() => {
        closeChecking();
        renderStartButton();
      });
  });
}

function renderStartButton() {
  if (!control || renderedState === "idle") return;
  renderedState = "idle";
  clearCountdown();
  control.innerHTML = '<button id="start-bot-btn" class="hero-cta">Start Bot</button>';
  bindStartButton(document.getElementById("start-bot-btn"));
}

function renderChecking() {
  if (!control || renderedState === "checking") return;
  renderedState = "checking";
  clearCountdown();
  control.innerHTML = '<span class="hero-cta hero-cta--running">Checking all countries…</span>';
}

function updateCountdownText() {
  const el = document.getElementById("rest-countdown");
  if (el) el.textContent = `Bot will start in ${countdownRemaining}s`;
}

// next_check_in_seconds comes straight from slot_status_cache's
// REFRESH_INTERVAL_SECONDS (set the moment a cycle finishes), so the very
// first render of this state always starts the countdown at that
// configured value, not some arbitrary number.
function renderResting(seconds) {
  if (!control) return;
  countdownRemaining = seconds;
  if (renderedState !== "resting") {
    renderedState = "resting";
    clearCountdown();
    control.innerHTML =
      '<div class="bot-resting">' +
      '<span class="hero-cta hero-cta--resting">Restarting</span>' +
      '<p class="bot-resting-timer" id="rest-countdown"></p>' +
      "</div>";
    countdownTimer = setInterval(() => {
      countdownRemaining = Math.max(0, countdownRemaining - 1);
      updateCountdownText();
    }, 1000);
  }
  updateCountdownText();
}

// Single source of truth for #bot-control's appearance once the bot is
// past its first cycle (before that, the modal loader owns it instead).
function renderBotControl(data) {
  if (!data.running) {
    renderStartButton();
  } else if (data.checking) {
    renderChecking();
  } else {
    renderResting(data.next_check_in_seconds ?? 0);
  }
}

function pollStatusForever() {
  fetch("/api/bot-status")
    .then((r) => r.json())
    .then((data) => {
      if (data.running && !data.first_cycle_done) return; // modal loader owns this phase
      renderBotControl(data);
    })
    .catch(() => {})
    .finally(() => {
      statusPollTimer = setTimeout(pollStatusForever, POLL_BOT_STATUS_MS);
    });
}

// ── Modal loader — first cycle only ──────────────────────────────────────

function closeChecking() {
  if (loaderPollTimer) {
    clearTimeout(loaderPollTimer);
    loaderPollTimer = null;
  }
  if (logEventSource) {
    logEventSource.close();
    logEventSource = null;
  }
  overlay.hidden = true;
  overlay.dataset.lock = "false";
  resultBox.innerHTML = "";
  resultBox.classList.remove("boxed");
}

// Only the stop button calls this — clicking the backdrop or anywhere else
// on the loader must NOT dismiss it (see coming-soon.js's lock check too).
function stopBotAndClose() {
  fetch("/api/stop-bot", { method: "POST" }).catch(() => {});
  closeChecking();
  renderStartButton();
}

function _openLogStream(logPane) {
  if (logEventSource) {
    logEventSource.close();
    logEventSource = null;
  }
  const es = new EventSource("/api/log-stream");
  logEventSource = es;
  es.onmessage = (e) => {
    try {
      const { line } = JSON.parse(e.data);
      if (!line || !line.trim()) return;
      const el = document.createElement("div");
      el.className = "log-line";
      el.textContent = line;
      logPane.appendChild(el);
      logPane.scrollTop = logPane.scrollHeight;
    } catch (_) {}
  };
  es.onerror = () => {};
}

function showChecking() {
  overlay.hidden = false;
  overlay.dataset.lock = "true";
  resultBox.innerHTML = "";
  resultBox.classList.remove("boxed");

  const wrap = document.createElement("div");
  wrap.className = "result-loading";

  const heading = document.createElement("p");
  heading.className = "loading-title";
  heading.textContent = "Checking all countries";

  const spinner = document.createElement("span");
  spinner.className = "loader";

  const logPane = document.createElement("div");
  logPane.className = "log-pane";

  const stopBtn = document.createElement("button");
  stopBtn.className = "loader-stop";
  stopBtn.type = "button";
  stopBtn.title = "Stop the bot";
  stopBtn.setAttribute("aria-label", "Stop the bot");
  stopBtn.innerHTML = STOP_ICON;
  stopBtn.onclick = stopBotAndClose;

  wrap.appendChild(heading);
  wrap.appendChild(spinner);
  wrap.appendChild(logPane);
  wrap.appendChild(stopBtn);
  resultBox.appendChild(wrap);

  _openLogStream(logPane);
}

function pollUntilFirstCycle() {
  fetch("/api/bot-status")
    .then((r) => r.json())
    .then((data) => {
      if (!data.running) {
        // Stopped from elsewhere (another tab, or the backend exiting).
        closeChecking();
        renderStartButton();
        return;
      }
      if (data.first_cycle_done) {
        closeChecking();
        refreshStatus();
        renderBotControl(data);
        return;
      }
      loaderPollTimer = setTimeout(pollUntilFirstCycle, POLL_BOT_STATUS_MS);
    })
    .catch(() => {
      loaderPollTimer = setTimeout(pollUntilFirstCycle, POLL_BOT_STATUS_MS);
    });
}

// ── Boot: figure out which of the above applies right now ───────────────

fetch("/api/bot-status")
  .then((r) => r.json())
  .then((data) => {
    if (data.running && !data.first_cycle_done) {
      showChecking();
      pollUntilFirstCycle();
    } else {
      renderBotControl(data);
    }
  })
  .catch(() => {})
  .finally(() => {
    statusPollTimer = setTimeout(pollStatusForever, POLL_BOT_STATUS_MS);
  });
})();
