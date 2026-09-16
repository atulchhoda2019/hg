const transcript = document.getElementById("transcript");
const composer = document.getElementById("composer");
const input = document.getElementById("utterance");
const tracePanel = document.getElementById("trace");
const suggestions = document.getElementById("suggestions");
const brandSelect = document.getElementById("brand");
const guestSelect = document.getElementById("guest");
const checkIn = document.getElementById("checkin");
const checkOut = document.getElementById("checkout");
const conversationField = document.getElementById("conversation");

const GUESTS = {
  "B-LUX": [
    ["G-7001", "G-7001 · Platinum, 148000 points, party of 4"],
    ["G-7002", "G-7002 · Silver, 9000 points, already booked"],
  ],
  "B-HARBOR": [["G-8001", "G-8001 · Gold, books and notifies"]],
  "B-EXPRESS": [["G-9001", "G-9001 · draft-only brand"]],
  "B-CLASSIC": [["G-9501", "G-9501 · search-only brand"]],
};

const SUGGESTIONS = [
  "family suite near the park, under $300, with breakfast",
  "book offer OF-RIV-FAM",
  "book offer OF-RIV-FAM with points",
  "how many points do I have",
  "what is my reservation",
  "what is the cancellation policy",
  "move my reservation to next week",
  "my trip stuff is wrong",
];

// Carried into the next turn only, so one clarify pass resolves by exact option match.
let clarify = null;
let conversationId = "";

const TRACE_EMPTY = "Send a message to see the nodes that ran.";

function newConversation() {
  conversationId = `C-${Math.random().toString(36).slice(2, 10)}`;
  conversationField.value = conversationId;
  clarify = null;
  transcript.innerHTML = "";
  tracePanel.textContent = TRACE_EMPTY;
}

function fillGuests() {
  guestSelect.innerHTML = "";
  for (const [ref, label] of GUESTS[brandSelect.value]) {
    const option = document.createElement("option");
    option.value = ref;
    option.textContent = label;
    guestSelect.appendChild(option);
  }
}

function bubble(role, text) {
  const node = document.createElement("div");
  node.className = `msg ${role}`;
  node.textContent = text;
  transcript.appendChild(node);
  transcript.scrollTop = transcript.scrollHeight;
  return node;
}

function renderCitations(citations) {
  if (!citations || !citations.length) return;
  const row = document.createElement("div");
  row.className = "citations";
  row.innerHTML = citations.map((id) => `<span>${id}</span>`).join("");
  transcript.appendChild(row);
}

function renderOptions(options) {
  if (!options || !options.length) return;
  const row = document.createElement("div");
  row.className = "options";
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = option.detail ? `${option.label} — ${option.detail}` : option.label;
    button.addEventListener("click", () => send(option.label));
    row.appendChild(button);
  }
  transcript.appendChild(row);
  transcript.scrollTop = transcript.scrollHeight;
}

function renderOffers(offers) {
  if (!offers || !offers.length) return;
  const row = document.createElement("div");
  row.className = "options";
  for (const offer of offers) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `Book ${offer.room_name} at ${offer.property_name} · ${offer.nightly_rate}/night`;
    button.addEventListener("click", () => send(`book offer ${offer.offer_id}`));
    row.appendChild(button);
  }
  transcript.appendChild(row);
  transcript.scrollTop = transcript.scrollHeight;
}

function renderPreview(body) {
  const proposal = body.proposal;
  const card = document.createElement("div");
  card.className = "proposal";
  const points = proposal.pay_with === "cash"
    ? `${proposal.total_price} in cash`
    : `${proposal.points_applied} points plus ${proposal.cash_due} in cash`;
  card.innerHTML = `
    <h3>Typed proposal · awaiting confirmation</h3>
    <div>${proposal.room_name} at ${proposal.property_name} · ${proposal.rate_plan}</div>
    <div>${proposal.check_in} to ${proposal.check_out} · ${proposal.nights} nights · ${proposal.nightly_rate} a night</div>
    <div>${points}</div>
    <div>${proposal.cancellation}</div>
    <div>checks passed: ${proposal.validations.join(", ")}</div>
    <code>nonce ${proposal.nonce} · expires ${proposal.expires_at}</code>
  `;
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.textContent = "Confirm booking";
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    const response = await fetch("/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversationId,
        proposalId: proposal.proposal_id,
        nonce: proposal.nonce,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      bubble("system", `Confirmation refused: ${JSON.stringify(payload.detail)}`);
      confirm.disabled = false;
      return;
    }
    // Confirming a proposal whose rate moved returns a refreshed proposal, not a receipt.
    if (payload.kind === "preview") {
      bubble("system", payload.notice);
      renderPreview(payload);
    } else if (payload.kind === "answer") {
      bubble("assistant", payload.text);
    } else {
      renderReceipt(payload.receipt);
    }
    await refreshTrace();
  });
  card.appendChild(confirm);
  transcript.appendChild(card);
  transcript.scrollTop = transcript.scrollHeight;
}

