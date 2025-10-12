const statusEl = document.getElementById("status");
const chatMessagesEl = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatSendBtn = chatForm.querySelector("button");
const participantListEl = document.getElementById("participant-list");
const fileListEl = document.getElementById("file-list");
const uploadButton = document.getElementById("upload-file");
const fileInput = document.getElementById("file-input");
const screenPreviewEl = document.getElementById("screen-preview");
const videoGridEl = document.getElementById("video-grid");

const joinOverlay = document.getElementById("join-overlay");
const joinForm = document.getElementById("join-form");
const nameInput = document.getElementById("name-input");
const randomNameBtn = document.getElementById("random-name");
const joinStatusEl = document.getElementById("join-status");
const joinButton = joinForm.querySelector(".primary");

const micToggleBtn = document.getElementById("toggle-mic");
const videoToggleBtn = document.getElementById("toggle-video");
const presentToggleBtn = document.getElementById("toggle-present");
const leaveButton = document.getElementById("leave-session");
const leaveCountdownEl = document.getElementById("leave-countdown");
const leaveSection = document.getElementById("leave-confirm");
const joinSection = document.getElementById("join-section");
const cancelLeaveBtn = document.getElementById("cancel-leave");
const confirmLeaveBtn = document.getElementById("confirm-leave");

let socket;
let socketReady = false;
let participants = new Set();
let files = new Map();
let currentUsername = null;
let currentPresenter = null;
let joined = false;
let micEnabled = false;
let videoEnabled = false;
const videoElements = new Map();
let leaveTimerId = null;
let leaveDeadlineMs = null;
const LEAVE_GRACE_PERIOD_MS = 20000;

function init() {
  setConnectedUi(false);
  joinButton.disabled = true;
  fetchConfig();
  connectSocket();
}

async function fetchConfig() {
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    if (!response.ok) throw new Error("config fetch failed");
    const data = await response.json();
    if (data.prefill_username) {
      nameInput.value = data.prefill_username;
    } else {
      await requestRandomName();
    }
  } catch (error) {
    console.warn("Unable to load client config", error);
    await requestRandomName();
  }
}

async function requestRandomName() {
  try {
    const response = await fetch("/api/random-name", { cache: "no-store" });
    if (!response.ok) throw new Error("random name failed");
    const data = await response.json();
    if (data.username) {
      nameInput.value = data.username;
      return;
    }
  } catch (error) {
    console.warn("Random name generation failed", error);
  }
  nameInput.value = generateLocalName();
}

function generateLocalName() {
  const adjectives = ["swift", "bright", "lively", "bold", "stellar", "brisk", "clever"];
  const nouns = ["lynx", "sparrow", "otter", "falcon", "fox", "orca", "aurora"];
  return `${pick(adjectives)}-${pick(nouns)}-${Math.floor(Math.random() * 900 + 100)}`;
}

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function connectSocket() {
  socket = new WebSocket(`ws://${window.location.host}/ws/control`);
  socket.addEventListener("open", () => {
    socketReady = true;
    joinButton.disabled = false;
    if (!joined) {
      setJoinStatus("Ready to join", false);
      joinOverlay.classList.remove("hidden");
    }
    updateStatusLine();
  });
  socket.addEventListener("close", () => {
    socketReady = false;
    joined = false;
    joinButton.disabled = true;
    setConnectedUi(false);
    setJoinStatus("Reconnecting to client service…", true);
    joinOverlay.classList.remove("hidden");
    updateStatusLine("Disconnected. Attempting to reconnect…");
    setTimeout(connectSocket, 2000);
  });
  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    handleServerEvent(data.type, data.payload);
  });
}

function sendControl(type, payload = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    console.warn("Socket not ready for", type);
    return false;
  }
  socket.send(
    JSON.stringify({
      type,
      payload,
    })
  );
  return true;
}

function handleServerEvent(type, payload) {
  switch (type) {
    case "session_status":
      handleSessionStatus(payload || {});
      break;
    case "welcome":
      initState(payload);
      break;
    case "chat_message":
      appendChatMessage(payload);
      break;
    case "user_joined":
      participants.add(payload.username);
      renderParticipants();
      ensureVideoTile(payload.username);
      break;
    case "user_left":
      participants.delete(payload.username);
      renderParticipants();
      removeVideoTile(payload.username);
      break;
    case "presenter_granted":
      setPresenterState(payload.username);
      break;
    case "presenter_revoked":
      if (currentPresenter === payload.username) {
        setPresenterState(null);
        screenPreviewEl.innerHTML = "Presenter stopped";
      }
      break;
    case "screen_control":
      handleScreenControl(payload);
      break;
    case "screen_frame":
      handleScreenFrame(payload);
      break;
    case "file_offer":
      handleFileOffer(payload);
      break;
    case "file_progress":
      handleFileProgress(payload);
      break;
    case "file_upload_complete":
      statusEl.textContent = `Upload complete: ${payload.filename}`;
      break;
    case "file_download_ready":
      if (payload?.url) {
        window.open(payload.url, "_blank");
      }
      break;
    case "video_frame":
      updateVideoTile(payload.username, payload.frame);
      break;
    case "state_snapshot":
      handleStateSnapshot(payload);
      break;
    default:
      console.debug("Unhandled event", type, payload);
  }
}

