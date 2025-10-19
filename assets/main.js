const statusEl = document.getElementById("status");
const chatMessagesEl = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const participantListEl = document.getElementById("participant-list");
const fileListEl = document.getElementById("file-list");
const uploadButton = document.getElementById("upload-file");
const fileInput = document.getElementById("file-input");
const stageEl = document.getElementById("meeting-stage");
const screenWrapperEl = document.getElementById("screen-wrapper");
const screenStatusEl = document.getElementById("screen-status");
const screenImageEl = document.getElementById("screen-image");
const videoGridEl = document.getElementById("video-grid");
const participantCountDisplay = document.getElementById("participant-count-display");

// Sidebar elements
const participantsSidebar = document.getElementById("participants-sidebar");
const chatSidebar = document.getElementById("chat-sidebar");
const toggleParticipantsBtn = document.getElementById("toggle-participants-btn");
const toggleChatBtn = document.getElementById("toggle-chat-btn");
const closeParticipantsBtn = document.getElementById("close-participants-btn");
const closeChatBtn = document.getElementById("close-chat-btn");

// Tab elements
const tabBtns = document.querySelectorAll(".tab-btn");
const tabContents = document.querySelectorAll(".tab-content");

const VIDEO_PLACEHOLDER_TEXT = "Waiting for participants...";
const VIDEO_IDLE_TIMEOUT_MS = 4000;
const VIDEO_STALE_CHECK_INTERVAL_MS = 1500;
const SCREEN_IDLE_TIMEOUT_MS = 4500;
const SCREEN_STALE_CHECK_INTERVAL_MS = 1500;
const BLANK_SCREEN_DATA_URL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";

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
const videoLastFrameAt = new Map();
let leaveTimerId = null;
let leaveDeadlineMs = null;
const LEAVE_GRACE_PERIOD_MS = 20000;

const avatarStyleCache = new Map();
const peerMedia = new Map();
let screenLastFrameAt = null;
let screenWatchdogTimerId = null;

function getAvatarInitial(username) {
  if (!username) {
    return "?";
  }
  const trimmed = username.trim();
  if (!trimmed) {
    return "?";
  }
  return trimmed[0].toUpperCase();
}

function getAvatarGradient(username) {
  const key = username || "";
  if (avatarStyleCache.has(key)) {
    return avatarStyleCache.get(key);
  }
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash << 5) - hash + key.charCodeAt(i);
    hash |= 0; // keep in 32-bit range
  }
  const hue = Math.abs(hash) % 360;
  const secondaryHue = (hue + 35) % 360;
  const gradient = `linear-gradient(135deg, hsl(${hue} 65% 72%), hsl(${secondaryHue} 70% 62%))`;
  avatarStyleCache.set(key, gradient);
  return gradient;
}

function createAvatarElement(username) {
  const avatar = document.createElement("div");
  avatar.className = "video-avatar";
  avatar.textContent = getAvatarInitial(username);
  avatar.style.background = getAvatarGradient(username);
  avatar.setAttribute("aria-hidden", "true");
  return avatar;
}

let videoWatchdogTimerId = null;

