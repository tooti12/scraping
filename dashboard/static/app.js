const promptContainer = document.getElementById("prompt-container");
const statusList = document.getElementById("status-list");

function logStatus(event) {
  const li = document.createElement("li");
  const time = new Date(event.ts * 1000).toLocaleTimeString();
  li.textContent = `[${time}] ${event.event}` + (event.payload && Object.keys(event.payload).length
    ? ` - ${JSON.stringify(event.payload)}`
    : "");
  statusList.prepend(li);
}

function clearPrompt() {
  promptContainer.innerHTML = '<p class="muted">No action needed right now - waiting on the bot.</p>';
}

function submitAnswer(promptId, value) {
  fetch("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_id: promptId, value: value }),
  }).then(() => clearPrompt());
}

function renderDatePrompt(promptId, payload) {
  const box = document.createElement("div");
  box.className = "prompt-box";
  box.innerHTML = "<h3>Select an appointment date</h3>";
  (payload.dates || []).forEach((d) => {
    const btn = document.createElement("button");
    btn.textContent = `${d.label} (${d.date})`;
    btn.onclick = () => submitAnswer(promptId, d.date);
    box.appendChild(btn);
  });
  if (!payload.dates || !payload.dates.length) {
    box.appendChild(document.createTextNode("No available dates were found."));
  }
  return box;
}

function periodOf(time) {
  const hour = parseInt(time.split(":")[0], 10);
  if (hour < 12) return "Morning";
  if (hour < 17) return "Afternoon";
  return "Evening";
}

function renderTimePrompt(promptId, payload) {
  const box = document.createElement("div");
  box.className = "prompt-box";
  const heading = document.createElement("h3");
  heading.textContent = `Select an appointment time${payload.date ? " (" + payload.date + ")" : ""}`;
  box.appendChild(heading);

  const filterSelect = document.createElement("select");
  ["All", "Morning", "Afternoon", "Evening"].forEach((period) => {
    const opt = document.createElement("option");
    opt.value = period;
    opt.textContent = period;
    filterSelect.appendChild(opt);
  });
  box.appendChild(filterSelect);

  const list = document.createElement("div");
  box.appendChild(list);

  function renderList() {
    list.innerHTML = "";
    const period = filterSelect.value;
    (payload.times || []).forEach((t) => {
      if (period !== "All" && periodOf(t.time) !== period) return;
      const btn = document.createElement("button");
      btn.textContent = t.time;
      btn.onclick = () => submitAnswer(promptId, t.row_id);
      list.appendChild(btn);
    });
    if (!list.children.length) {
      list.textContent = "No times in this filter.";
    }
  }
  filterSelect.onchange = renderList;
  renderList();

  return box;
}

function renderReviewPrompt(promptId, payload) {
  const box = document.createElement("div");
  box.className = "prompt-box";
  box.innerHTML = "<h3>Confirm booking before payment</h3>";

  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(payload, null, 2);
  box.appendChild(pre);

  const confirmBtn = document.createElement("button");
  confirmBtn.textContent = "Confirm and proceed";
  confirmBtn.onclick = () => submitAnswer(promptId, { confirmed: true });
  box.appendChild(confirmBtn);

  const cancelBtn = document.createElement("button");
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => submitAnswer(promptId, { confirmed: false });
  box.appendChild(cancelBtn);

  return box;
}

function renderCardPrompt(promptId, payload) {
  const box = document.createElement("div");
  box.className = "prompt-box";
  const heading = document.createElement("h3");
  heading.textContent = `Enter payment card details${payload.total ? " (Total: " + payload.total + ")" : ""}`;
  box.appendChild(heading);

  const form = document.createElement("form");
  const fields = [
    { name: "holder_name", label: "Holder Name", type: "text" },
    { name: "card_number", label: "Card Number", type: "text" },
    { name: "expiry_month", label: "Expiry Month (MM)", type: "text" },
    { name: "expiry_year", label: "Expiry Year (YYYY)", type: "text" },
    { name: "cvv", label: "Security Code", type: "password" },
  ];
  const inputs = {};
  fields.forEach((f) => {
    const label = document.createElement("label");
    label.textContent = f.label;
    const input = document.createElement("input");
    input.type = f.type;
    input.name = f.name;
    input.autocomplete = "off";
    inputs[f.name] = input;
    label.appendChild(input);
    form.appendChild(label);
  });

  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.textContent = "Submit payment";
  form.appendChild(submitBtn);

  form.onsubmit = (e) => {
    e.preventDefault();
    const value = {};
    fields.forEach((f) => {
      value[f.name] = inputs[f.name].value;
    });
    submitAnswer(promptId, value);
    form.reset();
  };

  box.appendChild(form);
  return box;
}

const PROMPT_RENDERERS = {
  select_date: renderDatePrompt,
  select_time: renderTimePrompt,
  confirm_review: renderReviewPrompt,
  enter_card_details: renderCardPrompt,
};

function renderPrompt(msg) {
  const renderer = PROMPT_RENDERERS[msg.prompt_type];
  promptContainer.innerHTML = "";
  if (!renderer) {
    promptContainer.textContent = `Unknown prompt type: ${msg.prompt_type}`;
    return;
  }
  promptContainer.appendChild(renderer(msg.prompt_id, msg.payload || {}));
}

const eventSource = new EventSource("/events");
eventSource.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === "status") {
    logStatus(msg);
  } else if (msg.type === "prompt") {
    renderPrompt(msg);
  } else if (msg.type === "prompt_resolved") {
    clearPrompt();
  }
};