function handleSessionStatus({ state, username, message }) {
  switch (state) {
    case "idle":
      resetLeaveFlow();
      joined = false;
      micEnabled = false;
      videoEnabled = false;
      setConnectedUi(false);
      updateStatusLine();
      joinButton.disabled = !socketReady;
      setJoinStatus(socketReady ? "Ready to join" : "Waiting for client service…", !socketReady);
      joinOverlay.classList.remove("hidden");
      if (username) {
        currentUsername = username;
        nameInput.value = username;
      }
      updateControlButtons();
      break;
    case "connecting":
      resetLeaveFlow();
      joinButton.disabled = true;
      setJoinStatus(`Connecting as ${username}…`, false);
      joinOverlay.classList.remove("hidden");
      break;
    case "connected":
      resetLeaveFlow();
      joined = true;
      currentUsername = username || currentUsername;
      joinButton.disabled = false;
      joinOverlay.classList.add("hidden");
      setConnectedUi(true);
      setJoinStatus("Connected", false);
      updateStatusLine();
      updateControlButtons();
      break;
    case "disconnecting":
      setConnectedUi(false);
      joinButton.disabled = true;
      joinOverlay.classList.remove("hidden");
      setJoinStatus(`Disconnecting as ${username || currentUsername || ""}…`, false);
      updateControlButtons();
      break;
    case "error":
      resetLeaveFlow();
      joined = false;
      micEnabled = false;
      videoEnabled = false;
      setConnectedUi(false);
      joinOverlay.classList.remove("hidden");
      joinButton.disabled = false;
      setJoinStatus(message ? `Error: ${message}` : "Unable to connect", true);
      updateStatusLine("Error establishing session");
      updateControlButtons();
      break;
    default:
      break;
  }
}

function setJoinStatus(text, isError) {
  joinStatusEl.textContent = text;
  joinStatusEl.classList.toggle("error", Boolean(isError));
}

function updateStatusLine(fallback) {
  if (!joined || !currentUsername) {
    statusEl.textContent = fallback || (socketReady ? "Offline" : "Connecting…");
    statusEl.classList.toggle("error", !socketReady);
    return;
  }
  const pieces = [`Connected as ${currentUsername}`];
  if (currentPresenter) {
    pieces.push(`Presenter: ${currentPresenter}`);
  }
  statusEl.textContent = pieces.join(" • ");
  statusEl.classList.remove("error");
}

function setConnectedUi(enabled) {
  chatInput.disabled = !enabled;
  chatSendBtn.disabled = !enabled;
  uploadButton.disabled = !enabled;
  fileInput.disabled = !enabled;
  micToggleBtn.disabled = !enabled;
  videoToggleBtn.disabled = !enabled;
  presentToggleBtn.disabled = !enabled;
  leaveButton.disabled = !enabled;
}

function initState(payload) {
  joined = true;
  currentUsername = payload.username;
  micEnabled = false;
  videoEnabled = false;
  updateStatusLine();
  participants = new Set(payload.peers || []);
  renderParticipants();
  chatMessagesEl.innerHTML = "";
  (payload.chat_history || []).forEach((msg) => appendChatMessage(msg));
  files = new Map();
  (payload.files || []).forEach((file) => {
    files.set(file.file_id, file);
  });
  renderFiles();
  sendControl("file_request_list");
  videoElements.clear();
  videoGridEl.innerHTML = "";
  participants.forEach((name) => ensureVideoTile(name));
  ensureVideoTile(currentUsername);
  resetLeaveFlow();
  updateControlButtons();
}

function handleStateSnapshot(snapshot) {
  if (!snapshot || !snapshot.connected) {
    return;
  }
  joined = true;
  currentUsername = snapshot.username || currentUsername;
  if (currentUsername) {
    nameInput.value = currentUsername;
  }
  participants = new Set(snapshot.peers || []);
  renderParticipants();
  chatMessagesEl.innerHTML = "";
  (snapshot.chat_history || []).forEach((msg) => appendChatMessage(msg));
  files = new Map();
  (snapshot.files || []).forEach((file) => {
    if (file.file_id) {
      files.set(file.file_id, file);
    }
  });
  renderFiles();
  const media = snapshot.media || {};
  micEnabled = Boolean(media.audio_enabled);
  videoEnabled = Boolean(media.video_enabled);
  setPresenterState(snapshot.presenter || null);
  updateControlButtons();
  videoElements.clear();
  videoGridEl.innerHTML = "";
  participants.forEach((name) => ensureVideoTile(name));
  ensureVideoTile(currentUsername);
  setConnectedUi(true);
  leaveButton.disabled = false;
  joinOverlay.classList.add("hidden");
  updateStatusLine();
  sendControl("file_request_list");
}