function init() {
  setConnectedUi(false);
  joinButton.disabled = true;
  fetchConfig();
  connectSocket();
  ensureVideoPlaceholder();
  updateStagePresenterState(false);
  showScreenMessage("No presenter");
  startVideoWatchdog();
  startScreenWatchdog();
  
  // Setup sidebar toggle
  toggleParticipantsBtn.addEventListener("click", toggleParticipantsSidebar);
  toggleChatBtn.addEventListener("click", toggleChatSidebar);
  closeParticipantsBtn.addEventListener("click", () => hideSidebar(participantsSidebar));
  closeChatBtn.addEventListener("click", () => hideSidebar(chatSidebar));
  
  // Setup tab switching
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function toggleParticipantsSidebar() {
  if (participantsSidebar.classList.contains("hidden")) {
    showSidebar(participantsSidebar);
    hideSidebar(chatSidebar);
    toggleParticipantsBtn.classList.add("active");
    toggleChatBtn.classList.remove("active");
  } else {
    hideSidebar(participantsSidebar);
    toggleParticipantsBtn.classList.remove("active");
  }
}

function toggleChatSidebar() {
  if (chatSidebar.classList.contains("hidden")) {
    showSidebar(chatSidebar);
    hideSidebar(participantsSidebar);
    toggleChatBtn.classList.add("active");
    toggleParticipantsBtn.classList.remove("active");
  } else {
    hideSidebar(chatSidebar);
    toggleChatBtn.classList.remove("active");
  }
}

function showSidebar(sidebar) {
  sidebar.classList.remove("hidden");
}

function hideSidebar(sidebar) {
  sidebar.classList.add("hidden");
}

function switchTab(tabName) {
  // Hide all tabs
  tabContents.forEach((content) => {
    content.classList.remove("active");
  });
  
  // Remove active from all buttons
  tabBtns.forEach((btn) => {
    btn.classList.remove("active");
  });
  
  // Show selected tab
  const activeTab = document.getElementById(`${tabName}-tab`);
  if (activeTab) {
    activeTab.classList.add("active");
  }
  
  // Set active button
  const activeBtn = document.querySelector(`[data-tab="${tabName}"]`);
  if (activeBtn) {
    activeBtn.classList.add("active");
  }
}

function flashStatus(message, severity = "info", duration = 4000) {
  if (!message) return;
  statusEl.textContent = message;
  statusEl.classList.toggle("error", severity === "error");
  statusEl.classList.toggle("warning", severity === "warning");
  if (duration > 0) {
    setTimeout(() => {
      statusEl.textContent = "";
      statusEl.classList.remove("error", "warning");
    }, duration);
  }
}

function ensureVideoPlaceholder() {
  if (!videoGridEl) return;
  const placeholder = videoGridEl.querySelector(".placeholder");
  const hasTiles = Boolean(videoGridEl.querySelector(".video-tile"));
  if (hasTiles && placeholder) {
    placeholder.remove();
    return;
  }
  if (!hasTiles && !placeholder) {
    const el = document.createElement("p");
    el.className = "placeholder";
    el.textContent = VIDEO_PLACEHOLDER_TEXT;
    videoGridEl.appendChild(el);
  }
}

function markVideoFrameReceived(username) {
  videoLastFrameAt.set(username, performance.now());
  const img = videoElements.get(username);
  if (!img) {
    return;
  }
  const tile = img.parentElement;
  if (tile) {
    tile.classList.add("has-frame");
    tile.classList.remove("video-muted");
  }
}

function markVideoInactive(username) {
  videoLastFrameAt.delete(username);
  const img = videoElements.get(username);
  if (!img) {
    return;
  }
  img.src = "";
  img.removeAttribute("src");
  const tile = img.parentElement;
  if (tile) {
    tile.classList.add("video-muted");
    tile.classList.remove("has-frame");
  }
}

function applyVideoEnabledState(username, enabled) {
  if (!username) {
    return;
  }
  ensureVideoTile(username);
  if (!enabled) {
    markVideoInactive(username);
    return;
  }
  videoLastFrameAt.delete(username);
  const img = videoElements.get(username);
  if (!img) {
    return;
  }
  const tile = img.parentElement;
  if (tile) {
    tile.classList.remove("video-muted");
  }
}

function updatePeerMedia(username, state) {
  if (typeof username !== "string" || !username) {
    return;
  }
  const previous = peerMedia.get(username) || { audio_enabled: false, video_enabled: false };
  const next = { ...previous };
  if (state && typeof state === "object") {
    if (Object.prototype.hasOwnProperty.call(state, "audio_enabled")) {
      next.audio_enabled = Boolean(state.audio_enabled);
    }
    if (Object.prototype.hasOwnProperty.call(state, "video_enabled")) {
      next.video_enabled = Boolean(state.video_enabled);
      applyVideoEnabledState(username, next.video_enabled);
    }
  }
  peerMedia.set(username, next);
}

function applyMediaSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") {
    return;
  }
  Object.entries(snapshot).forEach(([username, state]) => {
    updatePeerMedia(username, state);
  });
}

function startVideoWatchdog() {
  if (videoWatchdogTimerId !== null) {
    return;
  }
  videoWatchdogTimerId = window.setInterval(() => {
    const now = performance.now();
    videoElements.forEach((img, username) => {
      const lastFrameAt = videoLastFrameAt.get(username);
      if (lastFrameAt && now - lastFrameAt > VIDEO_IDLE_TIMEOUT_MS) {
        markVideoInactive(username);
      }
    });
  }, VIDEO_STALE_CHECK_INTERVAL_MS);
}

