const participantTable = document.getElementById("participant-table");
const participantCountEl = document.getElementById("participant-count");
const presenterEl = document.getElementById("presenter-name");
const chatCountEl = document.getElementById("chat-count");
const chatLogEl = document.getElementById("chat-log");
const eventLogEl = document.getElementById("event-log");
const lastUpdatedEl = document.getElementById("last-updated");
const shutdownButton = document.getElementById("action-shutdown");
const shutdownLabel = shutdownButton ? shutdownButton.textContent : "";
const bannedListEl = document.getElementById("banned-list");
const bannedEmptyEl = document.getElementById("banned-empty");

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
  const clients = Array.isArray(state.clients) ? state.clients : [];
  const usernames = Array.isArray(state.participant_usernames) ? state.participant_usernames : clients.map((client) => client.username);
  const participantCount = typeof state.participant_count === "number" ? state.participant_count : new Set(usernames).size;
  participantCountEl.textContent = participantCount;
  const presenter = state.presenter || "None";
  presenterEl.textContent = presenter;
  chatCountEl.textContent = (state.chat_history || []).length;
  lastUpdatedEl.textContent = new Date(state.timestamp * 1000).toLocaleTimeString();

  renderParticipants(clients, presenter);
  renderChat(state.chat_history || []);
  renderEvents(state.events || []);
  renderBannedList(state.banned_usernames || []);
}

function renderParticipants(clients, presenter) {
  participantTable.innerHTML = "";
  if (!clients.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 10;
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

      const connectionCell = document.createElement("td");
      connectionCell.textContent = (client.connection_type || "-").toUpperCase();

      const addressCell = document.createElement("td");
      addressCell.textContent = client.peer_ip || "-";

      const portCell = document.createElement("td");
      portCell.textContent = client.peer_port ?? "-";

      const throughputCell = document.createElement("td");
      throughputCell.innerHTML = `${formatBitsPerSecond(client.throughput_bps)}<div class="subtext">${formatBytes(client.bytes_received)} received</div>`;

      const bandwidthCell = document.createElement("td");
      bandwidthCell.innerHTML = `${formatBitsPerSecond(client.bandwidth_bps)}<div class="subtext">${formatBytes(client.bytes_sent)} sent</div>`;

      const actionsCell = document.createElement("td");
      const kickButton = document.createElement("button");
      kickButton.type = "button";
      kickButton.className = "action-button";
      kickButton.textContent = "Kick";
      kickButton.addEventListener("click", () => handleKick(client.username, kickButton));
      actionsCell.appendChild(kickButton);

      row.append(
        nameCell,
        statusCell,
        connectedCell,
        heartbeatCell,
        connectionCell,
        addressCell,
        portCell,
        throughputCell,
        bandwidthCell,
        actionsCell
      );
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
    case "user_kicked": {
      const actor = details.actor ? ` by ${details.actor}` : "";
      return `${details.username} was removed${actor}.`;
    }
    case "user_blocked":
      return `${details.username} attempted to join but is blocked.`;
    default:
      return JSON.stringify(details);
  }
}

function renderBannedList(usernames) {
  if (!bannedListEl || !bannedEmptyEl) {
    return;
  }
  bannedListEl.innerHTML = "";
  const unique = Array.isArray(usernames) ? [...new Set(usernames.filter((name) => typeof name === "string" && name.trim()))] : [];
  if (!unique.length) {
    bannedEmptyEl.style.display = "block";
    return;
  }
  bannedEmptyEl.style.display = "none";
  unique
    .slice()
    .sort((a, b) => a.localeCompare(b))
    .forEach((name) => {
      const item = document.createElement("li");
      item.textContent = name;
      bannedListEl.appendChild(item);
    });
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

function formatBitsPerSecond(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  const units = ["bps", "Kbps", "Mbps", "Gbps"];
  let index = 0;
  let n = value;
  while (n >= 1000 && index < units.length - 1) {
    n /= 1000;
    index += 1;
  }
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${units[index]}`;
}

function formatBytes(value) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  let n = value;
  while (n >= 1024 && index < units.length - 1) {
    n /= 1024;
    index += 1;
  }
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${units[index]}`;
}

fetchState();

if (shutdownButton) {
  shutdownButton.addEventListener("click", handleShutdown);
}

async function handleShutdown() {
  if (!shutdownButton) {
    return;
  }
  const confirmed = window.confirm("Stop the server and clear temporary files?");
  if (!confirmed) {
    return;
  }
  shutdownButton.disabled = true;
  shutdownButton.textContent = "Requesting shutdown...";
  try {
    await postJson("/api/actions/shutdown");
    shutdownButton.textContent = "Shutdown requested";
    lastUpdatedEl.textContent = "Shutdown requested";
  } catch (error) {
    console.error("Failed to request shutdown", error);
    window.alert(error.message || "Failed to request shutdown");
    shutdownButton.disabled = false;
    shutdownButton.textContent = shutdownLabel;
  }
}

async function handleKick(username, button) {
  const confirmed = window.confirm(`Remove ${username} from the session?`);
  if (!confirmed) {
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Removing...";
  try {
    await postJson("/api/actions/kick", { username });
    button.textContent = "Removed";
  } catch (error) {
    console.error("Failed to kick participant", error);
    window.alert(error.message || "Failed to kick participant");
    button.disabled = false;
    button.textContent = original;
  }
}

async function postJson(url, body) {
  const options = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const data = await response.json();
      if (data && typeof data.detail === "string") {
        message = data.detail;
      }
    } catch (parseError) {
      try {
        const rawText = await response.text();
        if (rawText) {
          message = rawText;
        }
      } catch {
        // swallow
      }
    }
    throw new Error(message);
  }
  try {
    return await response.json();
  } catch {
    return null;
  }
}