function appendChatMessage({ sender, message, timestamp_ms }) {
  const item = document.createElement("div");
  item.className = "chat-message";
  const meta = document.createElement("div");
  meta.className = "meta";
  const date = timestamp_ms ? new Date(timestamp_ms) : new Date();
  meta.textContent = `${sender} • ${date.toLocaleTimeString()}`;
  const body = document.createElement("div");
  body.textContent = message;
  item.append(meta, body);
  chatMessagesEl.appendChild(item);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

function renderParticipants() {
  participantListEl.innerHTML = "";
  Array.from(participants)
    .sort((a, b) => a.localeCompare(b))
    .forEach((username) => {
      const li = document.createElement("li");
      li.textContent = username;
      participantListEl.appendChild(li);
    });
}

function ensureVideoTile(username) {
  if (!username || videoElements.has(username) || username === currentUsername) {
    return;
  }
  const tile = document.createElement("div");
  tile.className = "video-tile";
  const label = document.createElement("div");
  label.className = "video-label";
  label.textContent = username;
  const img = document.createElement("img");
  img.alt = `${username} video`;
  tile.append(img, label);
  videoElements.set(username, img);
  videoGridEl.appendChild(tile);
}

function removeVideoTile(username) {
  const img = videoElements.get(username);
  if (!img) return;
  videoElements.delete(username);
  const tile = img.parentElement;
  tile?.remove();
}

function updateVideoTile(username, frame) {
  if (username === currentUsername) {
    return;
  }
  ensureVideoTile(username);
  const img = videoElements.get(username);
  if (img) {
    img.src = `data:image/jpeg;base64,${frame}`;
  }
}

function handleScreenControl({ state, username }) {
  if (state === "start" && username !== currentUsername) {
    screenPreviewEl.textContent = "Receiving presenter feed…";
  }
  if (state === "stop" && username !== currentUsername) {
    screenPreviewEl.textContent = "No presenter";
  }
}

function handleScreenFrame({ frame, username }) {
  if (username === currentUsername || !frame) {
    return;
  }
  if (!screenPreviewEl.querySelector("img")) {
    screenPreviewEl.innerHTML = "";
    const img = document.createElement("img");
    img.className = "screen-image";
    img.style.width = "100%";
    img.style.borderRadius = "8px";
    screenPreviewEl.appendChild(img);
  }
  const img = screenPreviewEl.querySelector("img");
  img.src = `data:image/jpeg;base64,${frame}`;
}

function handleFileOffer(payload) {
  if (payload.files) {
    payload.files.forEach((file) => files.set(file.file_id, file));
  } else if (payload.file_id) {
    files.set(payload.file_id, payload);
  }
  renderFiles();
}

function handleFileProgress(payload) {
  if (!payload.file_id) {
    statusEl.textContent = `Uploading ${payload.filename} (${formatBytes(payload.received)} / ${formatBytes(payload.total_size)})`;
    return;
  }
  const file = files.get(payload.file_id) || payload;
  file.received = payload.received;
  file.total_size = payload.total_size;
  files.set(payload.file_id, file);
  renderFiles();
}

function renderFiles() {
  fileListEl.innerHTML = "";
  files.forEach((file, id) => {
    const li = document.createElement("li");
    li.className = "file-entry";
    const meta = document.createElement("div");
    meta.className = "file-meta";
    const progress = file.received && file.total_size ? ` (${Math.floor((file.received / file.total_size) * 100)}%)` : "";
    meta.textContent = `${file.filename} • ${formatBytes(file.total_size || 0)}${progress}`;
    const actions = document.createElement("div");
    actions.className = "file-actions";
    const downloadBtn = document.createElement("button");
    downloadBtn.textContent = "Download";
    downloadBtn.addEventListener("click", () => sendControl("file_download", { file_id: id }));
    actions.appendChild(downloadBtn);
    li.append(meta, actions);
    fileListEl.appendChild(li);
  });
}

function formatBytes(size) {
  if (!size) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let idx = 0;
  let value = size;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(1)} ${units[idx]}`;
}

function updateControlButtons() {
  micToggleBtn.classList.toggle("active", micEnabled);
  micToggleBtn.querySelector(".label").textContent = micEnabled ? "Mic On" : "Mic Off";
  videoToggleBtn.classList.toggle("active", videoEnabled);
  videoToggleBtn.querySelector(".label").textContent = videoEnabled ? "Camera On" : "Camera Off";
  const isPresenter = currentPresenter && currentPresenter === currentUsername;
  presentToggleBtn.classList.toggle("active", Boolean(isPresenter));
  presentToggleBtn.querySelector(".label").textContent = isPresenter ? "Stop Sharing" : "Share Screen";
}

function setPresenterState(username) {
  currentPresenter = username;
  updateStatusLine();
  updateControlButtons();
}

function resetLeaveFlow() {
  if (leaveTimerId) {
    clearTimeout(leaveTimerId);
    leaveTimerId = null;
  }
  leaveDeadlineMs = null;
  leaveSection.classList.add("hidden");
  joinSection.classList.remove("hidden");
  leaveCountdownEl.textContent = Math.round(LEAVE_GRACE_PERIOD_MS / 1000).toString();
}

function beginLeaveCountdown() {
  if (!joined || leaveButton.disabled) {
    return;
  }
  setConnectedUi(false);
  leaveButton.disabled = true;
  leaveDeadlineMs = Date.now() + LEAVE_GRACE_PERIOD_MS;
  joinOverlay.classList.remove("hidden");
  joinSection.classList.add("hidden");
  leaveSection.classList.remove("hidden");
  updateLeaveCountdown();
}

function updateLeaveCountdown() {
  if (leaveDeadlineMs === null) {
    return;
  }
  const remainingMs = leaveDeadlineMs - Date.now();
  if (remainingMs <= 0) {
    confirmLeave(true);
    return;
  }
  const seconds = Math.max(1, Math.ceil(remainingMs / 1000));
  leaveCountdownEl.textContent = seconds.toString();
  leaveTimerId = setTimeout(updateLeaveCountdown, 250);
}

function cancelLeaveCountdown() {
  if (!joined) {
    return;
  }
  resetLeaveFlow();
  joinOverlay.classList.add("hidden");
  setConnectedUi(true);
  leaveButton.disabled = false;
  updateControlButtons();
}

function confirmLeave(autoTriggered = false) {
  if (leaveTimerId) {
    clearTimeout(leaveTimerId);
    leaveTimerId = null;
  }
  if (leaveDeadlineMs === null && !autoTriggered) {
    return;
  }
  leaveDeadlineMs = null;
  leaveSection.classList.add("hidden");
  joinSection.classList.remove("hidden");
  joinOverlay.classList.remove("hidden");
  joined = false;
  micEnabled = false;
  videoEnabled = false;
  setConnectedUi(false);
  const accepted = sendControl("leave_session", { force: true, auto: autoTriggered });
  setJoinStatus(accepted ? "Disconnecting…" : "Unable to signal disconnect", !accepted);
  updateControlButtons();
  updateStatusLine("Disconnecting…");
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!joined) return;
  const message = chatInput.value.trim();
  if (!message) return;
  sendControl("chat_send", { message });
  chatInput.value = "";
});

uploadButton.addEventListener("click", async () => {
  if (!joined || !fileInput.files?.length) return;
  const data = new FormData();
  data.append("file", fileInput.files[0]);
  try {
    await fetch("/api/files/upload", {
      method: "POST",
      body: data,
    });
  } catch (err) {
    console.error("Upload failed", err);
  }
});

joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!socketReady) {
    setJoinStatus("Waiting for client service…", true);
    return;
  }
  const desiredName = nameInput.value.trim();
  if (!desiredName) {
    setJoinStatus("Please enter a name", true);
    nameInput.focus();
    return;
  }
  joinButton.disabled = true;
  setJoinStatus(`Connecting as ${desiredName}…`, false);
  const accepted = sendControl("join", { username: desiredName });
  if (!accepted) {
    joinButton.disabled = false;
    setJoinStatus("Connection lost. Retrying…", true);
  }
});

randomNameBtn.addEventListener("click", () => {
  requestRandomName();
});

micToggleBtn.addEventListener("click", () => {
  if (micToggleBtn.disabled) return;
  micEnabled = !micEnabled;
  updateControlButtons();
  sendControl("toggle_audio", { enabled: micEnabled });
});

videoToggleBtn.addEventListener("click", () => {
  if (videoToggleBtn.disabled) return;
  videoEnabled = !videoEnabled;
  updateControlButtons();
  sendControl("toggle_video", { enabled: videoEnabled });
});

presentToggleBtn.addEventListener("click", () => {
  if (presentToggleBtn.disabled) return;
  const wantToShare = !(currentPresenter && currentPresenter === currentUsername);
  sendControl("toggle_presentation", { enabled: wantToShare });
});

leaveButton.addEventListener("click", beginLeaveCountdown);
cancelLeaveBtn.addEventListener("click", cancelLeaveCountdown);
confirmLeaveBtn.addEventListener("click", () => confirmLeave(false));

init();
