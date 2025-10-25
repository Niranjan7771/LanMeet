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
const timeRemainingEl = document.getElementById("time-remaining");
const timeTotalEl = document.getElementById("time-total");
const latencyAverageEl = document.getElementById("latency-average");
const latencyRangeEl = document.getElementById("latency-range");
const healthStatusEl = document.getElementById("health-status");
const healthUpdatedEl = document.getElementById("health-updated");
const storageUsageEl = document.getElementById("storage-usage");
const storageFilesEl = document.getElementById("storage-files");
const timeLimitInput = document.getElementById("time-limit-minutes");
const timeLimitStartNow = document.getElementById("time-limit-start-now");
const timeLimitApplyBtn = document.getElementById("time-limit-apply");
const timeLimitClearBtn = document.getElementById("time-limit-clear");
const noticeMessageInput = document.getElementById("notice-message");
const noticeLevelSelect = document.getElementById("notice-level");
const noticeSendBtn = document.getElementById("notice-send");
const participantFilterSelect = document.getElementById("participant-filter");
const participantSearchInput = document.getElementById("participant-search");
const exportEventsBtn = document.getElementById("export-events");
const logTailEl = document.getElementById("log-tail");

let latestState = null;
let participantFilter = "all";
let participantSearch = "";
let cachedClients = [];
let timeLimitState = null;
let timeLimitTickerId = null;

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
  latestState = state;
  cachedClients = clients;
  const usernames = Array.isArray(state.participant_usernames) ? state.participant_usernames : clients.map((client) => client.username);
  const participantCount = typeof state.participant_count === "number" ? state.participant_count : new Set(usernames).size;
  participantCountEl.textContent = participantCount;
  const presenter = state.presenter || "None";
  presenterEl.textContent = presenter;
  chatCountEl.textContent = (state.chat_history || []).length;
  lastUpdatedEl.textContent = new Date(state.timestamp * 1000).toLocaleTimeString();

  const shutdownRequested = Boolean(state.shutdown_requested);
  if (shutdownButton) {
    if (shutdownRequested) {
      shutdownButton.disabled = true;
      const label = typeof state.shutdown_reason === "string" && state.shutdown_reason
        ? `Shutdown in progress: ${state.shutdown_reason}`
        : "Shutdown in progress";
      shutdownButton.textContent = label;
      lastUpdatedEl.textContent = label;
    } else if (shutdownButton.textContent !== "Requesting shutdown...") {
      shutdownButton.disabled = false;
      shutdownButton.textContent = shutdownLabel;
    }
  }

  renderTimeLimit(state.time_limit);
  renderLatencySummary(state.latency_summary);
  renderHealthCard(state.health);
  renderStorageUsage(state.storage_usage);
  renderParticipants(clients, presenter);
  renderChat(state.chat_history || []);
  renderEvents(state.events || []);
  renderLogTail(state.log_tail || []);
  renderBannedList(state.banned_usernames || []);
}

function renderTimeLimit(info) {
  if (!timeRemainingEl || !timeTotalEl) {
    return;
  }
  if (timeLimitTickerId !== null) {
    window.clearInterval(timeLimitTickerId);
    timeLimitTickerId = null;
  }
  if (!info || !info.is_active) {
    timeLimitState = null;
    timeRemainingEl.textContent = "No limit";
    timeRemainingEl.classList.remove("expired");
    timeTotalEl.textContent = "Configure a limit to start countdown.";
    if (timeLimitInput && document.activeElement !== timeLimitInput) {
      timeLimitInput.value = "";
    }
    return;
  }
  timeLimitState = {
    ...info,
    _received_at: Date.now() / 1000,
  };
  updateTimeLimitDisplay();
  timeLimitTickerId = window.setInterval(updateTimeLimitDisplay, 1000);
  if (timeLimitInput && document.activeElement !== timeLimitInput) {
    const durationSeconds = typeof info.duration_seconds === "number" ? info.duration_seconds : null;
    timeLimitInput.value = durationSeconds ? String(Math.round(durationSeconds / 60)) : "";
  }
}

function updateTimeLimitDisplay() {
  if (!timeLimitState || !timeRemainingEl || !timeTotalEl) {
    return;
  }
  const remainingSeconds = computeRemainingSeconds(timeLimitState);
  if (remainingSeconds === null) {
    timeRemainingEl.textContent = "--";
    timeRemainingEl.classList.remove("expired");
  } else if (remainingSeconds <= 0) {
    timeRemainingEl.textContent = "Expired";
    timeRemainingEl.classList.add("expired");
  } else {
    timeRemainingEl.textContent = formatCountdown(remainingSeconds);
    timeRemainingEl.classList.toggle("expired", remainingSeconds <= 0);
  }
  const durationSeconds = typeof timeLimitState.duration_seconds === "number" ? timeLimitState.duration_seconds : null;
  if (!durationSeconds) {
    timeTotalEl.textContent = "Countdown in progress.";
  } else {
    timeTotalEl.textContent = `of ${formatCountdown(durationSeconds)}`;
  }
}