function startScreenWatchdog() {
  if (screenWatchdogTimerId !== null) {
    return;
  }
  screenWatchdogTimerId = window.setInterval(() => {
    if (!currentPresenter || screenLastFrameAt === null) {
      return;
    }
    const now = performance.now();
    if (now - screenLastFrameAt > SCREEN_IDLE_TIMEOUT_MS) {
      screenLastFrameAt = null;
      setPresenterState(null);
    }
  }, SCREEN_STALE_CHECK_INTERVAL_MS);
}

function updateStagePresenterState(hasPresenter) {
  if (stageEl) {
    stageEl.classList.toggle("has-presenter", hasPresenter);
  }
  if (screenWrapperEl) {
    if (hasPresenter) {
      screenWrapperEl.classList.remove("hidden");
    } else {
      screenWrapperEl.classList.add("hidden");
    }
  }
}

function showScreenMessage(message) {
  clearScreenFrame();
  if (screenStatusEl) {
    screenStatusEl.textContent = message;
  }
}

async function fetchConfig() {
  try {
    const res = await fetch("/api/config");
    const config = await res.json();
    if (config && config.server_host && config.tcp_port) {
      nameInput.value = generateLocalName();
      joinButton.disabled = false;
    }
  } catch (error) {
    console.error("Config fetch error", error);
  }
}

async function requestRandomName() {
  try {
    const res = await fetch("/api/random-name");
    const data = await res.json();
    if (data.name) {
      nameInput.value = data.name;
    }
  } catch (error) {
    console.error("Random name error", error);
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
    flashStatus("Connected to server", "info", 2000);
  });
  socket.addEventListener("close", () => {
    socketReady = false;
    if (!joined) {
      flashStatus("Disconnected from server", "warning", 3000);
    }
  });
  socket.addEventListener("message", (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleServerEvent(msg.type, msg.payload || {});
    } catch (err) {
      console.error("Message parse error", err);
    }
  });
}

function sendControl(type, payload = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    console.warn("Socket not ready");
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
      handleSessionStatus(payload);
      break;
    case "state_snapshot":
      handleStateSnapshot(payload);
      break;
    case "welcome":
      handleWelcome(payload);
      break;
    case "presenter_granted":
      handlePresenterGranted(payload);
      break;
    case "presenter_revoked":
      handlePresenterRevoked(payload);
      break;
    case "user_joined":
      handleUserJoined(payload);
      break;
    case "user_left":
      handleUserLeft(payload);
      break;
    case "peer_joined":
      syncParticipantsFromServer(payload.peers || []);
      break;
    case "peer_left":
      syncParticipantsFromServer(payload.peers || []);
      break;
    case "presenter_changed":
      setPresenterState(payload.presenter || null);
      break;
    case "chat_message":
      appendChatMessage(payload);
      break;
    case "video_frame":
      updateVideoTile(payload.username, payload.frame);
      break;
    case "video_status":
      handleVideoStatus(payload);
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
    default:
      console.log("Unknown event type", type);
  }
}

function handleSessionStatus({ state, username, message }) {
  switch (state) {
    case "connected":
      joined = true;
      currentUsername = username;
      updateStatusLine();
      setConnectedUi(true);
      joinOverlay.classList.add("hidden");
      break;
    case "connecting":
      setJoinStatus(`Connecting as ${username}...`, false);
      break;
    case "error":
      joined = false;
      setJoinStatus(message || "Connection error", true);
      break;
    case "idle":
      joined = false;
      setJoinStatus("Idle", false);
      break;
    default:
      console.log("Unknown session state", state);
  }
}

function setJoinStatus(text, isError) {
  joinStatusEl.textContent = text;
  joinStatusEl.classList.toggle("error", Boolean(isError));
}

function updateStatusLine(fallback) {
  if (!joined || !currentUsername) {
    statusEl.textContent = fallback || "Offline";
    statusEl.classList.add("error");
    return;
  }
  const pieces = [`Connected as ${currentUsername}`];
  if (currentPresenter) {
    pieces.push(`Presenter: ${currentPresenter}`);
  }
  statusEl.textContent = pieces.join(" • ");
  statusEl.classList.remove("error");
  statusEl.classList.remove("warning");
}

