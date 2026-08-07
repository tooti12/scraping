const promptContainer = document.getElementById("prompt-container");
const statusList = document.getElementById("status-list");
const connDot = document.getElementById("conn-dot");
const currentStep = document.getElementById("current-step");
const stepperEl = document.getElementById("stepper");

// ── Phase stepper ───────────────────────────────────────────────────────
// Macro view of where the booking flow is right now, separate from the
// current-step pill (which shows the specific sub-event). Built from the
// same event/prompt vocabulary booking_flow.py and app.js already speak.

const PHASES = [
  { id: "confirm", label: "Confirm Slot" },
  { id: "applicant", label: "Applicant Details" },
  { id: "datetime", label: "Date & Time" },
  { id: "review", label: "Review" },
  { id: "payment", label: "Payment" },
  { id: "done", label: "Done" },
];

const EVENT_PHASE = {
  booking_flow_started: "confirm",
  step_applicant_form: "applicant",
  applicant_fields_fetched: "applicant",
  step_otp: "applicant",
  otp_requested: "applicant",
  otp_fetch_failed: "applicant",
  otp_verified: "applicant",
  otp_verification_failed: "applicant",
  step_applicant_summary: "applicant",
  step_book_appointment: "datetime",
  date_selected: "datetime",
  time_selected: "datetime",
  step_review: "review",
  review_ready: "review",
  review_cancelled: "datetime",
  terms_accepted: "review",
  step_payment_disclaimer: "payment",
  step_payment: "payment",
  payment_submitted: "payment",
  booking_flow_finished: "done",
};

const PROMPT_PHASE = {
  confirm_booking: "confirm",
  enter_applicant_details: "applicant",
  select_date: "datetime",
  select_time: "datetime",
  confirm_review: "review",
  enter_card_details: "payment",
};

const CHECK_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5 9.5 17 19 7"/></svg>';

function renderStepper() {
  stepperEl.innerHTML = "";
  PHASES.forEach((phase, i) => {
    if (i > 0) {
      const line = document.createElement("div");
      line.className = "stepper-line";
      stepperEl.appendChild(line);
    }
    const item = document.createElement("div");
    item.className = "stepper-item";
    item.dataset.phase = phase.id;

    const bullet = document.createElement("div");
    bullet.className = "stepper-bullet";
    bullet.innerHTML = `<span>${i + 1}</span>${CHECK_ICON}`;

    const label = document.createElement("span");
    label.className = "stepper-label";
    label.textContent = phase.label;

    item.appendChild(bullet);
    item.appendChild(label);
    stepperEl.appendChild(item);
  });
}

function setPhase(phaseId) {
  if (!phaseId) return;
  const targetIndex = PHASES.findIndex((p) => p.id === phaseId);
  if (targetIndex === -1) return;
  stepperEl.querySelectorAll(".stepper-item").forEach((item, i) => {
    item.classList.remove("is-current", "is-done");
    if (i < targetIndex) item.classList.add("is-done");
    else if (i === targetIndex) item.classList.add("is-current");
  });
}

renderStepper();

// Maps the raw event names pushed via FrontendBridge.push_status() to a
// human-readable label and a severity used for log/badge coloring.
const STEP_INFO = {
  booking_flow_started: ["Booking started", "info"],
  step_applicant_form: ["Filling applicant details", "info"],
  applicant_fields_fetched: ["Applicant fields fetched from form", "info"],
  step_otp: ["OTP verification", "info"],
  otp_requested: ["OTP requested", "info"],
  otp_fetch_failed: ["OTP fetch failed", "error"],
  otp_verified: ["OTP verified", "success"],
  otp_verification_failed: ["OTP verification failed", "error"],
  step_applicant_summary: ["Reviewing applicant summary", "info"],
  step_book_appointment: ["Selecting date & time", "info"],
  date_selected: ["Date selected", "success"],
  time_selected: ["Time selected", "success"],
  step_review: ["Review & consent", "info"],
  review_ready: ["Review ready", "info"],
  review_cancelled: ["Review cancelled", "warning"],
  terms_accepted: ["Terms accepted", "success"],
  step_payment_disclaimer: ["Payment disclaimer", "info"],
  step_payment: ["Payment", "info"],
  payment_submitted: ["Payment submitted", "success"],
  booking_flow_finished: ["Booking finished", "success"],
};

function describeEvent(event) {
  return STEP_INFO[event] || [event.replace(/_/g, " "), "info"];
}

function setStep(label, level) {
  currentStep.textContent = label;
  currentStep.className = "topbar-step level-" + level;
}