function computeRemainingSeconds(state) {
  const nowSeconds = Date.now() / 1000;
  if (typeof state.end_timestamp === "number") {
    return Math.max(0, Math.round(state.end_timestamp - nowSeconds));
  }
  if (typeof state.remaining_seconds === "number" && typeof state._received_at === "number") {
    const elapsed = nowSeconds - state._received_at;
    return Math.max(0, Math.round(state.remaining_seconds - elapsed));
  }
  if (typeof state.duration_seconds === "number" && typeof state.started_at === "number") {
    const elapsed = nowSeconds - state.started_at;
    return Math.max(0, Math.round(state.duration_seconds - elapsed));
  }
  return null;
}

function renderLatencySummary(summary) {
  if (!latencyAverageEl || !latencyRangeEl) {
    return;
  }
  if (!summary || typeof summary.sample_count !== "number" || summary.sample_count === 0) {
    latencyAverageEl.textContent = "--";
    latencyRangeEl.textContent = "No latency samples yet.";
    return;
  }
  const average = Math.round(Number(summary.average_ms || 0));
  const minValue = Math.round(Number(summary.min_ms || 0));
  const maxValue = Math.round(Number(summary.max_ms || 0));
  latencyAverageEl.textContent = `${average} ms`;
  latencyRangeEl.textContent = `Min ${minValue} ms • Max ${maxValue} ms (${summary.sample_count} sample${summary.sample_count === 1 ? "" : "s"})`;
}

function renderHealthCard(health) {
  if (!healthStatusEl || !healthUpdatedEl) {
    return;
  }
  if (!health || typeof health !== "object") {
    healthStatusEl.textContent = "UNKNOWN";
    healthUpdatedEl.textContent = "—";
    return;
  }
  const status = String(health.status || "unknown").toUpperCase();
  healthStatusEl.textContent = status;
  const updatedAt = typeof health.timestamp === "number" ? formatTimestamp(health.timestamp) : "—";
  const participants = typeof health.participant_count === "number" ? ` • ${health.participant_count} participants` : "";
  healthUpdatedEl.textContent = `Updated ${updatedAt}${participants}`;
}

function renderStorageUsage(usage) {
  if (!storageUsageEl || !storageFilesEl) {
    return;
  }
  if (!usage || typeof usage !== "object") {
    storageUsageEl.textContent = "--";
    storageFilesEl.textContent = "No storage metrics yet.";
    return;
  }
  const bytes = Number(usage.bytes || 0);
  const files = Number(usage.files || 0);
  storageUsageEl.textContent = formatBytes(bytes);
  storageFilesEl.textContent = `${files} file${files === 1 ? "" : "s"}`;
}

function renderLogTail(entries) {
  if (!logTailEl) {
    return;
  }
  if (!Array.isArray(entries) || entries.length === 0) {
    logTailEl.textContent = "No recent log lines yet.";
    return;
  }
  const lines = entries.slice(-40).map((entry) => {
    const timestamp = formatTimestamp(entry.timestamp || Date.now() / 1000);
    const level = typeof entry.level === "string" ? entry.level.toUpperCase() : "INFO";
    const loggerName = entry.logger ? ` ${entry.logger}` : "";
    const message = entry.message || "";
    return `[${timestamp}] ${level}${loggerName} :: ${message}`;
  });
  logTailEl.textContent = lines.join("\n");
}