function setConnectedUi(enabled) {
  chatInput.disabled = !enabled;
  uploadButton.disabled = !enabled;
  fileInput.disabled = !enabled;
  micToggleBtn.disabled = !enabled;
  videoToggleBtn.disabled = !enabled;
  presentToggleBtn.disabled = !enabled;
  leaveButton.disabled = !enabled;
  toggleParticipantsBtn.disabled = !enabled;
  toggleChatBtn.disabled = !enabled;
}

function initState(payload) {
  joined = true;
  currentUsername = payload.username;
  micEnabled = false;
  videoEnabled = false;
  updateStatusLine();
  participants = new Set(payload.peers || []);
  ensureSelfInParticipants();
  peerMedia.clear();
  setPresenterState(payload.presenter || null);
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
  ensureVideoPlaceholder();
  participants.forEach((name) => ensureVideoTile(name));
  ensureVideoTile(currentUsername);
  applyMediaSnapshot(payload.media_state || payload.peer_media);
  resetLeaveFlow();
  updateControlButtons();
  updateParticipantSummary();
}

function handleStateSnapshot(snapshot) {
  if (!snapshot || !snapshot.connected) {
    initState(snapshot || {});
    return;
  }
  joined = true;
  currentUsername = snapshot.username || currentUsername;
  if (currentUsername) {
    ensureSelfInParticipants();
  }
  participants = new Set(snapshot.peers || []);
  ensureSelfInParticipants();
  renderParticipants();
  chatMessagesEl.innerHTML = "";
  (snapshot.chat_history || []).forEach((msg) => appendChatMessage(msg));
  files = new Map();
  (snapshot.files || []).forEach((file) => {
    files.set(file.file_id, file);
  });
  renderFiles();
  const media = snapshot.media || {};
  micEnabled = Boolean(media.audio_enabled);
  videoEnabled = Boolean(media.video_enabled);
  setPresenterState(snapshot.presenter || null);
  updateControlButtons();
  videoElements.clear();
  videoGridEl.innerHTML = "";
  ensureVideoPlaceholder();
  participants.forEach((name) => ensureVideoTile(name));
  ensureVideoTile(currentUsername);
  peerMedia.clear();
  applyMediaSnapshot(snapshot.peer_media || snapshot.media_state);
  setConnectedUi(true);
  leaveButton.disabled = false;
  joinOverlay.classList.add("hidden");
  updateStatusLine();
  sendControl("file_request_list");
  updateParticipantSummary();
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
  ensureSelfInParticipants();
  Array.from(participants)
    .sort((a, b) => a.localeCompare(b))
    .forEach((username) => {
      const li = document.createElement("li");
      li.textContent = username === currentUsername ? `${username} (You)` : username;
      participantListEl.appendChild(li);
    });
  updateParticipantSummary();
}

function ensureVideoTile(username) {
  if (!username || videoElements.has(username)) {
    return;
  }
  const tile = document.createElement("div");
  tile.className = "video-tile";
  tile.dataset.username = username;
  const label = document.createElement("div");
  label.className = "video-label";
  const isSelf = username === currentUsername;
  label.textContent = isSelf ? "You" : username;
  const img = document.createElement("img");
  img.alt = `${username} video`;
  img.addEventListener("load", () => {
    markVideoFrameReceived(username);
  });
  const avatar = createAvatarElement(username);
  tile.append(avatar, img, label);
  if (isSelf) {
    tile.classList.add("self-video");
  } else {
    tile.classList.remove("self-video");
  }
  videoElements.set(username, img);
  videoGridEl.appendChild(tile);
  updatePresenterHighlight();
  ensureVideoPlaceholder();
  markVideoInactive(username);
}

function removeVideoTile(username) {
  const img = videoElements.get(username);
  if (!img) return;
  videoElements.delete(username);
  const tile = img.parentElement;
  tile?.remove();
  ensureVideoPlaceholder();
  videoLastFrameAt.delete(username);
}

function updateVideoTile(username, frame) {
  ensureVideoTile(username);
  const img = videoElements.get(username);
  if (img) {
    img.src = `data:image/jpeg;base64,${frame}`;
    markVideoFrameReceived(username);
  }
}

