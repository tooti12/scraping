const overlay = document.getElementById("result-overlay");
const resultBox = document.getElementById("result-box");

const ICONS = {
  check:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.5 2.5L16 9.5"/></svg>',
  empty:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9 9h.01M15 9h.01M8.5 15a5 5 0 0 1 7 0"/></svg>',
  error:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>',
  cross:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
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

const RING_CIRCUMFERENCE = 213.6; // 2 * PI * r(34), matches home.css

let loaderArcEl = null;
let loadingStatusEl = null;
let driftTimer = null;
let currentPct = 0;
let driftCap = 100;
let activeEventSource = null;
let activeCheckId = null;
let isLoading = false;

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
  if (loaderArcEl) {
    loaderArcEl.style.strokeDashoffset = RING_CIRCUMFERENCE * (1 - pct / 100);
  }
  if (text != null && loadingStatusEl) loadingStatusEl.textContent = text;
}

// Lets the ring creep slightly between real backend events (e.g. during the
// ~20s OTP-wait window) so it doesn't look frozen, but it never overtakes
// the next real checkpoint — the text only ever changes on a real event.
function startDrift(cap) {
  clearDrift();
  driftCap = cap;
  driftTimer = setInterval(() => {
    if (currentPct < driftCap - 0.5) {
      setProgress(Math.min(currentPct + 0.4, driftCap), null);
    }
  }, 900);
}

function stopLoading() {
  clearDrift();
  closeEventSource();
  isLoading = false;
  activeCheckId = null;
}

function closeOverlay() {
  stopLoading();
  overlay.hidden = true;
  resultBox.innerHTML = "";
  resultBox.classList.remove("boxed");
}

// Cancels the in-flight backend check (force-quits its Chrome session via
// /api/check-cancel) and immediately dismisses the overlay — used by both
// the loader's hover-cross and a click anywhere else on screen.
function cancelAndClose() {
  if (activeCheckId) {
    fetch(`/api/check-cancel/${activeCheckId}`, { method: "POST" }).catch(() => {});
  }
  closeOverlay();
}

function showLoading(name) {
  overlay.hidden = false;
  resultBox.innerHTML = "";
  resultBox.classList.remove("boxed");
  isLoading = true;

  const wrap = document.createElement("div");
  wrap.className = "result-loading";

  const heading = document.createElement("p");
  heading.className = "loading-title";
  heading.textContent = `Checking ${name} (London, Tourism)`;

  const circle = document.createElement("div");
  circle.className = "loader-circle";
  circle.title = "Cancel check";
  circle.innerHTML = `
    <svg class="loader-ring" viewBox="0 0 80 80">
      <circle class="loader-track" cx="40" cy="40" r="34"></circle>
      <circle class="loader-arc" cx="40" cy="40" r="34"></circle>
    </svg>
    <span class="loader-cancel">${ICONS.cross}</span>
  `;
  loaderArcEl = circle.querySelector(".loader-arc");

  const status = document.createElement("p");
  status.className = "loading-status";
  loadingStatusEl = status;

  const hint = document.createElement("p");
  hint.className = "loading-hint";
  hint.textContent = "Hover the circle (or click anywhere) to cancel.";

  wrap.appendChild(heading);
  wrap.appendChild(circle);
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
  resultBox.classList.add("boxed");

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
  } else if (data.status === "cancelled") {
    heading.className = "result-empty";
    icon.innerHTML = ICONS.empty;
    heading.appendChild(icon);
    heading.appendChild(document.createTextNode("Check cancelled"));
    resultBox.appendChild(heading);

    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "You cancelled this check before it finished.";
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
// check settles, however it settles (result, cancel, or dropped connection).
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
      activeCheckId = data.check_id;
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

// While loading: a click ANYWHERE (the circle, its cancel cross, or the bare
// backdrop) cancels the in-flight check and dismisses the overlay. Once a
// result is showing (boxed card), only a click on the dim backdrop itself
// closes it — clicking inside the result card (e.g. its action buttons)
// must not.
overlay.addEventListener("click", (e) => {
  if (isLoading) {
    cancelAndClose();
    return;
  }
  if (e.target === overlay) closeOverlay();
});

// ── Booking console — gated behind "coming soon" until it has auth ──────

function showComingSoon() {
  stopLoading();
  overlay.hidden = false;
  resultBox.innerHTML = "";
  resultBox.classList.add("boxed");

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
