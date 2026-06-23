const overlay = document.getElementById("result-overlay");
const resultBox = document.getElementById("result-box");

const ICONS = {
  check:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.5 2.5L16 9.5"/></svg>',
  empty:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9 9h.01M15 9h.01M8.5 15a5 5 0 0 1 7 0"/></svg>',
  error:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>',
};

// Mirrors the real event names slot_check_service.py emits (via
// dashboard/app.py's per-check SSE stream) — these are the *actual*
// backend milestones, not a guess, so the loader text always matches what
// the bot is doing right now instead of drifting out of sync with it.
const STATUS_INFO = {
  connecting: { pct: 8, text: "Connecting to VFS Global..." },
  logging_in: { pct: 20, text: "Logging in..." },
  awaiting_otp: { pct: 38, text: "Waiting for OTP verification..." },
  verifying_otp: { pct: 55, text: "Verifying OTP..." },
  session_ready: { pct: 68, text: "Session established..." },
  checking_centres: { pct: 80, text: "Checking appointment centres..." },
  finalizing: { pct: 92, text: "Finalizing result..." },
};

let progressFillEl = null;
let loadingStatusEl = null;
let driftTimer = null;
let currentPct = 0;
let driftCap = 100;
let activeEventSource = null;

function clearDrift() {
  if (driftTimer) {
    clearInterval(driftTimer);
    driftTimer = null;
  }
}

function closeEventSource() {
  if (activeEventSource) {
    activeEventSource.close();
    activeEventSource = null;
  }
}

function setProgress(pct, text) {
  currentPct = pct;
  if (progressFillEl) progressFillEl.style.width = pct + "%";
  if (text != null && loadingStatusEl) loadingStatusEl.textContent = text;
}

// Lets the bar creep slightly between real backend events (e.g. during the
// ~20s OTP-wait window) so it doesn't look frozen, but it never overtakes
// the next real checkpoint — the text only ever changes on a real event.
function startDrift(cap) {
  clearDrift();
  driftCap = cap;
  driftTimer = setInterval(() => {
    if (currentPct < driftCap - 0.5) {
      currentPct = Math.min(currentPct + 0.4, driftCap);
      if (progressFillEl) progressFillEl.style.width = currentPct + "%";
    }
  }, 900);
}

function stopLoading() {
  clearDrift();
  closeEventSource();
}

function closeOverlay() {
  stopLoading();
  overlay.hidden = true;
  resultBox.innerHTML = "";
}

function showLoading(name) {
  overlay.hidden = false;
  resultBox.innerHTML = "";

  const wrap = document.createElement("div");
  wrap.className = "result-loading";

  const heading = document.createElement("p");
  heading.className = "loading-title";
  heading.textContent = `Checking ${name} (London, Tourism)`;

  const track = document.createElement("div");
  track.className = "progress-track";
  const fill = document.createElement("div");
  fill.className = "progress-fill";
  track.appendChild(fill);
  progressFillEl = fill;

  const status = document.createElement("p");
  status.className = "loading-status";
  loadingStatusEl = status;

  const hint = document.createElement("p");
  hint.className = "loading-hint";
  hint.textContent = "Live checks can take up to a minute — sit tight.";

  wrap.appendChild(heading);
  wrap.appendChild(track);
  wrap.appendChild(status);
  wrap.appendChild(hint);
  resultBox.appendChild(wrap);

  setProgress(4, "Connecting to VFS Global...");
  startDrift(14);
}

function infoRow(dl, label, value) {
  if (!value) return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.appendChild(dt);
  dl.appendChild(dd);
}