function handleScreenControl({ state, username }) {
  if (state === "start") {
    currentPresenter = username;
    screenLastFrameAt = null;
    updateStatusLine();
    updateControlButtons();
    updateStagePresenterState(true);
    showScreenMessage(`${username} is sharing their screen`);
    updatePresenterHighlight();
  }
  if (state === "stop") {
    if (currentPresenter === username) {
      currentPresenter = null;
      screenLastFrameAt = null;
      updateStatusLine();
      updateControlButtons();
      updateStagePresenterState(false);
      showScreenMessage("Screen sharing stopped");
      updatePresenterHighlight();
    }
  }
}

function handleScreenFrame({ frame, username }) {
  if (!frame) {
    return;
  }
  updateStagePresenterState(true);
  if (username === currentUsername) {
    return;
  }
  if (!screenImageEl) {
    return;
  }
  screenImageEl.removeAttribute("style");
  screenImageEl.src = `data:image/jpeg;base64,${frame}`;
  screenImageEl.alt = `${username} shared screen`;
  screenWrapperEl?.classList.add("has-content");
  screenLastFrameAt = performance.now();
}

function clearScreenFrame() {
  screenLastFrameAt = null;
  if (screenImageEl) {
    screenImageEl.src = BLANK_SCREEN_DATA_URL;
    screenImageEl.removeAttribute("src");
    screenImageEl.removeAttribute("alt");
  }
  screenWrapperEl?.classList.remove("has-content");
}

function handleFileOffer(payload) {
  if (payload.files) {
    files = new Map(payload.files.map((f) => [f.file_id, f]));
  } else if (payload.file_id) {
    files.set(payload.file_id, payload);
  }
  renderFiles();
}

