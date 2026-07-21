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

function renderCountryStatus(container, s) {
  if (!s) {
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

// ── Bot status badge ─────────────────────────────────────────────────────
// Shows/hides the eyebrow banner when bot state changes. Bot can only be
// started or stopped by an admin from /admin — no controls on this page.
(function () {
  const eyebrow = document.getElementById("bot-eyebrow");
  const eyebrowText = document.getElementById("bot-eyebrow-text");
  if (!eyebrow) return;

  function pollBotStatus() {
    fetch("/api/bot-status")
      .then((r) => r.json())
      .then((data) => {
        if (data.running) {
          eyebrow.hidden = false;
          eyebrowText.textContent = data.checking
            ? "Bot is checking slots now — results updating soon"
            : "Bot is monitoring in background";
        } else {
          eyebrow.hidden = true;
        }
      })
      .catch(() => {});
  }

  pollBotStatus();
  setInterval(pollBotStatus, 10_000);
})();
