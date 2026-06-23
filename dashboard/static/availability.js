// How often this page re-polls the cached status — purely a read of
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

// Builds the status block for one country card from the same shape
// slot_status_cache.get_all_status() / GET /api/status returns:
// { status: "slots_available" | "no_slots" | "error", centre, appt_cat,
//   sub_cat, slot_details, message, checked_at } — centre/appt_cat/sub_cat
// are plain strings (see slot_check_service.check_country_slot), not
// {id, text} objects.
function renderCountryStatus(container, s) {
  if (!s) {
    // No result cached yet for this country (first check still in
    // progress) — leave it blank, just the flag/code/name from the card
    // shell, no placeholder text.
    container.innerHTML = "";
    return;
  }

  let badgeClass = "status-error";
  let badgeText = "Check failed";
  let detail = s.message || "Will retry on the next pass.";

  if (s.status === "slots_available") {
    badgeClass = "status-success";
    badgeText = "Slot available";
    detail = s.slot_details || [s.centre, s.appt_cat, s.sub_cat].filter(Boolean).join(" · ");
  } else if (s.status === "no_slots") {
    badgeClass = "status-empty";
    badgeText = "No slots";
    detail = s.slot_details || "No appointment slots are currently available.";
  }

  container.innerHTML = `
    <span class="status-badge ${badgeClass}">${badgeText}</span>
    <p class="status-detail">${detail}</p>
    <p class="status-time">${timeAgo(s.checked_at)}</p>
  `;
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

// ── Start Bot — kicks off slot_status_cache.py's background loop; it
// doesn't run on its own until this is clicked. ──────────────────────────

function showBotRunning() {
  const control = document.getElementById("bot-control");
  if (!control) return;
  control.innerHTML = '<span id="bot-status-text" class="hero-cta hero-cta--running">Bot is running</span>';
}

const startBotBtn = document.getElementById("start-bot-btn");
if (startBotBtn) {
  startBotBtn.addEventListener("click", () => {
    startBotBtn.disabled = true;
    startBotBtn.textContent = "Starting…";
    fetch("/api/start-bot", { method: "POST" })
      .then((r) => r.json())
      .then(() => showBotRunning())
      .catch(() => {
        startBotBtn.disabled = false;
        startBotBtn.textContent = "Start Bot";
      });
  });
}