function renderParticipants(clients, presenter) {
  participantTable.innerHTML = "";
  const dataset = Array.isArray(clients) ? clients.slice() : [];
  if (!dataset.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 10;
    cell.className = "placeholder";
    cell.textContent = "No active participants";
    row.appendChild(cell);
    participantTable.appendChild(row);
    return;
  }

  const filtered = dataset
    .filter((client) => participantMatchesFilter(client))
    .filter((client) => participantMatchesSearch(client));

  if (!filtered.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 10;
    cell.className = "placeholder";
    cell.textContent = "No participants match the current filters.";
    row.appendChild(cell);
    participantTable.appendChild(row);
    return;
  }

  filtered
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

function participantMatchesFilter(client) {
  switch (participantFilter) {
    case "presenters":
      return Boolean(client.is_presenter);
    case "hand":
      return Boolean(client.hand_raised);
    case "muted":
      return client.audio_enabled === false;
    case "noVideo":
      return client.video_enabled === false;
    case "highLatency": {
      const latency = Number(client.latency_ms);
      return Number.isFinite(latency) && latency >= 250;
    }
    case "inactive": {
      const lastSeen = Number(client.last_seen_seconds);
      return Number.isFinite(lastSeen) && lastSeen >= 30;
    }
    default:
      return true;
  }
}

function participantMatchesSearch(client) {
  if (!participantSearch) {
    return true;
  }
  const haystack = [client.username, client.peer_ip, client.peer_port, client.connection_type]
    .filter((value) => value !== null && value !== undefined)
    .join(" ")
    .toLowerCase();
  return haystack.includes(participantSearch);
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
    case "time_limit_set":
      {
        const minutesRaw = details && details.duration_minutes !== undefined ? Number(details.duration_minutes) : NaN;
        const minutes = Number.isFinite(minutesRaw) ? Math.round(minutesRaw) : null;
        const label = minutes ? `${minutes} minute${minutes === 1 ? "" : "s"}` : "an unknown duration";
        return `Time limit set to ${label} by ${details.actor || "admin"}.`;
      }
    case "time_limit_cleared":
      return `Time limit cleared by ${details.actor || "admin"}.`;
    case "admin_notice":
      return `${details.actor || "admin"} broadcast: ${details.message || "(no message)"}`;
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

function formatCountdown(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined || Number.isNaN(totalSeconds)) {
    return "--";
  }
  const value = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;
  const pieces = [];
  if (hours > 0) {
    pieces.push(`${hours}h`);
  }
  if (minutes > 0 || hours > 0) {
    pieces.push(`${minutes}m`);
  }
  pieces.push(`${seconds}s`);
  return pieces.join(" ");
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

if (timeLimitApplyBtn) {
  timeLimitApplyBtn.addEventListener("click", handleTimeLimitApply);
}

if (timeLimitClearBtn) {
  timeLimitClearBtn.addEventListener("click", handleTimeLimitClear);
}

if (noticeSendBtn) {
  noticeSendBtn.addEventListener("click", handleNoticeSend);
}

if (participantFilterSelect) {
  participantFilterSelect.addEventListener("change", () => {
    participantFilter = participantFilterSelect.value;
    if (cachedClients && latestState) {
      renderParticipants(cachedClients, latestState.presenter || "");
    }
  });
}

if (participantSearchInput) {
  participantSearchInput.addEventListener("input", () => {
    participantSearch = participantSearchInput.value.trim().toLowerCase();
    if (cachedClients && latestState) {
      renderParticipants(cachedClients, latestState.presenter || "");
    }
  });
}

if (exportEventsBtn) {
  exportEventsBtn.addEventListener("click", handleExportEvents);
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
    const result = await postJson("/api/actions/shutdown");
    const initiated = result && typeof result.initiated === "boolean" ? result.initiated : true;
    const statusLabel = initiated ? "Shutdown requested" : "Shutdown already in progress";
    shutdownButton.textContent = statusLabel;
    lastUpdatedEl.textContent = statusLabel;
  } catch (error) {
    console.error("Failed to request shutdown", error);
    window.alert(error.message || "Failed to request shutdown");
    shutdownButton.disabled = false;
    shutdownButton.textContent = shutdownLabel;
  }
}

async function handleTimeLimitApply() {
  if (!timeLimitInput) {
    return;
  }
  const rawValue = timeLimitInput.value.trim();
  const minutes = Number(rawValue);
  if (!Number.isFinite(minutes) || minutes <= 0) {
    window.alert("Enter a positive duration in minutes before applying the limit.");
    return;
  }
  const payload = {
    duration_minutes: minutes,
    start_now: timeLimitStartNow ? Boolean(timeLimitStartNow.checked) : false,
  };
  try {
    const response = await postJson("/api/actions/time-limit", payload);
    if (response && response.time_limit) {
      renderTimeLimit(response.time_limit);
    }
    if (timeLimitStartNow) {
      timeLimitStartNow.checked = false;
    }
  } catch (error) {
    console.error("Failed to set time limit", error);
    window.alert(error.message || "Failed to apply time limit");
  }
}

async function handleTimeLimitClear() {
  const confirmed = window.confirm("Clear the current meeting time limit?");
  if (!confirmed) {
    return;
  }
  try {
    const response = await postJson("/api/actions/time-limit", { duration_minutes: null });
    if (response && response.time_limit) {
      renderTimeLimit(response.time_limit);
    } else {
      renderTimeLimit(null);
    }
    if (timeLimitInput) {
      timeLimitInput.value = "";
    }
    if (timeLimitStartNow) {
      timeLimitStartNow.checked = false;
    }
  } catch (error) {
    console.error("Failed to clear time limit", error);
    window.alert(error.message || "Failed to clear time limit");
  }
}

async function handleNoticeSend() {
  if (!noticeMessageInput) {
    return;
  }
  const message = noticeMessageInput.value.trim();
  if (!message) {
    window.alert("Enter a message to broadcast.");
    return;
  }
  const payload = {
    message,
    level: noticeLevelSelect ? noticeLevelSelect.value : "info",
  };
  try {
    await postJson("/api/actions/notice", payload);
    noticeMessageInput.value = "";
  } catch (error) {
    console.error("Failed to send notice", error);
    window.alert(error.message || "Failed to send notice");
  }
}

async function handleExportEvents() {
  try {
    const response = await fetch("/api/export/events", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = url;
    link.download = `session-events-${timestamp}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  } catch (error) {
    console.error("Failed to export events", error);
    window.alert(error.message || "Failed to export events");
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
