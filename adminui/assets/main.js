const participantTable = document.getElementById("participant-table");
const participantCountEl = document.getElementById("participant-count");
const presenterEl = document.getElementById("presenter-name");
const chatCountEl = document.getElementById("chat-count");
const chatLogEl = document.getElementById("chat-log");
const eventLogEl = document.getElementById("event-log");
const lastUpdatedEl = document.getElementById("last-updated");

async function fetchState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    const data = await response.json();
    renderState(data);
  } catch (error) {
    console.error("Failed to fetch admin state", error);
    lastUpdatedEl.textContent = "Connection lost";
  } finally {
    window.setTimeout(fetchState, 2000);
  }
}

function renderState(state) {
  const clients = state.clients || [];
  participantCountEl.textContent = clients.length;
  const presenter = state.presenter || "None";
  presenterEl.textContent = presenter;
  chatCountEl.textContent = (state.chat_history || []).length;
  lastUpdatedEl.textContent = new Date(state.timestamp * 1000).toLocaleTimeString();

  renderParticipants(clients, presenter);
  renderChat(state.chat_history || []);
  renderEvents(state.events || []);
}

function renderParticipants(clients, presenter) {
  participantTable.innerHTML = "";
  if (!clients.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "placeholder";
    cell.textContent = "No active participants";
    row.appendChild(cell);
    participantTable.appendChild(row);
    return;
  }

  clients
    .slice()
    .sort((a, b) => a.username.localeCompare(b.username))
    .forEach((client) => {
      const row = document.createElement("tr");
      const nameCell = document.createElement("td");
      nameCell.innerHTML = `
        <span class="status-online">
          <span class="status-dot"></span>
          ${client.username}
        </span>
      `;
      const statusCell = document.createElement("td");
      statusCell.textContent = client.is_presenter ? "Presenter" : "Active";
      if (client.is_presenter) {
        statusCell.className = "status-presenter";
      }
      const connectedAt = formatTimestamp(client.connected_at);
      const lastHeartbeat = formatDuration(client.last_seen_seconds);
      const connectedCell = document.createElement("td");
      connectedCell.textContent = connectedAt;
      const heartbeatCell = document.createElement("td");
      heartbeatCell.textContent = `${lastHeartbeat} ago`;

      row.append(nameCell, statusCell, connectedCell, heartbeatCell);
      participantTable.appendChild(row);
    });
}

function renderChat(messages) {
  chatLogEl.innerHTML = "";
  if (!messages.length) {
    chatLogEl.appendChild(makePlaceholder("No chat messages yet."));
    return;
  }

  messages.slice(-50).forEach((message) => {
    const item = document.createElement("li");
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${message.sender} • ${formatTimestamp(message.timestamp_ms / 1000)}`;
    const body = document.createElement("div");
    body.className = "body";
    body.textContent = message.message;
    item.append(meta, body);
    chatLogEl.appendChild(item);
  });
}

function renderEvents(events) {
  eventLogEl.innerHTML = "";
  if (!events.length) {
    eventLogEl.appendChild(makePlaceholder("No events recorded."));
    return;
  }

  events.slice(-50).reverse().forEach((event) => {
    const item = document.createElement("li");
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${event.type.replace(/_/g, " ")} • ${formatTimestamp(event.timestamp)}`;
    const body = document.createElement("div");
    body.className = "body";
    body.textContent = describeEvent(event);
    item.append(meta, body);
    eventLogEl.appendChild(item);
  });
}

function describeEvent(event) {
  const details = event.details || {};
  switch (event.type) {
    case "user_joined":
      return `${details.username} joined the session.`;
    case "user_left":
      return `${details.username} left the session.`;
    case "presenter_granted":
      return `${details.username} is now presenting.`;
    case "presenter_revoked":
      return `${details.username} stopped presenting.`;
    case "chat_message":
      return `${details.sender}: ${details.message}`;
    default:
      return JSON.stringify(details);
  }
}

function formatTimestamp(value) {
  if (value === undefined || value === null) return "—";
  const millis = value > 1_000_000_000_000 ? value : value * 1000;
  const date = new Date(millis);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
}

function formatDuration(seconds) {
  if (typeof seconds !== "number") return "—";
  const value = Math.max(0, seconds);
  if (value < 1) return "just now";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${Math.round(value / 3600)}h`;
}

function makePlaceholder(text) {
  const item = document.createElement("li");
  item.className = "placeholder";
  item.textContent = text;
  return item;
}

fetchState();