function logStatus(event) {
  const [label, level] = describeEvent(event.event);
  setStep(label, level);
  setPhase(EVENT_PHASE[event.event]);

  const li = document.createElement("li");
  li.className = "level-" + level;

  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = new Date(event.ts * 1000).toLocaleTimeString();
  li.appendChild(time);

  const headline = document.createElement("span");
  headline.className = "log-event";
  headline.textContent = label;
  li.appendChild(headline);

  if (event.payload && Object.keys(event.payload).length) {
    const payload = document.createElement("span");
    payload.className = "log-payload";
    payload.textContent = JSON.stringify(event.payload);
    li.appendChild(payload);
  }

  statusList.prepend(li);
}

function clearPrompt() {
  setStep("Working…", "info");
  promptContainer.innerHTML =
    '<div class="empty-state"><span class="loader"></span>' +
    '<p class="muted">No action needed right now, waiting on the bot.</p></div>';
}

function submitAnswer(promptId, value) {
  fetch("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_id: promptId, value: value }),
  }).then(() => clearPrompt());
}

function el(tag, props, children) {
  const node = document.createElement(tag);
  Object.entries(props || {}).forEach(([k, v]) => {
    if (k === "text") node.textContent = v;
    else node[k] = v;
  });
  (children || []).forEach((c) => node.appendChild(c));
  return node;
}

function infoGrid(pairs) {
  const dl = el("dl", { className: "info-grid" });
  pairs.forEach(([label, value]) => {
    if (!value) return;
    dl.appendChild(el("dt", { text: label }));
    dl.appendChild(el("dd", { text: value }));
  });
  return dl;
}

// ── Prompt renderers ───────────────────────────────────────────────────

function renderBookingPrompt(promptId, payload) {
  const box = el("div", { className: "prompt-box" });
  box.appendChild(el("h3", { text: "Slot available: proceed to booking?" }));
  box.appendChild(
    infoGrid([
      ["Country", (payload.country || "").toUpperCase()],
      ["Centre", payload.centre],
      ["Category", payload.appt_cat],
      ["Sub-category", payload.sub_cat],
      ["Slot", payload.slot_details],
    ])
  );

  const actions = el("div", { className: "actions" });
  actions.appendChild(
    el("button", {
      className: "primary",
      text: "Yes, book this slot",
      onclick: () => submitAnswer(promptId, { confirmed: true }),
    })
  );
  actions.appendChild(
    el("button", {
      className: "danger",
      text: "No, check other countries",
      onclick: () => submitAnswer(promptId, { confirmed: false }),
    })
  );
  box.appendChild(actions);
  return box;
}

function renderApplicantPrompt(promptId, payload) {
  const box = el("div", { className: "prompt-box" });
  box.appendChild(
    el("h3", {
      text:
        "Enter applicant details" +
        (payload.login_user ? " (" + payload.login_user + ")" : ""),
    })
  );

  // Fields are discovered live from the VFS form, not hard-coded. Each
  // entry's label/required-ness reflects exactly what that country/visa
  // category currently shows, so this form is built dynamically.
  const fields = payload.fields || [];
  const form = el("form");
  const inputs = {};

  if (!fields.length) {
    form.appendChild(
      el("p", {
        className: "muted field-full",
        text: "No fields were detected on the applicant form.",
      })
    );
  }

  fields.forEach((f) => {
    const label = el("label", {});
    label.appendChild(
      document.createTextNode(f.label)
    );
    if (f.required) {
      label.appendChild(el("span", { className: "req-mark", text: "*" }));
    }
    const input = el("input", {
      type: "text",
      name: f.label,
      autocomplete: "off",
      required: !!f.required,
    });
    inputs[f.label] = input;
    label.appendChild(input);
    form.appendChild(label);
  });

  const submitBtn = el("button", {
    type: "submit",
    className: "primary field-full",
    text: "Submit applicant details",
  });
  form.appendChild(submitBtn);

  form.onsubmit = (e) => {
    e.preventDefault();
    const value = {};
    fields.forEach((f) => {
      value[f.label] = inputs[f.label].value;
    });
    submitAnswer(promptId, value);
    form.reset();
  };

  box.appendChild(form);
  return box;
}

function renderDatePrompt(promptId, payload) {
  const box = el("div", { className: "prompt-box" });
  box.appendChild(el("h3", { text: "Select an appointment date" }));

  const grid = el("div", { className: "slot-grid" });
  (payload.dates || []).forEach((d) => {
    grid.appendChild(
      el("button", {
        text: `${d.label} (${d.date})`,
        onclick: () => submitAnswer(promptId, d.date),
      })
    );
  });
  if (!payload.dates || !payload.dates.length) {
    grid.appendChild(el("p", { className: "muted", text: "No available dates were found." }));
  }
  box.appendChild(grid);
  return box;
}

function periodOf(time) {
  const hour = parseInt(time.split(":")[0], 10);
  if (hour < 12) return "Morning";
  if (hour < 17) return "Afternoon";
  return "Evening";
}