function renderResult(name, data) {
  stopLoading();
  resultBox.innerHTML = "";

  const closeBtn = document.createElement("button");
  closeBtn.className = "modal-close";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "×";
  closeBtn.onclick = closeOverlay;
  resultBox.appendChild(closeBtn);

  const heading = document.createElement("h2");
  const icon = document.createElement("span");
  icon.className = "result-icon";

  if (data.status === "slots_available") {
    heading.className = "result-success";
    icon.innerHTML = ICONS.check;
    heading.appendChild(icon);
    heading.appendChild(document.createTextNode(`Slot available — ${name}`));
    resultBox.appendChild(heading);

    const dl = document.createElement("dl");
    dl.className = "info-grid";
    infoRow(dl, "Centre", data.centre);
    infoRow(dl, "Category", data.appt_cat);
    infoRow(dl, "Sub-category", data.sub_cat);
    infoRow(dl, "Slot", data.slot_details);
    resultBox.appendChild(dl);
  } else if (data.status === "no_slots") {
    heading.className = "result-empty";
    icon.innerHTML = ICONS.empty;
    heading.appendChild(icon);
    heading.appendChild(document.createTextNode(`No slots available — ${name}`));
    resultBox.appendChild(heading);

    const p = document.createElement("p");
    p.className = "muted";
    p.textContent =
      data.slot_details ||
      "We are sorry but no appointment slots are currently available. New slots open at regular intervals, please try again later.";
    resultBox.appendChild(p);
  } else {
    heading.className = "result-error";
    icon.innerHTML = ICONS.error;
    heading.appendChild(icon);
    heading.appendChild(document.createTextNode("Couldn't complete the check"));
    resultBox.appendChild(heading);

    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = data.message || "Something went wrong. Please try again.";
    resultBox.appendChild(p);
  }

  const actions = document.createElement("div");
  actions.className = "result-actions";
  const closeAction = document.createElement("button");
  closeAction.textContent = "Close";
  closeAction.onclick = closeOverlay;
  actions.appendChild(closeAction);
  resultBox.appendChild(actions);
}

// Subscribes to this specific check's SSE stream and reflects real backend
// milestones onto the loader. `finish` re-enables the country card once the
// check settles, however it settles (result or dropped connection).
function streamCheck(checkId, name, finish) {
  const es = new EventSource(`/api/check-stream/${checkId}`);
  activeEventSource = es;
  let settled = false;

  es.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "status") {
      const info = STATUS_INFO[msg.event];
      if (info) {
        setProgress(Math.max(currentPct, info.pct), info.text);
        startDrift(Math.min(info.pct + 6, 96));
      }
    } else if (msg.type === "result") {
      settled = true;
      activeEventSource = null;
      es.close();
      renderResult(name, msg.data);
      finish();
    }
  };

  es.onerror = () => {
    if (settled) return;
    settled = true;
    activeEventSource = null;
    es.close();
    renderResult(name, { status: "error", message: "Lost connection while checking. Please try again." });
    finish();
  };
}

function startCheck(card) {
  const country = card.dataset.country;
  const name = card.dataset.name;

  if (card.classList.contains("checking")) return;
  card.classList.add("checking");
  card.disabled = true;
  const cta = card.querySelector(".country-cta");
  const originalCta = cta.innerHTML;
  cta.textContent = "Checking…";

  function finish() {
    card.classList.remove("checking");
    card.disabled = false;
    cta.innerHTML = originalCta;
  }

  showLoading(name);

  fetch(`/api/check/${country}`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (!data.check_id) {
        renderResult(name, data);
        finish();
        return;
      }
      streamCheck(data.check_id, name, finish);
    })
    .catch(() => {
      renderResult(name, { status: "error", message: "Network error — please try again." });
      finish();
    });
}

document.querySelectorAll(".country-card").forEach((card) => {
  card.addEventListener("click", () => startCheck(card));
});

overlay.addEventListener("click", (e) => {
  if (e.target === overlay) closeOverlay();
});

// ── Booking console — gated behind "coming soon" until it has auth ──────

function showComingSoon() {
  stopLoading();
  overlay.hidden = false;
  resultBox.innerHTML = "";

  const closeBtn = document.createElement("button");
  closeBtn.className = "modal-close";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "×";
  closeBtn.onclick = closeOverlay;
  resultBox.appendChild(closeBtn);

  const heading = document.createElement("h2");
  heading.className = "result-empty";
  const icon = document.createElement("span");
  icon.className = "result-icon";
  icon.innerHTML = ICONS.empty;
  heading.appendChild(icon);
  heading.appendChild(document.createTextNode("Booking Console — coming soon"));
  resultBox.appendChild(heading);

  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = "We're still locking this down for safe use. For now, use the slot checker above to see live VFS availability.";
  resultBox.appendChild(p);

  const actions = document.createElement("div");
  actions.className = "result-actions";
  const closeAction = document.createElement("button");
  closeAction.textContent = "Close";
  closeAction.onclick = closeOverlay;
  actions.appendChild(closeAction);
  resultBox.appendChild(actions);
}

document.querySelectorAll(".js-coming-soon").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    showComingSoon();
  });
});
