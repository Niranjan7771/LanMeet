const statusEl = document.getElementById("status");
const chatMessagesEl = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const participantListEl = document.getElementById("participant-list");
const fileListEl = document.getElementById("file-list");
const uploadButton = document.getElementById("upload-file");
const fileInput = document.getElementById("file-input");
const startScreenBtn = document.getElementById("start-screen");
const stopScreenBtn = document.getElementById("stop-screen");
const screenPreviewEl = document.getElementById("screen-preview");
const videoGridEl = document.getElementById("video-grid");

let socket;
let participants = new Set();
let files = new Map();
let currentUsername = null;
let currentPresenter = null;
const videoElements = new Map();

function connectSocket() {
  socket = new WebSocket(`ws://${window.location.host}/ws/control`);
  socket.addEventListener("open", () => {
    statusEl.textContent = "Connected";
    statusEl.classList.remove("error");
  });
  socket.addEventListener("close", () => {
    statusEl.textContent = "Disconnected. Reconnecting…";
    statusEl.classList.add("error");
    setTimeout(connectSocket, 2000);
  });
  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    handleServerEvent(data.type, data.payload);
  });
}

function sendControl(type, payload = {}) {
  socket?.send(
    JSON.stringify({
      type,
      payload,
    })
  );
}

function handleServerEvent(type, payload) {
  switch (type) {
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
      currentPresenter = payload.username;
      if (currentPresenter === currentUsername) {
        startScreenBtn.disabled = true;
        stopScreenBtn.disabled = false;
      } else {
        startScreenBtn.disabled = false;
        stopScreenBtn.disabled = true;
      }
      break;
    case "presenter_revoked":
      if (currentPresenter === payload.username) {
        currentPresenter = null;
        startScreenBtn.disabled = false;
        stopScreenBtn.disabled = true;
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
    default:
      console.debug("Unhandled event", type, payload);
  }
}

function initState(payload) {
  statusEl.textContent = `Connected as ${payload.username}`;
  participants = new Set(payload.peers || []);
  currentUsername = payload.username;
  startScreenBtn.disabled = false;
  stopScreenBtn.disabled = true;
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
  participants.forEach((username) => ensureVideoTile(username));
  ensureVideoTile(currentUsername);
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
  if (username === currentUsername) {
    return;
  }
  if (!frame) {
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

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  sendControl("chat_send", { message });
  chatInput.value = "";
});

startScreenBtn.addEventListener("click", () => {
  sendControl("request_presenter");
});

stopScreenBtn.addEventListener("click", () => {
  sendControl("release_presenter");
});

uploadButton.addEventListener("click", async () => {
  if (!fileInput.files?.length) return;
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

connectSocket();
