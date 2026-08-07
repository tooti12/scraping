// Shared by home.html and availability.html, gates the Booking Console
// link behind a "coming soon" modal until it has an auth layer in front
// of it (see dashboard/app.py's docstring on why /booking can't go public
// yet).
const overlay = document.getElementById("result-overlay");
const resultBox = document.getElementById("result-box");

const ICONS = {
  empty:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9 9h.01M15 9h.01M8.5 15a5 5 0 0 1 7 0"/></svg>',
};

function closeOverlay() {
  overlay.hidden = true;
  resultBox.innerHTML = "";
  resultBox.classList.remove("boxed");
}

function showComingSoon() {
  // availability.js sets this while its "checking all countries" loader is
  // up, since that loader must not be dismissed (or replaced) by anything
  // other than its own stop button.
  if (overlay.dataset.lock === "true") return;
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
  heading.appendChild(document.createTextNode("Booking Console: coming soon"));
  resultBox.appendChild(heading);

  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = "We're still locking this down for safe use. For now, check current VFS availability instead.";
  resultBox.appendChild(p);

  const actions = document.createElement("div");
  actions.className = "result-actions";
  const closeAction = document.createElement("button");
  closeAction.textContent = "Close";
  closeAction.onclick = closeOverlay;
  actions.appendChild(closeAction);
  resultBox.appendChild(actions);
}

overlay.addEventListener("click", (e) => {
  if (e.target === overlay && overlay.dataset.lock !== "true") closeOverlay();
});

document.querySelectorAll(".js-coming-soon").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    showComingSoon();
  });
});