function handleFileProgress(payload) {
  if (!payload.file_id) {
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
    const info = document.createElement("div");
    info.textContent = `${file.filename} (${formatBytes(file.total_size)})`;
    if (file.received && file.total_size) {
      const progress = Math.round((file.received / file.total_size) * 100);
      info.textContent += ` - ${progress}%`;
    }
    const actions = document.createElement("div");
    actions.className = "file-actions";
    const downloadBtn = document.createElement("button");
    downloadBtn.textContent = "⬇️";
    downloadBtn.onclick = () => downloadFile(id);
    actions.appendChild(downloadBtn);
    li.append(info, actions);
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

function downloadFile(fileId) {
  window.location.href = `/api/files/download/${fileId}`;
}

function updateControlButtons() {
  micToggleBtn.classList.toggle("active", micEnabled);
  videoToggleBtn.classList.toggle("active", videoEnabled);
  const selfTileImg = videoElements.get(currentUsername);
  if (selfTileImg) {
    const tile = selfTileImg.parentElement;
    if (tile) {
      tile.classList.toggle("video-muted", !videoEnabled);
      if (!videoEnabled) {
        markVideoInactive(currentUsername);
      }
    }
  }
  const isPresenter = currentPresenter && currentPresenter === currentUsername;
  presentToggleBtn.classList.toggle("active", Boolean(isPresenter));
  const presenterLocked = currentPresenter && currentPresenter !== currentUsername;
  presentToggleBtn.classList.toggle("locked", Boolean(presenterLocked));
  presentToggleBtn.title = presenterLocked
    ? `${currentPresenter} is currently presenting.`
    : "Start or stop sharing your screen";
}

function setPresenterState(username) {
  currentPresenter = username;
  if (!username) {
    screenLastFrameAt = null;
  }
  updateStatusLine();
  updateControlButtons();
  const hasPresenter = Boolean(username);
  updateStagePresenterState(hasPresenter);
  if (username && username !== currentUsername) {
    showScreenMessage(`${username} is presenting`);
  } else if (hasPresenter) {
    showScreenMessage("You are presenting");
  } else {
    showScreenMessage("No presenter");
  }
  updatePresenterHighlight();
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
  setJoinStatus(accepted ? "Disconnecting..." : "Unable to signal disconnect", !accepted);
  updateControlButtons();
  updateStatusLine("Disconnecting...");
}

function ensureSelfInParticipants() {
  if (currentUsername) {
    participants.add(currentUsername);
  }
}

function updateParticipantSummary() {
  ensureSelfInParticipants();
  if (participantCountDisplay) {
    const count = participants.size;
    participantCountDisplay.textContent = `${count} ${count === 1 ? "participant" : "participants"}`;
  }
  updatePresenterHighlight();
  ensureVideoPlaceholder();
}

function syncParticipantsFromServer(list) {
  if (!Array.isArray(list)) {
    return false;
  }
  const filtered = list
    .filter((name) => typeof name === "string")
    .map((name) => name.trim())
    .filter((name) => name.length > 0);
  participants = new Set(filtered);
  Array.from(peerMedia.keys()).forEach((username) => {
    if (username !== currentUsername && !participants.has(username)) {
      peerMedia.delete(username);
    }
  });
  renderParticipants();
  const known = new Set(participants);
  if (currentUsername) {
    known.add(currentUsername);
  }
  known.forEach((name) => ensureVideoTile(name));
  videoElements.forEach((_, username) => {
    if (!known.has(username)) {
      removeVideoTile(username);
    }
  });
  return true;
}

function handleWelcome(payload) {
  initState(payload || {});
}

function handleUserJoined(payload) {
  const updated = syncParticipantsFromServer(payload.participants || []);
  if (!updated) {
    const username = typeof payload.username === "string" ? payload.username : null;
    if (username) {
      participants.add(username);
      renderParticipants();
      ensureVideoTile(username);
    }
  }
  const username = typeof payload.username === "string" ? payload.username : null;
  if (username) {
    updatePeerMedia(username, payload);
  }
}

function handleUserLeft(payload) {
  syncParticipantsFromServer(payload.participants || []);
  const username = typeof payload.username === "string" ? payload.username : null;
  if (username) {
    peerMedia.delete(username);
  }
}

function handleVideoStatus(payload) {
  const username = typeof payload.username === "string" ? payload.username : null;
  if (!username) {
    return;
  }
  updatePeerMedia(username, payload);
}

function handlePresenterGranted(payload) {
  const username = typeof payload.username === "string" ? payload.username : null;
  setPresenterState(username);
}

function handlePresenterRevoked(payload) {
  const username = typeof payload.username === "string" ? payload.username : null;
  if (!username || currentPresenter === username) {
    setPresenterState(null);
  }
}

function updatePresenterHighlight() {
  const hasPresenter = Boolean(currentPresenter);
  document.body.classList.toggle("presenter-active", hasPresenter);
  videoElements.forEach((img, username) => {
    const tile = img.parentElement;
    if (tile) {
      tile.classList.toggle("presenter", username === currentPresenter);
    }
  });
}

// Event Listeners
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
    const res = await fetch("/api/files/upload", { method: "POST", body: data });
    if (res.ok) {
      fileInput.value = "";
    }
  } catch (err) {
    console.error("Upload error", err);
  }
});

joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!socketReady) {
    setJoinStatus("Socket not ready, please try again", true);
    return;
  }
  const desiredName = nameInput.value.trim();
  if (!desiredName) {
    setJoinStatus("Please enter a name", true);
    return;
  }
  joinButton.disabled = true;
  setJoinStatus(`Connecting as ${desiredName}...`, false);
  const accepted = sendControl("join", { username: desiredName });
  if (!accepted) {
    setJoinStatus("Failed to send join request", true);
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
  if (currentUsername) {
    applyVideoEnabledState(currentUsername, videoEnabled);
    updatePeerMedia(currentUsername, { video_enabled: videoEnabled });
  }
  sendControl("toggle_video", { enabled: videoEnabled });
});

presentToggleBtn.addEventListener("click", () => {
  if (presentToggleBtn.disabled) return;
  if (currentPresenter && currentPresenter !== currentUsername) {
    flashStatus(`${currentPresenter} is presenting`, "warning", 2000);
    return;
  }
  const wantToShare = !(currentPresenter && currentPresenter === currentUsername);
  sendControl("toggle_presentation", { enabled: wantToShare });
});

leaveButton.addEventListener("click", beginLeaveCountdown);
cancelLeaveBtn.addEventListener("click", cancelLeaveCountdown);
confirmLeaveBtn.addEventListener("click", () => confirmLeave(false));

init();