function renderTimePrompt(promptId, payload) {
  const box = el("div", { className: "prompt-box" });
  box.appendChild(
    el("h3", {
      text: "Select an appointment time" + (payload.date ? " (" + payload.date + ")" : ""),
    })
  );

  const filterWrap = el("div", { className: "period-filter" });
  const filterSelect = el("select");
  ["All", "Morning", "Afternoon", "Evening"].forEach((period) => {
    filterSelect.appendChild(el("option", { value: period, text: period }));
  });
  filterWrap.appendChild(filterSelect);
  box.appendChild(filterWrap);

  const grid = el("div", { className: "time-grid" });
  box.appendChild(grid);

  function renderList() {
    grid.innerHTML = "";
    const period = filterSelect.value;
    let any = false;
    (payload.times || []).forEach((t) => {
      if (period !== "All" && periodOf(t.time) !== period) return;
      any = true;
      grid.appendChild(
        el("button", {
          text: t.time,
          onclick: () => submitAnswer(promptId, t.row_id),
        })
      );
    });
    if (!any) {
      grid.appendChild(el("p", { className: "muted", text: "No times in this filter." }));
    }
  }
  filterSelect.onchange = renderList;
  renderList();

  return box;
}

function renderReviewPrompt(promptId, payload) {
  const box = el("div", { className: "prompt-box" });
  box.appendChild(el("h3", { text: "Confirm booking before payment" }));

  if (payload.applicant_name) {
    box.appendChild(
      el("p", { className: "muted", text: "Applicant: " + payload.applicant_name })
    );
  }

  const details = payload.details || {};
  if (Object.keys(details).length) {
    const table = el("table", { className: "review-table" });
    Object.entries(details).forEach(([label, value]) => {
      table.appendChild(el("tr", {}, [el("td", { text: label }), el("td", { text: value })]));
    });
    box.appendChild(table);
  }

  if (payload.total) {
    box.appendChild(el("div", { className: "review-total", text: "Total: " + payload.total }));
  }

  const actions = el("div", { className: "actions" });
  actions.appendChild(
    el("button", {
      className: "primary",
      text: "Confirm and proceed",
      onclick: () => submitAnswer(promptId, { confirmed: true }),
    })
  );
  actions.appendChild(
    el("button", {
      className: "danger",
      text: "Go back",
      onclick: () => submitAnswer(promptId, { confirmed: false }),
    })
  );
  box.appendChild(actions);
  return box;
}

function renderCardPrompt(promptId, payload) {
  const box = el("div", { className: "prompt-box" });
  box.appendChild(
    el("h3", {
      text: "Enter payment card details" + (payload.total ? " (Total: " + payload.total + ")" : ""),
    })
  );

  const form = el("form");
  const fields = [
    { name: "holder_name", label: "Holder Name", type: "text" },
    { name: "card_number", label: "Card Number", type: "text" },
    { name: "expiry_month", label: "Expiry Month (MM)", type: "text" },
    { name: "expiry_year", label: "Expiry Year (YYYY)", type: "text" },
    { name: "cvv", label: "Security Code", type: "password" },
  ];
  const inputs = {};
  fields.forEach((f) => {
    const label = el("label", { text: f.label });
    const input = el("input", { type: f.type, name: f.name, autocomplete: "off" });
    inputs[f.name] = input;
    label.appendChild(input);
    form.appendChild(label);
  });

  form.appendChild(
    el("p", {
      className: "security-note",
      text: "Card details go straight to VFS's payment processor, never logged or stored by this dashboard.",
    })
  );

  const submitBtn = el("button", {
    type: "submit",
    className: "primary field-full",
    text: "Submit payment",
  });
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
  confirm_booking: renderBookingPrompt,
  enter_applicant_details: renderApplicantPrompt,
  select_date: renderDatePrompt,
  select_time: renderTimePrompt,
  confirm_review: renderReviewPrompt,
  enter_card_details: renderCardPrompt,
};

function renderPrompt(msg) {
  const renderer = PROMPT_RENDERERS[msg.prompt_type];
  promptContainer.innerHTML = "";
  setStep("Action required", "warning");
  setPhase(PROMPT_PHASE[msg.prompt_type]);
  if (!renderer) {
    promptContainer.textContent = `Unknown prompt type: ${msg.prompt_type}`;
    return;
  }
  promptContainer.appendChild(renderer(msg.prompt_id, msg.payload || {}));
}

// ── SSE connection ──────────────────────────────────────────────────────

const eventSource = new EventSource("/events");

eventSource.onopen = () => {
  connDot.className = "dot connected";
};
eventSource.onerror = () => {
  connDot.className = "dot disconnected";
};

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