function renderReceipt(receipt) {
  if (!receipt) return;
  const reservation = receipt.reservation;
  const card = document.createElement("div");
  card.className = "receipt";
  card.innerHTML = `
    <h3>Booked${receipt.replayed ? " \u00b7 duplicate collapsed" : ""}${receipt.reconciled ? " \u00b7 reconciled after a timeout" : ""}</h3>
    <div>${reservation.room_name} at ${reservation.property_id} \u00b7 ${reservation.check_in} to ${reservation.check_out}</div>
    <div>${reservation.total_price} total \u00b7 ${reservation.points_applied} points applied</div>
    <div>verified in the reservation system: ${receipt.verified_confirmation_number}</div>
    <code>idempotency key ${receipt.idempotency_key} \u00b7 ${receipt.committed_at}</code>
  `;
  transcript.appendChild(card);
  transcript.scrollTop = transcript.scrollHeight;
}

function renderDraft(body) {
  const card = document.createElement("div");
  card.className = "proposal";
  card.innerHTML = `
    <h3>Draft for the booking page</h3>
    <div>${body.form.action} \u00b7 ${JSON.stringify(body.form.requested)}</div>
    <div>${body.form.instructions}</div>
  `;
  transcript.appendChild(card);
}

function renderResponse(body, utterance) {
  switch (body.kind) {
    case "clarify":
      bubble("assistant", body.question);
      renderOptions(body.options);
      clarify = { options: body.options, utterance };
      return;
    case "handoff":
      bubble("assistant", body.summary);
      return;
    case "preview":
      bubble("assistant", "Here is exactly what I would book. Nothing is reserved until you confirm.");
      renderCitations(body.citations);
      renderPreview(body);
      return;
    case "receipt":
      renderReceipt(body.receipt);
      return;
    case "draft":
      renderDraft(body);
      return;
    default:
      bubble("assistant", body.text || "");
      renderCitations(body.citations);
      renderOffers(body.offers);
      if (body.next_step) bubble("system", body.next_step);
      if (body.disclosures) body.disclosures.forEach((line) => bubble("system", line));
  }
}

const TRACE_FIELDS = [
  "source", "confidence", "band", "row", "graph_id", "posture", "allowed", "reason",
  "kind", "items", "types", "offers", "passages", "count", "passed", "reasons",
  "citations", "stale", "refetched", "rung", "checks", "outcome", "replayed",
  "verified", "refreshed", "was", "now", "model", "attempt", "timeout",
];

function renderTrace(events, runUrl) {
  if (!events.length) {
    tracePanel.textContent = TRACE_EMPTY;
    return;
  }
  const latest = events[events.length - 1].turn_id;
  const link = runUrl
    ? `<div class="runlink"><a href="${runUrl}" target="_blank" rel="noreferrer">Open this turn in LangSmith</a></div>`
    : "";
  tracePanel.innerHTML = link + events
    .filter((event) => event.turn_id === latest)
    .map((event) => {
      const fields = TRACE_FIELDS
        .filter((key) => event[key] !== undefined && event[key] !== null)
        .map((key) => `${key}=${JSON.stringify(event[key])}`)
        .join(" ");
      return `<div><span class="node">${event.node}</span> <span class="fields">${fields}</span></div>`;
    })
    .join("");
}

async function refreshTrace() {
  const response = await fetch(`/trace/${conversationId}`);
  if (!response.ok) return;
  const body = await response.json();
  renderTrace(body.events, body.langsmith_run_url);
}

async function send(utterance) {
  if (!utterance.trim()) return;
  bubble("user", utterance);
  input.value = "";

  const uiContext = {
    check_in: checkIn.value,
    check_out: checkOut.value,
    ...(clarify
      ? { clarify_rounds: 1, clarify_options: clarify.options, clarify_utterance: clarify.utterance }
      : {}),
  };
  const carried = clarify;
  clarify = null;

  const response = await fetch("/turn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversationId,
      brandId: brandSelect.value,
      guestRef: guestSelect.value,
      utterance,
      uiContext,
    }),
  });
  const body = await response.json();
  if (!response.ok) {
    const detail = body.detail || {};
    bubble("system", detail.message || JSON.stringify(detail));
    clarify = carried;
    return;
  }
  renderResponse(body, utterance);
  await refreshTrace();
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  send(input.value);
});

brandSelect.addEventListener("change", () => {
  fillGuests();
  newConversation();
  bubble("system", `Switched to ${brandSelect.value} · ${guestSelect.value}. New conversation.`);
});

// The thread is bound to one guest: switching identity must not inherit a live proposal.
guestSelect.addEventListener("change", () => {
  newConversation();
  bubble("system", `Switched to ${guestSelect.value}. New conversation.`);
});

document.getElementById("reset").addEventListener("click", () => {
  newConversation();
  bubble("system", "New conversation.");
});

for (const suggestion of SUGGESTIONS) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = suggestion;
  button.addEventListener("click", () => send(suggestion));
  suggestions.appendChild(button);
}

fillGuests();
newConversation();
bubble("assistant", "Hi! I can find a room, price a stay in cash or points, check your reservation, and book once you confirm.");
