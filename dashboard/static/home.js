const overlay = document.getElementById("result-overlay");
const resultBox = document.getElementById("result-box");

// Rotated while a check is in flight so the wait doesn't feel dead — the
// real check takes anywhere from ~20s to over a minute (login + OTP wait +
// navigating VFS's form), so the message keeps changing the whole time.
const LOADING_MESSAGES = [
  "Bot is logging in to VFS...",
  "Bot is working...",
  "Checking appointment centres...",
  "Bot is doing its best — almost there...",
  "Still checking, hang tight...",
  "Talking to VFS Global's servers...",
];

let loadingTimer = null;

function stopLoading() {
  if (loadingTimer) {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }
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
  wrap.innerHTML = '<div class="spinner"></div>';

  const heading = document.createElement("p");
  heading.textContent = `Checking ${name} (London, Tourism)`;

  const status = document.createElement("p");
  status.className = "muted";

  wrap.appendChild(heading);
  wrap.appendChild(status);
  resultBox.appendChild(wrap);

  stopLoading();
  let i = 0;
  const tick = () => {
    status.textContent = LOADING_MESSAGES[i % LOADING_MESSAGES.length];
    i++;
  };
  tick();
  loadingTimer = setInterval(tick, 2500);
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

  if (data.status === "slots_available") {
    heading.className = "result-success";
    heading.textContent = `Slot available — ${name}`;
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
    heading.textContent = `No slots available — ${name}`;
    resultBox.appendChild(heading);

    const p = document.createElement("p");
    p.className = "muted";
    p.textContent =
      data.slot_details ||
      "We are sorry but no appointment slots are currently available. New slots open at regular intervals, please try again later.";
    resultBox.appendChild(p);
  } else {
    heading.className = "result-error";
    heading.textContent = "Couldn't complete the check";
    resultBox.appendChild(heading);

    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = data.message || "Something went wrong. Please try again.";
    resultBox.appendChild(p);
  }
}

function startCheck(card) {
  const country = card.dataset.country;
  const name = card.dataset.name;

  if (card.classList.contains("checking")) return;
  card.classList.add("checking");
  card.disabled = true;
  const cta = card.querySelector(".country-cta");
  const originalCta = cta.textContent;
  cta.textContent = "Checking…";

  showLoading(name);

  fetch(`/api/check/${country}`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => renderResult(name, data))
    .catch(() => renderResult(name, { status: "error", message: "Network error — please try again." }))
    .finally(() => {
      card.classList.remove("checking");
      card.disabled = false;
      cta.textContent = originalCta;
    });
}

document.querySelectorAll(".country-card").forEach((card) => {
  card.addEventListener("click", () => startCheck(card));
});

overlay.addEventListener("click", (e) => {
  if (e.target === overlay) closeOverlay();
});
