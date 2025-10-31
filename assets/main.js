const statusEl = document.getElementById("status");
const chatMessagesEl = document.getElementById("chat-messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatInputOverlay = document.getElementById("chat-input-overlay");
const participantListEl = document.getElementById("participant-list");
const mentionPopup = document.getElementById("mention-popup");
const fileListEl = document.getElementById("file-list");
const uploadButton = document.getElementById("upload-file");
const fileInput = document.getElementById("file-input");
const stageEl = document.getElementById("meeting-stage");
const screenWrapperEl = document.getElementById("screen-wrapper");
const screenStatusEl = document.getElementById("screen-status");
const screenImageEl = document.getElementById("screen-image");
const videoGridEl = document.getElementById("video-grid");
const participantCountDisplay = document.getElementById("participant-count-display");
const typingIndicatorEl = document.getElementById("typing-indicator");
const latencyBadgeEl = document.getElementById("latency-badge");
const meetingTimerEl = document.getElementById("meeting-timer");
const reactionsBtn = document.getElementById("reactions-btn");
const reactionPicker = document.getElementById("reaction-picker");
const reactionOptions = reactionPicker ? Array.from(reactionPicker.querySelectorAll(".reaction-option")) : [];
const reactionStreamEl = document.getElementById("reaction-stream");
const dragOverlay = document.getElementById("drag-overlay");
const chatTabContent = document.getElementById("chat-tab");
const filesTabContent = document.getElementById("files-tab");
const chatTabBtn = document.querySelector('.tab-btn[data-tab="chat"]');
const filesTabBtn = document.querySelector('.tab-btn[data-tab="files"]');

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

const TIME_LIMIT_LEAVE_REASON = "Meeting time limit reached";
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
const handToggleBtn = document.getElementById("toggle-hand");
const leaveButton = document.getElementById("leave-session");
const leaveCountdownEl = document.getElementById("leave-countdown");
const leaveSection = document.getElementById("leave-confirm");
const joinSection = document.getElementById("join-section");
const cancelLeaveBtn = document.getElementById("cancel-leave");
const confirmLeaveBtn = document.getElementById("confirm-leave");
const kickedNotice = document.getElementById("kicked-notice");
const kickedMessageEl = document.getElementById("kicked-message");
const adminBannerEl = document.getElementById("admin-banner");

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
let wasKicked = false;
let kickedReason = "";

let presenceMap = new Map();
let localHandRaised = false;
let lastLatencyMetrics = null;
let typingActive = false;
let typingDebounceId = null;
let typingResetId = null;
let reactionMenuOpen = false;
const pendingDownloads = new Set();
const pendingShareLinks = new Set();
let dragDepthCounter = 0;
let meetingTimerState = null;
let meetingTimerIntervalId = null;
let meetingTimerAutoLeaveTriggered = false;
let adminBannerTimeoutId = null;
let statusRestoreTimerId = null;
let persistentStatus = {
  text: statusEl ? statusEl.textContent || "" : "",
  severity: statusEl && statusEl.classList.contains("error")
    ? "error"
    : statusEl && statusEl.classList.contains("warning")
    ? "warning"
    : "info",
};
let suppressChatNotifications = false;
let suppressFileNotifications = false;
let chatUnreadCount = 0;
let filesUnreadCount = 0;
let chatToggleBadgeEl = null;
let chatTabBadgeEl = null;
let filesTabBadgeEl = null;

// Mention autocomplete state
let mentionStartPos = -1;
let mentionQuery = "";
let mentionSelectedIndex = -1;
let mentionMatches = [];

const TYPING_DEBOUNCE_MS = 250;
const TYPING_IDLE_TIMEOUT_MS = 3500;
const LATENCY_GOOD_MS = 120;
const LATENCY_WARN_MS = 250;
const REACTION_REPLAY_LIMIT = 5;
const MAX_REACTION_STREAM_ITEMS = 6;

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

function createParticipantAvatar(username) {
  const avatar = document.createElement("div");
  avatar.className = "participant-avatar";
  avatar.textContent = getAvatarInitial(username);
  avatar.style.background = getAvatarGradient(username);
  avatar.setAttribute("aria-hidden", "true");
  return avatar;
}

function normalizePresenceEntry(raw, options = {}) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const username = typeof raw.username === "string" ? raw.username.trim() : "";
  if (!username) {
    return null;
  }
  const partial = Boolean(options.partial);
  const coerceNumber = (value) => {
    if (value === null || value === undefined) {
      return null;
    }
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  };
  const entry = { username };
  const booleanKeys = ["audio_enabled", "video_enabled", "hand_raised", "is_typing", "is_presenter"];
  booleanKeys.forEach((key) => {
    if (partial) {
      if (Object.prototype.hasOwnProperty.call(raw, key)) {
        entry[key] = Boolean(raw[key]);
      }
    } else {
      entry[key] = Boolean(raw[key]);
    }
  });
  if (partial) {
    if (Object.prototype.hasOwnProperty.call(raw, "last_seen_seconds")) {
      entry.last_seen_seconds = coerceNumber(raw.last_seen_seconds) ?? 0;
    }
    if (Object.prototype.hasOwnProperty.call(raw, "latency_ms")) {
      entry.latency_ms = coerceNumber(raw.latency_ms);
    }
    if (Object.prototype.hasOwnProperty.call(raw, "jitter_ms")) {
      entry.jitter_ms = coerceNumber(raw.jitter_ms);
    }
  } else {
    entry.last_seen_seconds = coerceNumber(raw.last_seen_seconds) ?? 0;
    entry.latency_ms = coerceNumber(raw.latency_ms);
    entry.jitter_ms = coerceNumber(raw.jitter_ms);
  }
  return entry;
}

function refreshPresenceUi() {
  renderParticipants();
  updateParticipantSummary();
  updateTypingIndicator();
}

function applyPresenceSnapshot(entries) {
  if (!Array.isArray(entries)) {
    presenceMap = new Map();
    refreshPresenceUi();
    return;
  }
  const next = new Map();
  entries.forEach((raw) => {
    const normalized = normalizePresenceEntry(raw, { partial: false });
    if (normalized) {
      next.set(normalized.username, normalized);
      updatePeerMedia(normalized.username, {
        audio_enabled: normalized.audio_enabled,
        video_enabled: normalized.video_enabled,
      });
    }
  });
  presenceMap = next;
  participants = new Set(Array.from(presenceMap.keys()));
  ensureSelfInParticipants();
  const selfEntry = currentUsername ? presenceMap.get(currentUsername) : null;
  if (selfEntry) {
    localHandRaised = Boolean(selfEntry.hand_raised);
    updateLatencyBadge(selfEntry.latency_ms, selfEntry.jitter_ms);
    updateControlButtons();
  }
  refreshPresenceUi();
}

function updatePresenceEntry(entry) {
  if (!entry || !entry.username) {
    return;
  }
  const existing = presenceMap.get(entry.username) || { username: entry.username };
  const merged = { ...existing, ...entry };
  presenceMap.set(entry.username, merged);
  participants.add(entry.username);
  ensureVideoTile(entry.username);
  const mediaUpdate = {};
  if (Object.prototype.hasOwnProperty.call(entry, "audio_enabled") || !Object.prototype.hasOwnProperty.call(existing, "audio_enabled")) {
    if (Object.prototype.hasOwnProperty.call(merged, "audio_enabled")) {
      mediaUpdate.audio_enabled = merged.audio_enabled;
    }
  }
  if (Object.prototype.hasOwnProperty.call(entry, "video_enabled") || !Object.prototype.hasOwnProperty.call(existing, "video_enabled")) {
    if (Object.prototype.hasOwnProperty.call(merged, "video_enabled")) {
      mediaUpdate.video_enabled = merged.video_enabled;
    }
  }
  if (Object.keys(mediaUpdate).length > 0) {
    updatePeerMedia(entry.username, mediaUpdate);
  }
  if (entry.username === currentUsername) {
    localHandRaised = Boolean(merged.hand_raised);
    updateLatencyBadge(merged.latency_ms, merged.jitter_ms);
    updateControlButtons();
  }
  refreshPresenceUi();
}

function patchLocalPresence(partial) {
  if (!currentUsername) {
    return;
  }
  const existing = presenceMap.get(currentUsername) || { username: currentUsername };
  const merged = { ...existing, ...partial };
  presenceMap.set(currentUsername, merged);
  refreshPresenceUi();
  updateControlButtons();
}

function updateTypingIndicator() {
  if (!typingIndicatorEl) {
    return;
  }
  const typers = Array.from(presenceMap.values()).filter((entry) => entry.username !== currentUsername && entry.is_typing);
  if (typers.length === 0) {
    typingIndicatorEl.textContent = "";
    typingIndicatorEl.classList.add("hidden");
    return;
  }
  let message;
  if (typers.length === 1) {
    message = `${typers[0].username} is typing…`;
  } else if (typers.length === 2) {
    message = `${typers[0].username} and ${typers[1].username} are typing…`;
  } else {
    message = `${typers.length} people are typing…`;
  }
  typingIndicatorEl.textContent = message;
  typingIndicatorEl.classList.remove("hidden");
}

function updateLatencyBadge(latencyMs, jitterMs) {
  if (!latencyBadgeEl) {
    return;
  }
  latencyBadgeEl.classList.remove("latency-good", "latency-warn", "latency-bad");
  if (latencyMs === null || latencyMs === undefined || Number.isNaN(latencyMs)) {
    latencyBadgeEl.textContent = "Latency: --";
    lastLatencyMetrics = null;
    return;
  }
  const roundedLatency = Math.max(0, Math.round(latencyMs));
  let text = `Latency: ${roundedLatency} ms`;
  if (jitterMs !== null && jitterMs !== undefined && !Number.isNaN(jitterMs)) {
    const roundedJitter = Math.max(0, Math.round(jitterMs));
    text += ` • Jitter: ${roundedJitter} ms`;
  }
  latencyBadgeEl.textContent = text;
  if (roundedLatency <= LATENCY_GOOD_MS) {
    latencyBadgeEl.classList.add("latency-good");
  } else if (roundedLatency <= LATENCY_WARN_MS) {
    latencyBadgeEl.classList.add("latency-warn");
  } else {
    latencyBadgeEl.classList.add("latency-bad");
  }
  lastLatencyMetrics = {
    latency_ms: latencyMs,
    jitter_ms: jitterMs,
  };
}

function applyMeetingTimer(status) {
  if (!meetingTimerEl) {
    return;
  }
  if (!status || status.is_active !== true) {
    clearMeetingTimer();
    return;
  }
  meetingTimerState = {
    ...status,
    _received_at: Date.now() / 1000,
  };
  if (status.is_expired) {
    triggerTimeLimitExpiry();
  } else {
    meetingTimerAutoLeaveTriggered = false;
  }
  if (meetingTimerIntervalId !== null) {
    clearInterval(meetingTimerIntervalId);
  }
  updateMeetingTimerTick();
  meetingTimerIntervalId = window.setInterval(updateMeetingTimerTick, 1000);
}

function clearMeetingTimer() {
  if (meetingTimerIntervalId !== null) {
    clearInterval(meetingTimerIntervalId);
    meetingTimerIntervalId = null;
  }
  meetingTimerState = null;
  meetingTimerAutoLeaveTriggered = false;
  if (meetingTimerEl) {
    meetingTimerEl.textContent = "No time limit";
    meetingTimerEl.classList.remove("expired");
    meetingTimerEl.removeAttribute("title");
  }
}

function updateMeetingTimerTick() {
  if (!meetingTimerEl || !meetingTimerState) {
    return;
  }
  const remaining = computeMeetingTimerRemaining(meetingTimerState);
  const total = typeof meetingTimerState.duration_seconds === "number" ? meetingTimerState.duration_seconds : null;
  if (remaining <= 0) {
    meetingTimerEl.textContent = "Time limit reached";
    meetingTimerEl.classList.add("expired");
    triggerTimeLimitExpiry();
  } else {
    meetingTimerEl.textContent = formatMeetingTimer(remaining);
    meetingTimerEl.classList.toggle("expired", remaining <= 0);
  }
  if (total) {
    meetingTimerEl.setAttribute("title", `Total time ${formatMeetingTimer(total)}`);
  } else {
    meetingTimerEl.removeAttribute("title");
  }
}

function computeMeetingTimerRemaining(state) {
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
  return 0;
}

function formatMeetingTimer(totalSeconds) {
  if (!Number.isFinite(totalSeconds)) {
    return "--:--";
  }
  const value = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function triggerTimeLimitExpiry() {
  if (meetingTimerAutoLeaveTriggered || wasKicked || !joined) {
    return;
  }
  meetingTimerAutoLeaveTriggered = true;
  joined = false;
  flashStatus(`${TIME_LIMIT_LEAVE_REASON}. Disconnecting…`, "warning", 4000);
  setConnectedUi(false);
  leaveButton.disabled = true;
  setJoinFormEnabled(false);
  resetLeaveFlow();
  if (joinOverlay) {
    joinOverlay.classList.remove("hidden");
  }
  if (joinSection) {
    joinSection.classList.remove("hidden");
  }
  if (leaveSection) {
    leaveSection.classList.add("hidden");
  }
  setJoinStatus(`${TIME_LIMIT_LEAVE_REASON}. Disconnecting…`, false);
  updateStatusLine(TIME_LIMIT_LEAVE_REASON);
  sendControl("leave_session", { force: true, auto: true, reason: TIME_LIMIT_LEAVE_REASON });
}

function showAdminBanner(notice) {
  if (!adminBannerEl) {
    return;
  }
  const message = notice && typeof notice.message === "string" ? notice.message.trim() : "";
  if (!message) {
    hideAdminBanner();
    return;
  }
  const rawLevel = notice && typeof notice.level === "string" ? notice.level.toLowerCase() : "info";
  const allowedLevels = new Set(["info", "success", "warning", "error"]);
  const level = allowedLevels.has(rawLevel) ? rawLevel : "info";
  adminBannerEl.textContent = message;
  adminBannerEl.classList.remove("hidden", "banner-info", "banner-success", "banner-warning", "banner-error");
  adminBannerEl.classList.add(`banner-${level}`);
  if (adminBannerTimeoutId !== null) {
    clearTimeout(adminBannerTimeoutId);
  }
  const timeoutMs = notice && typeof notice.timeout_ms === "number" ? notice.timeout_ms : 12000;
  adminBannerTimeoutId = window.setTimeout(() => {
    hideAdminBanner();
  }, Math.max(3000, timeoutMs));
}

function hideAdminBanner() {
  if (!adminBannerEl) {
    return;
  }
  adminBannerEl.classList.add("hidden");
  adminBannerEl.classList.remove("banner-info", "banner-success", "banner-warning", "banner-error");
  if (adminBannerTimeoutId !== null) {
    clearTimeout(adminBannerTimeoutId);
    adminBannerTimeoutId = null;
  }
}

function copyTextToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).catch((err) => {
      console.warn("Clipboard write failed", err);
    });
  }
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.value = text;
    document.body.appendChild(input);
    input.select();
    try {
      document.execCommand("copy");
    } catch (err) {
      console.warn("Fallback clipboard copy failed", err);
    }
    document.body.removeChild(input);
    resolve();
  });
}

function spawnReaction(entry) {
  if (!reactionStreamEl || !entry || !entry.reaction) {
    return;
  }
  const burst = document.createElement("div");
  burst.className = "reaction-burst";
  burst.textContent = entry.reaction;
  if (entry.username) {
    burst.title = `${entry.username} reacted with ${entry.reaction}`;
  }
  reactionStreamEl.appendChild(burst);
  burst.addEventListener("animationend", () => {
    burst.remove();
  });
  while (reactionStreamEl.childElementCount > MAX_REACTION_STREAM_ITEMS) {
    const first = reactionStreamEl.firstElementChild;
    if (first) {
      first.remove();
    } else {
      break;
    }
  }
}

function replayRecentReactions(reactions) {
  if (!Array.isArray(reactions) || reactions.length === 0) {
    return;
  }
  const recent = reactions.slice(-REACTION_REPLAY_LIMIT);
  recent.forEach((reaction, index) => {
    setTimeout(() => spawnReaction(reaction), index * 180);
  });
}

function setReactionMenu(open) {
  if (!reactionPicker || !reactionsBtn) {
    return;
  }
  reactionMenuOpen = Boolean(open);
  reactionPicker.classList.toggle("hidden", !reactionMenuOpen);
  reactionsBtn.setAttribute("aria-expanded", reactionMenuOpen ? "true" : "false");
}

function closeReactionMenu() {
  setReactionMenu(false);
}

function handleReactionSelection(rawReaction) {
  const reaction = typeof rawReaction === "string" ? rawReaction.trim() : "";
  if (!reaction) {
    closeReactionMenu();
    return;
  }
  if (!joined) {
    flashStatus("Join the session to send reactions", "warning", 3000);
    closeReactionMenu();
    return;
  }
  sendControl("send_reaction", { reaction });
  spawnReaction({ username: currentUsername || "You", reaction });
  closeReactionMenu();
}

function isEditableTarget(target) {
  if (!target) {
    return false;
  }
  if (target.isContentEditable) {
    return true;
  }
  const tagName = target.tagName;
  return tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT";
}

function sendTypingState(isTyping) {
  const desired = Boolean(isTyping);
  if (typingActive === desired && desired) {
    return;
  }
  typingActive = desired;
  if (!joined) {
    return;
  }
  sendControl("typing", { is_typing: desired });
  if (!isTyping && typingResetId) {
    clearTimeout(typingResetId);
    typingResetId = null;
  }
}

function requestTypingStart() {
  if (!joined) {
    return;
  }
  if (typingDebounceId) {
    clearTimeout(typingDebounceId);
  }
  typingDebounceId = window.setTimeout(() => {
    sendTypingState(true);
    typingDebounceId = null;
  }, TYPING_DEBOUNCE_MS);
  scheduleTypingStop();
}

function scheduleTypingStop() {
  if (typingResetId) {
    clearTimeout(typingResetId);
  }
  typingResetId = window.setTimeout(() => {
    typingResetId = null;
    if (typingDebounceId) {
      clearTimeout(typingDebounceId);
      typingDebounceId = null;
    }
    if (typingActive) {
      sendTypingState(false);
    }
  }, TYPING_IDLE_TIMEOUT_MS);
}

function resetTypingState() {
  if (typingDebounceId) {
    clearTimeout(typingDebounceId);
    typingDebounceId = null;
  }
  if (typingResetId) {
    clearTimeout(typingResetId);
    typingResetId = null;
  }
  if (typingActive) {
    typingActive = false;
    if (joined) {
      sendControl("typing", { is_typing: false });
    }
  }
}

function setHandRaised(desired) {
  if (!joined || !currentUsername) {
    return;
  }
  const next = Boolean(desired);
  if (localHandRaised === next) {
    return;
  }
  localHandRaised = next;
  sendControl("toggle_hand", { hand_raised: next });
  patchLocalPresence({ hand_raised: next });
  updateControlButtons();
}

function handleGlobalKeydown(event) {
  if (event.key === "Escape" && reactionMenuOpen) {
    closeReactionMenu();
  }
  if (!joined) {
    return;
  }
  const target = event.target;
  const noModifier = !event.altKey && !event.ctrlKey && !event.metaKey;
  if (isEditableTarget(target)) {
    if (event.key === "Escape") {
      return;
    }
    if (!(event.ctrlKey || event.metaKey)) {
      return;
    }
  }
  if (!noModifier) {
    return;
  }
  switch (event.code) {
    case "KeyM":
      if (micToggleBtn && !micToggleBtn.disabled) {
        event.preventDefault();
        micToggleBtn.click();
      }
      break;
    case "KeyV":
      if (videoToggleBtn && !videoToggleBtn.disabled) {
        event.preventDefault();
        videoToggleBtn.click();
      }
      break;
    case "KeyH":
      if (handToggleBtn && !handToggleBtn.disabled) {
        event.preventDefault();
        setHandRaised(!localHandRaised);
      }
      break;
    case "KeyR":
      if (reactionsBtn && !reactionsBtn.disabled) {
        event.preventDefault();
        setReactionMenu(!reactionMenuOpen);
      }
      break;
    case "KeyP":
      if (presentToggleBtn && !presentToggleBtn.disabled) {
        event.preventDefault();
        presentToggleBtn.click();
      }
      break;
    default:
      break;
  }
}

async function uploadFilesSequentially(fileList) {
  if (!fileList || fileList.length === 0) {
    return;
  }
  if (!joined) {
    flashStatus("Join the session to upload files", "warning", 3000);
    return;
  }
  for (const file of fileList) {
    const data = new FormData();
    data.append("file", file);
    try {
      const res = await fetch("/api/files/upload", { method: "POST", body: data });
      if (!res.ok) {
        let message = `Upload failed for ${file.name}`;
        try {
          const error = await res.json();
          if (error?.detail) {
            message = error.detail;
          }
        } catch (err) {
          console.error("Upload error detail parse failed", err);
        }
        flashStatus(message, "error", 5000);
      } else {
        flashStatus(`Uploaded ${file.name}`, "info", 2500);
      }
    } catch (err) {
      console.error("Upload error", err);
      flashStatus(`Upload error for ${file.name}`, "error", 5000);
    }
  }
  sendControl("file_request_list");
}

function showDragOverlay() {
  if (!dragOverlay) {
    return;
  }
  dragOverlay.classList.remove("hidden");
}

function hideDragOverlay() {
  if (!dragOverlay) {
    return;
  }
  dragOverlay.classList.add("hidden");
  dragDepthCounter = 0;
}

function eventHasFiles(event) {
  return Boolean(event?.dataTransfer && Array.from(event.dataTransfer.types || []).includes("Files"));
}

function handleDragEnter(event) {
  if (!joined || !eventHasFiles(event)) {
    return;
  }
  event.preventDefault();
  dragDepthCounter += 1;
  showDragOverlay();
}

function handleDragOver(event) {
  if (!joined || !eventHasFiles(event)) {
    return;
  }
  event.preventDefault();
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = "copy";
  }
}

function handleDragLeave(event) {
  if (!joined || !eventHasFiles(event)) {
    return;
  }
  event.preventDefault();
  dragDepthCounter = Math.max(0, dragDepthCounter - 1);
  if (dragDepthCounter === 0) {
    hideDragOverlay();
  }
}

function handleDrop(event) {
  if (!eventHasFiles(event)) {
    return;
  }
  event.preventDefault();
  if (!joined) {
    hideDragOverlay();
    flashStatus("Join the session to upload files", "warning", 3000);
    return;
  }
  hideDragOverlay();
  const filesArray = event.dataTransfer ? Array.from(event.dataTransfer.files || []) : [];
  if (filesArray.length === 0) {
    return;
  }
  uploadFilesSequentially(filesArray);
}

let videoWatchdogTimerId = null;

function escapeHtml(text) {
  if (text === null || text === undefined) {
    return "";
  }
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderChatInputOverlay(value) {
  if (!chatInputOverlay) {
    return;
  }

  const placeholderText = chatInput ? chatInput.getAttribute("placeholder") || "" : "";
  const safeValue = typeof value === "string" ? value : "";

  if (!safeValue) {
    if (placeholderText) {
      chatInputOverlay.textContent = placeholderText;
      chatInputOverlay.classList.add("placeholder");
    } else {
      chatInputOverlay.innerHTML = "&nbsp;";
      chatInputOverlay.classList.remove("placeholder");
    }
    return;
  }

  chatInputOverlay.classList.remove("placeholder");
  const mentionRegex = /@([A-Za-z0-9_-]+)/g;
  let lastIndex = 0;
  let result = "";
  let match;

  while ((match = mentionRegex.exec(safeValue)) !== null) {
    result += escapeHtml(safeValue.slice(lastIndex, match.index));
    const mentionValue = escapeHtml(match[1]);
    const mentionText = escapeHtml(match[0]);
    result += `<span class="mention-token" data-mention="${mentionValue}">${mentionText}</span>`;
    lastIndex = mentionRegex.lastIndex;
  }

  result += escapeHtml(safeValue.slice(lastIndex));
  chatInputOverlay.innerHTML = result || "&nbsp;";
}

function flashMentionToken(username) {
  if (!chatInputOverlay || !username) {
    return;
  }
  const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(username) : username.replace(/["\\]/g, "\\$&");
  const tokens = chatInputOverlay.querySelectorAll(`[data-mention="${escaped}"]`);
  tokens.forEach((token) => {
    token.classList.remove("flash");
    // Force reflow so the animation retriggers
    void token.offsetWidth;
    token.classList.add("flash");
  });
}

// ===================== Mention Autocomplete Functions =====================

function handleChatInputChange(event) {
  if (!chatInput || !mentionPopup) {
    return;
  }

  const input = event.target;
  const text = input.value;
  const cursorPos = input.selectionStart ?? text.length;

  renderChatInputOverlay(text);

  // Find the last @ before the cursor
  const textBeforeCursor = text.substring(0, cursorPos);
  const lastAtIndex = textBeforeCursor.lastIndexOf('@');

  if (lastAtIndex === -1) {
    // No @ found, hide popup
    hideMentionPopup();
    return;
  }

  // Ensure @ is at start or preceded by whitespace / punctuation (avoid emails)
  const charBeforeAt = lastAtIndex > 0 ? textBeforeCursor.charAt(lastAtIndex - 1) : '';
  if (charBeforeAt && /[\w@]/.test(charBeforeAt)) {
    hideMentionPopup();
    return;
  }

  // Check if there's a space between @ and cursor (means mention was completed)
  const textAfterAt = textBeforeCursor.substring(lastAtIndex);
  if (textAfterAt.includes(' ') && textAfterAt.length > 1) {
    hideMentionPopup();
    return;
  }

  // Extract the query (text after @)
  const query = textBeforeCursor.substring(lastAtIndex + 1);

  // Show popup with filtered participants
  mentionStartPos = lastAtIndex;
  mentionQuery = query;
  showMentionPopup(query);
}

function handleChatInputKeydown(event) {
  if (!mentionPopup || !mentionPopup.classList.contains('visible')) {
    return;
  }

  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault();
      if (mentionSelectedIndex < mentionMatches.length - 1) {
        mentionSelectedIndex++;
        updateMentionSelection();
      }
      break;

    case 'ArrowUp':
      event.preventDefault();
      if (mentionSelectedIndex > 0) {
        mentionSelectedIndex--;
        updateMentionSelection();
      }
      break;

    case 'Enter':
      event.preventDefault();
      if (mentionSelectedIndex >= 0 && mentionSelectedIndex < mentionMatches.length) {
        insertMention(mentionMatches[mentionSelectedIndex]);
      }
      break;

    case 'Escape':
      event.preventDefault();
      hideMentionPopup();
      break;

    case 'Tab':
      // Allow tab to select if popup is open
      if (mentionSelectedIndex >= 0 && mentionSelectedIndex < mentionMatches.length) {
        event.preventDefault();
        insertMention(mentionMatches[mentionSelectedIndex]);
      }
      break;
  }
}

function showMentionPopup(query) {
  if (!mentionPopup) {
    return;
  }

  const candidateList = Array.from(
    participants instanceof Set
      ? participants
      : Array.isArray(participants)
      ? participants
      : []
  ).filter((name) => typeof name === 'string' && name.trim().length > 0);

  // Exclude current user from suggestions
  const filteredCandidates = candidateList.filter((name) => name !== currentUsername);

  if (filteredCandidates.length === 0) {
    mentionPopup.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'mention-empty';
    empty.textContent = 'No other participants to mention yet';
    mentionPopup.appendChild(empty);
    mentionPopup.classList.remove('hidden');
    mentionPopup.classList.add('visible');
    mentionMatches = [];
    mentionSelectedIndex = -1;
    return;
  }

  const lowerQuery = (query || '').toLowerCase();
  const sortedCandidates = filteredCandidates
    .slice()
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

  // Filter participants by query (case-insensitive)
  mentionMatches = sortedCandidates.filter((name) =>
    name.toLowerCase().startsWith(lowerQuery)
  );

  if (mentionMatches.length === 0) {
    mentionPopup.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'mention-empty';
    empty.textContent = query ? `No matches for "@${query}"` : 'No participants match this search';
    mentionPopup.appendChild(empty);
    mentionPopup.classList.remove('hidden');
    mentionPopup.classList.add('visible');
    mentionMatches = [];
    mentionSelectedIndex = -1;
    return;
  }

  // Reset selection to first item
  mentionSelectedIndex = 0;

  // Clear and populate popup
  mentionPopup.innerHTML = '';

  mentionMatches.forEach((username, index) => {
    const item = document.createElement('div');
    item.className = 'mention-item';
    if (index === mentionSelectedIndex) {
      item.classList.add('selected');
    }

    // Create avatar with gradient background
    const avatar = document.createElement('div');
    avatar.className = 'mention-avatar';
    avatar.textContent = username.charAt(0).toUpperCase();

    // Create username text
    const usernameEl = document.createElement('span');
    usernameEl.className = 'mention-username';
    usernameEl.textContent = username;

    item.appendChild(avatar);
    item.appendChild(usernameEl);

    // Click handler
    item.addEventListener('click', () => {
      insertMention(username);
    });

    mentionPopup.appendChild(item);
  });

  mentionPopup.classList.remove('hidden');
  mentionPopup.classList.add('visible');
}

function hideMentionPopup() {
  if (!mentionPopup) {
    return;
  }
  mentionPopup.classList.add('hidden');
  mentionPopup.classList.remove('visible');
  mentionPopup.innerHTML = '';
  mentionStartPos = -1;
  mentionQuery = '';
  mentionSelectedIndex = -1;
  mentionMatches = [];
}

function updateMentionSelection() {
  if (!mentionPopup) {
    return;
  }
  const items = mentionPopup.querySelectorAll('.mention-item');
  items.forEach((item, index) => {
    if (index === mentionSelectedIndex) {
      item.classList.add('selected');
      // Scroll into view if needed
      item.scrollIntoView({ block: 'nearest' });
    } else {
      item.classList.remove('selected');
    }
  });
}

function insertMention(username) {
  if (!chatInput) {
    return;
  }

  const currentValue = chatInput.value;
  const beforeMention = currentValue.substring(0, Math.max(0, mentionStartPos));
  const afterMention = currentValue.substring(chatInput.selectionStart ?? currentValue.length);
  const existingMentions = parseMentions(currentValue);

  if (existingMentions.includes(username)) {
    const dedupedValue = beforeMention + afterMention;
    chatInput.value = dedupedValue;
    renderChatInputOverlay(chatInput.value);
    flashMentionToken(username);
    hideMentionPopup();
    const fallbackPos = beforeMention.length;
    chatInput.setSelectionRange(fallbackPos, fallbackPos);
    chatInput.focus();
    return;
  }

  // Insert @username with a space after
  const mentionText = `@${username} `;
  const newValue = beforeMention + mentionText + afterMention;
  chatInput.value = newValue;

  // Set cursor position after the inserted mention
  const newCursorPos = beforeMention.length + mentionText.length;
  chatInput.setSelectionRange(newCursorPos, newCursorPos);

  // Hide popup and reset state
  hideMentionPopup();
  renderChatInputOverlay(chatInput.value);
  flashMentionToken(username);

  // Focus back on input
  chatInput.focus();
}

function parseMentions(text) {
  // Extract all @username patterns from text
  // Matches @ followed by allowed characters (letters, numbers, underscore, hyphen)
  const mentionRegex = /@([A-Za-z0-9_-]+)/g;
  const mentions = [];
  let match;

  const source = typeof text === "string" ? text : "";

  const participantSet =
    participants instanceof Set
      ? participants
      : new Set(Array.isArray(participants) ? participants : []);

  while ((match = mentionRegex.exec(source)) !== null) {
    const username = match[1];
    if (participantSet.has(username) && !mentions.includes(username)) {
      mentions.push(username);
    }
  }

  return mentions;
}

// ===================== End Mention Autocomplete Functions =====================

function init() {
  setConnectedUi(false);
  setJoinFormEnabled(false);
  fetchConfig();
  connectSocket();
  ensureVideoPlaceholder();
  updateStagePresenterState(false);
  showScreenMessage("No presenter");
  startVideoWatchdog();
  startScreenWatchdog();
  updateLatencyBadge(null, null);
  
  setupNotificationBadges();
  window.addEventListener("focus", clearNotificationsForActiveTab);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      clearNotificationsForActiveTab();
    }
  });

  // Setup sidebar toggle
  toggleParticipantsBtn.addEventListener("click", toggleParticipantsSidebar);
  toggleChatBtn.addEventListener("click", toggleChatSidebar);
  closeParticipantsBtn.addEventListener("click", () => hideSidebar(participantsSidebar));
  closeChatBtn.addEventListener("click", () => hideSidebar(chatSidebar));
  
  // Setup tab switching
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  if (reactionsBtn) {
    reactionsBtn.addEventListener("click", () => {
      if (reactionsBtn.disabled) {
        return;
      }
      setReactionMenu(!reactionMenuOpen);
    });
  }
  reactionOptions.forEach((option) => {
    option.addEventListener("click", (event) => {
      event.preventDefault();
      handleReactionSelection(option.dataset.reaction);
    });
  });

  document.addEventListener("click", (event) => {
    if (!reactionMenuOpen) {
      return;
    }
    if (reactionsBtn?.contains(event.target) || reactionPicker?.contains(event.target)) {
      return;
    }
    closeReactionMenu();
  });

  document.addEventListener("keydown", handleGlobalKeydown);

  if (chatInput) {
    chatInput.addEventListener("input", handleChatInputChange);
    chatInput.addEventListener("keydown", handleChatInputKeydown);
    chatInput.addEventListener("blur", () => {
      resetTypingState();
      // Delay hiding mention popup to allow click
      setTimeout(() => hideMentionPopup(), 200);
    });
    renderChatInputOverlay(chatInput.value || "");
  }

  document.addEventListener("dragenter", handleDragEnter);
  document.addEventListener("dragover", handleDragOver);
  document.addEventListener("dragleave", handleDragLeave);
  document.addEventListener("drop", handleDrop);
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
    clearNotificationsForActiveTab();
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

  if (tabName === "chat") {
    resetChatNotifications();
  } else if (tabName === "files") {
    resetFileNotifications();
  }
}

function setupNotificationBadges() {
  if (toggleChatBtn && !chatToggleBadgeEl) {
    chatToggleBadgeEl = document.createElement("span");
    chatToggleBadgeEl.className = "notification-badge hidden";
    chatToggleBadgeEl.setAttribute("aria-hidden", "true");
    toggleChatBtn.appendChild(chatToggleBadgeEl);
  }
  if (chatTabBtn && !chatTabBadgeEl) {
    chatTabBadgeEl = document.createElement("span");
    chatTabBadgeEl.className = "notification-badge hidden";
    chatTabBadgeEl.setAttribute("aria-hidden", "true");
    chatTabBtn.appendChild(chatTabBadgeEl);
  }
  if (filesTabBtn && !filesTabBadgeEl) {
    filesTabBadgeEl = document.createElement("span");
    filesTabBadgeEl.className = "notification-badge hidden";
    filesTabBadgeEl.setAttribute("aria-hidden", "true");
    filesTabBtn.appendChild(filesTabBadgeEl);
  }
  updateSidebarNotificationBadge();
  updateChatNotificationBadge();
  updateFilesNotificationBadge();
}

function formatNotificationCount(count) {
  if (!Number.isFinite(count) || count <= 0) {
    return "";
  }
  if (count > 9) {
    return "9+";
  }
  return String(count);
}

function updateSidebarNotificationBadge() {
  if (!chatToggleBadgeEl || !toggleChatBtn) {
    return;
  }
  const combined = chatUnreadCount + filesUnreadCount;
  if (combined > 0) {
    chatToggleBadgeEl.textContent = formatNotificationCount(combined);
    chatToggleBadgeEl.classList.remove("hidden");
    toggleChatBtn.classList.add("has-notification");
  } else {
    chatToggleBadgeEl.textContent = "";
    chatToggleBadgeEl.classList.add("hidden");
    toggleChatBtn.classList.remove("has-notification");
  }
}

function updateChatNotificationBadge() {
  if (!chatTabBadgeEl || !chatTabBtn) {
    return;
  }
  if (chatUnreadCount > 0) {
    chatTabBadgeEl.textContent = formatNotificationCount(chatUnreadCount);
    chatTabBadgeEl.classList.remove("hidden");
    chatTabBtn.classList.add("has-notification");
  } else {
    chatTabBadgeEl.textContent = "";
    chatTabBadgeEl.classList.add("hidden");
    chatTabBtn.classList.remove("has-notification");
  }
  updateSidebarNotificationBadge();
}

function updateFilesNotificationBadge() {
  if (!filesTabBadgeEl || !filesTabBtn) {
    return;
  }
  if (filesUnreadCount > 0) {
    filesTabBadgeEl.textContent = formatNotificationCount(filesUnreadCount);
    filesTabBadgeEl.classList.remove("hidden");
    filesTabBtn.classList.add("has-notification");
  } else {
    filesTabBadgeEl.textContent = "";
    filesTabBadgeEl.classList.add("hidden");
    filesTabBtn.classList.remove("has-notification");
  }
  updateSidebarNotificationBadge();
}

function resetChatNotifications() {
  chatUnreadCount = 0;
  updateChatNotificationBadge();
}

function resetFileNotifications() {
  filesUnreadCount = 0;
  updateFilesNotificationBadge();
}

function clearNotificationsForActiveTab() {
  if (!chatSidebar || chatSidebar.classList.contains("hidden")) {
    return;
  }
  if (chatTabContent && chatTabContent.classList.contains("active")) {
    resetChatNotifications();
  } else if (filesTabContent && filesTabContent.classList.contains("active")) {
    resetFileNotifications();
  }
}

function windowHasFocus() {
  return document.hasFocus() && !document.hidden;
}

function isChatSidebarVisible() {
  return Boolean(chatSidebar && !chatSidebar.classList.contains("hidden"));
}

function isChatTabActive() {
  return Boolean(chatTabContent && chatTabContent.classList.contains("active"));
}

function isFilesTabActive() {
  return Boolean(filesTabContent && filesTabContent.classList.contains("active"));
}

function runWithSuppressedChat(callback) {
  const previous = suppressChatNotifications;
  suppressChatNotifications = true;
  try {
    callback();
  } finally {
    suppressChatNotifications = previous;
  }
}

function runWithSuppressedFile(callback) {
  const previous = suppressFileNotifications;
  suppressFileNotifications = true;
  try {
    callback();
  } finally {
    suppressFileNotifications = previous;
  }
}

function handleChatNotification(sender) {
  if (suppressChatNotifications) {
    return;
  }
  if (typeof sender === "string" && currentUsername && sender === currentUsername) {
    return;
  }
  const inView = isChatSidebarVisible() && isChatTabActive() && windowHasFocus();
  if (inView) {
    return;
  }
  const wasZero = chatUnreadCount === 0;
  chatUnreadCount = Math.min(99, chatUnreadCount + 1);
  updateChatNotificationBadge();
  if (wasZero) {
    const name = typeof sender === "string" && sender.trim() ? sender.trim() : "New chat message";
    const message = sender && sender.trim() ? `New message from ${sender.trim()}` : name;
    flashStatus(message, "info", 3200);
  }
}

function handleFileNotification({ uploader, filename }) {
  if (suppressFileNotifications) {
    return;
  }
  const inView = isChatSidebarVisible() && isFilesTabActive() && windowHasFocus();
  if (!inView) {
    const wasZero = filesUnreadCount === 0;
    filesUnreadCount = Math.min(99, filesUnreadCount + 1);
    updateFilesNotificationBadge();
    if (wasZero) {
      const safeName = typeof filename === "string" && filename.trim() ? filename.trim() : "a file";
      const displayName = safeName.length > 32 ? `${safeName.slice(0, 29)}…` : safeName;
      if (uploader && uploader !== currentUsername) {
        flashStatus(`${uploader} shared ${displayName}`, "info", 3500);
      } else {
        flashStatus(`New shared file: ${displayName}`, "info", 3500);
      }
    }
  }
}

function setPersistentStatus(text, severity) {
  persistentStatus = {
    text: typeof text === "string" ? text : "",
    severity: severity === "error" || severity === "warning" ? severity : "info",
  };
}

function applyPersistentStatus() {
  if (!statusEl) {
    return;
  }
  statusEl.textContent = persistentStatus.text;
  statusEl.classList.toggle("error", persistentStatus.severity === "error");
  statusEl.classList.toggle("warning", persistentStatus.severity === "warning");
  if (persistentStatus.severity !== "error" && persistentStatus.severity !== "warning") {
    statusEl.classList.remove("error", "warning");
  }
}

function flashStatus(message, severity = "info", duration = 4000) {
  if (!message) return;
  if (statusRestoreTimerId) {
    clearTimeout(statusRestoreTimerId);
    statusRestoreTimerId = null;
  }
  statusEl.textContent = message;
  statusEl.classList.toggle("error", severity === "error");
  statusEl.classList.toggle("warning", severity === "warning");
  if (severity !== "error" && severity !== "warning") {
    statusEl.classList.remove("error", "warning");
  }
  if (duration > 0) {
    statusRestoreTimerId = window.setTimeout(() => {
      statusRestoreTimerId = null;
      applyPersistentStatus();
    }, duration);
  } else {
    setPersistentStatus(message, severity);
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
  if (username === currentUsername && state && typeof state === "object") {
    let changed = false;
    if (Object.prototype.hasOwnProperty.call(state, "audio_enabled")) {
      const nextAudio = Boolean(state.audio_enabled);
      if (micEnabled !== nextAudio) {
        micEnabled = nextAudio;
        changed = true;
      }
    }
    if (Object.prototype.hasOwnProperty.call(state, "video_enabled")) {
      const nextVideo = Boolean(state.video_enabled);
      if (videoEnabled !== nextVideo) {
        videoEnabled = nextVideo;
        changed = true;
      }
    }
    if (changed) {
      patchLocalPresence({
        audio_enabled: next.audio_enabled,
        video_enabled: next.video_enabled,
      });
    }
  }
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
      setJoinFormEnabled(true);
    }
  } catch (error) {
    console.error("Config fetch error", error);
  }
}

async function requestRandomName() {
  if (wasKicked) {
    return;
  }
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
    if (!joined && !wasKicked) {
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
  if (wasKicked) {
    return false;
  }
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
  if (wasKicked && type !== "session_status" && type !== "kicked") {
    return;
  }
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
    case "audio_status":
      handleAudioStatus(payload);
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
      handleFileUploadComplete(payload);
      break;
    case "file_download_ready":
      handleFileDownloadReady(payload);
      break;
    case "file_share_link":
      handleFileShareLink(payload);
      break;
    case "kicked":
      enterKickedState(payload || {});
      break;
    case "presence_sync":
      applyPresenceSnapshot(payload.participants || []);
      break;
    case "presence_update":
      {
        const normalized = normalizePresenceEntry(payload, { partial: true });
        if (normalized) {
          updatePresenceEntry(normalized);
        }
      }
      break;
    case "typing_status":
      if (payload && typeof payload.username === "string") {
        updatePresenceEntry({ username: payload.username, is_typing: Boolean(payload.is_typing) });
      }
      break;
    case "hand_status":
      if (payload && typeof payload.username === "string") {
        updatePresenceEntry({ username: payload.username, hand_raised: Boolean(payload.hand_raised) });
      }
      break;
    case "reaction":
      spawnReaction(payload);
      break;
    case "latency_update":
      if (payload && typeof payload.username === "string") {
        updatePresenceEntry({
          username: payload.username,
          latency_ms: payload.latency_ms,
          jitter_ms: payload.jitter_ms,
        });
      }
      if (payload && payload.username === currentUsername) {
        updateLatencyBadge(payload.latency_ms, payload.jitter_ms);
      }
      break;
    case "latency_metrics":
      updateLatencyBadge(payload.latency_ms, payload.jitter_ms);
      break;
    case "time_limit_update":
      applyMeetingTimer(payload);
      break;
    case "admin_notice":
      showAdminBanner(payload);
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
      wasKicked = false;
      kickedReason = "";
      break;
    case "connecting":
      setJoinStatus(`Connecting as ${username}...`, false);
      break;
    case "reconnecting":
      joined = false;
      setConnectedUi(false);
      resetTypingState();
      if (typeof username === "string") {
        setJoinStatus(`Reconnecting as ${username}...`, false);
      }
      flashStatus("Connection lost. Attempting to reconnect…", "warning", 4000);
      updateStatusLine("Reconnecting...");
      break;
    case "disconnecting":
      joined = false;
      setConnectedUi(false);
      leaveButton.disabled = true;
      setJoinFormEnabled(false);
      resetLeaveFlow();
      if (joinOverlay) {
        joinOverlay.classList.remove("hidden");
      }
      if (joinSection) {
        joinSection.classList.remove("hidden");
      }
      if (leaveSection) {
        leaveSection.classList.add("hidden");
      }
      setJoinStatus(message || "Disconnecting...", false);
      updateStatusLine(message || "Disconnecting...");
      resetChatNotifications();
      resetFileNotifications();
      break;
    case "error":
      joined = false;
      setJoinStatus(message || "Connection error", true);
      if (!wasKicked) {
        setJoinFormEnabled(true);
      }
      resetChatNotifications();
      resetFileNotifications();
      break;
    case "kicked":
      enterKickedState({ reason: message });
      break;
    case "idle":
      if (wasKicked) {
        enterKickedState({ reason: kickedReason || message });
        break;
      }
      joined = false;
      setJoinStatus("Idle", false);
      setJoinFormEnabled(true);
      clearMeetingTimer();
      hideAdminBanner();
      resetChatNotifications();
      resetFileNotifications();
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
    if (wasKicked) {
      statusEl.textContent = kickedReason || fallback || "Removed by administrator";
      statusEl.classList.add("error");
      statusEl.classList.remove("warning");
      setPersistentStatus(statusEl.textContent, "error");
      return;
    }
    statusEl.textContent = fallback || "Offline";
    statusEl.classList.add("error");
    statusEl.classList.remove("warning");
    setPersistentStatus(statusEl.textContent, "error");
    return;
  }
  const pieces = [`Connected as ${currentUsername}`];
  if (currentPresenter) {
    pieces.push(`Presenter: ${currentPresenter}`);
  }
  statusEl.textContent = pieces.join(" • ");
  statusEl.classList.remove("error");
  statusEl.classList.remove("warning");
  setPersistentStatus(statusEl.textContent, "info");
}

function setConnectedUi(enabled) {
  chatInput.disabled = !enabled;
  uploadButton.disabled = !enabled;
  fileInput.disabled = !enabled;
  micToggleBtn.disabled = !enabled;
  videoToggleBtn.disabled = !enabled;
  presentToggleBtn.disabled = !enabled;
  if (handToggleBtn) {
    handToggleBtn.disabled = !enabled;
  }
  if (reactionsBtn) {
    reactionsBtn.disabled = !enabled;
  }
  leaveButton.disabled = !enabled;
  toggleParticipantsBtn.disabled = !enabled;
  toggleChatBtn.disabled = !enabled;
  if (!enabled) {
    closeReactionMenu();
  }
}

function setJoinFormEnabled(enabled) {
  const allow = Boolean(enabled && !wasKicked);
  if (nameInput) {
    nameInput.disabled = !allow;
  }
  if (joinButton) {
    joinButton.disabled = !allow;
  }
  if (randomNameBtn) {
    randomNameBtn.disabled = !allow;
  }
}

function enterKickedState(payload = {}) {
  if (wasKicked) {
    return;
  }
  wasKicked = true;
  clearMeetingTimer();
  hideAdminBanner();
  const reason = typeof payload.reason === "string" ? payload.reason : typeof payload.message === "string" ? payload.message : "An administrator removed you from this meeting.";
  kickedReason = reason;
  setConnectedUi(false);
  setJoinFormEnabled(false);
  joined = false;
  currentPresenter = null;
  micEnabled = false;
  videoEnabled = false;
  resetTypingState();
  updateControlButtons();
  participants = new Set();
  renderParticipants();
  files = new Map();
  renderFiles();
  chatMessagesEl.innerHTML = "";
  videoElements.clear();
  videoGridEl.innerHTML = "";
  ensureVideoPlaceholder();
  peerMedia.clear();
  screenLastFrameAt = null;
  showScreenMessage("Removed by admin");
  updateStagePresenterState(false);
  updatePresenterHighlight();
  hideSidebar(participantsSidebar);
  hideSidebar(chatSidebar);
  toggleParticipantsBtn.classList.remove("active");
  toggleChatBtn.classList.remove("active");
  resetLeaveFlow();
  resetChatNotifications();
  resetFileNotifications();
  flashStatus(reason, "error", 0);
  updateStatusLine("Removed by administrator");
  if (joinStatusEl) {
    joinStatusEl.textContent = reason;
    joinStatusEl.classList.add("error");
  }
  if (joinOverlay) {
    joinOverlay.classList.remove("hidden");
  }
  if (joinSection) {
    joinSection.classList.add("hidden");
  }
  if (leaveSection) {
    leaveSection.classList.add("hidden");
  }
  if (kickedNotice) {
    kickedNotice.classList.remove("hidden");
  }
  if (kickedMessageEl) {
    kickedMessageEl.textContent = reason;
  }
}

function initState(payload) {
  if (wasKicked) {
    enterKickedState({ reason: kickedReason });
    return;
  }
  joined = true;
  currentUsername = payload.username;
  micEnabled = Boolean(payload.media?.audio_enabled || payload.media_state?.[currentUsername]?.audio_enabled);
  videoEnabled = Boolean(payload.media?.video_enabled || payload.media_state?.[currentUsername]?.video_enabled);
  localHandRaised = Boolean(payload.hand_raised);
  updateStatusLine();
  participants = new Set(payload.peers || []);
  ensureSelfInParticipants();
  peerMedia.clear();
  setPresenterState(payload.presenter || null);
  applyPresenceSnapshot(payload.presence || []);
  applyMeetingTimer(payload.time_limit || null);
  if (Array.isArray(payload.admin_notices) && payload.admin_notices.length > 0) {
    const latestNotice = payload.admin_notices[payload.admin_notices.length - 1];
    showAdminBanner(latestNotice);
  }
  (payload.peers || []).forEach((name) => {
    if (typeof name === "string" && name.trim()) {
      participants.add(name.trim());
    }
  });
  refreshPresenceUi();
  runWithSuppressedChat(() => {
    chatMessagesEl.innerHTML = "";
    (payload.chat_history || []).forEach((msg) => appendChatMessage(msg));
  });
  resetChatNotifications();
  runWithSuppressedFile(() => {
    files = new Map();
    (payload.files || []).forEach((file) => {
      files.set(file.file_id, file);
    });
  });
  renderFiles();
  resetFileNotifications();
  sendControl("file_request_list");
  videoElements.clear();
  videoGridEl.innerHTML = "";
  ensureVideoPlaceholder();
  participants.forEach((name) => ensureVideoTile(name));
  ensureVideoTile(currentUsername);
  applyMediaSnapshot(payload.media_state || payload.peer_media);
  const latency = payload.latency || null;
  if (latency) {
    updateLatencyBadge(latency.latency_ms, latency.jitter_ms);
  } else {
    updateLatencyBadge(null, null);
  }
  replayRecentReactions(payload.reactions || []);
  resetLeaveFlow();
  resetTypingState();
  setConnectedUi(true);
  leaveButton.disabled = false;
  joinOverlay.classList.add("hidden");
  updateControlButtons();
  updateParticipantSummary();
}

function handleStateSnapshot(snapshot) {
  if (wasKicked) {
    enterKickedState({ reason: kickedReason });
    return;
  }
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
  applyPresenceSnapshot(snapshot.presence || []);
  (snapshot.peers || []).forEach((name) => {
    if (typeof name === "string" && name.trim()) {
      participants.add(name.trim());
    }
  });
  applyMeetingTimer(snapshot.time_limit || null);
  if (Array.isArray(snapshot.admin_notices) && snapshot.admin_notices.length > 0) {
    const latestNotice = snapshot.admin_notices[snapshot.admin_notices.length - 1];
    showAdminBanner(latestNotice);
  }
  refreshPresenceUi();
  runWithSuppressedChat(() => {
    chatMessagesEl.innerHTML = "";
    (snapshot.chat_history || []).forEach((msg) => appendChatMessage(msg));
  });
  resetChatNotifications();
  runWithSuppressedFile(() => {
    files = new Map();
    (snapshot.files || []).forEach((file) => {
      files.set(file.file_id, file);
    });
  });
  renderFiles();
  resetFileNotifications();
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
  const latency = snapshot.latency || lastLatencyMetrics || null;
  if (latency) {
    updateLatencyBadge(latency.latency_ms, latency.jitter_ms);
  } else {
    updateLatencyBadge(null, null);
  }
  replayRecentReactions(snapshot.reactions || []);
  localHandRaised = Boolean(snapshot.hand_raised);
  resetTypingState();
  updateControlButtons();
  updateParticipantSummary();
}

function appendChatMessage({ sender, message, timestamp_ms, recipients }) {
  const item = document.createElement("div");
  item.className = "chat-message";
  item.style.animation = "slideInMessage 0.3s ease";
  
  // Check if current user is mentioned
  const mentions = parseMentions(message);
  const currentUserMentioned = mentions.includes(currentUsername);
  if (currentUserMentioned) {
    item.classList.add('has-mention');
  }
  
  const meta = document.createElement("div");
  meta.className = "meta";
  
  const senderSpan = document.createElement("span");
  senderSpan.className = "sender";
  senderSpan.textContent = sender;
  
  const timeSpan = document.createElement("span");
  timeSpan.className = "time";
  const date = timestamp_ms ? new Date(timestamp_ms) : new Date();
  timeSpan.textContent = date.toLocaleTimeString();
  
  meta.appendChild(senderSpan);
  meta.appendChild(document.createTextNode(" • "));
  meta.appendChild(timeSpan);
  
  // Add mention badge if current user is mentioned
  if (currentUserMentioned) {
    const mentionBadge = document.createElement("span");
    mentionBadge.className = "mention-badge";
    mentionBadge.textContent = "You were mentioned";
    meta.appendChild(mentionBadge);
  }
  
  if (Array.isArray(recipients) && recipients.length > 0) {
    const badge = document.createElement("span");
    badge.className = "recipients-badge";
    const displayList = recipients.join(", ");
    badge.textContent = `to ${displayList}`;
    meta.appendChild(badge);
  }
  
  const body = document.createElement("div");
  body.className = "body";
  
  // Render message with highlighted @mentions
  const mentionRegex = /@([A-Za-z0-9_-]+)/g;
  let lastIndex = 0;
  let match;
  
  while ((match = mentionRegex.exec(message)) !== null) {
    const username = match[1];
    const matchStart = match.index;
    const matchEnd = mentionRegex.lastIndex;
    
    // Add text before the mention
    if (matchStart > lastIndex) {
      body.appendChild(document.createTextNode(message.substring(lastIndex, matchStart)));
    }
    
    // Add the mention as a styled span
    const mentionSpan = document.createElement('span');
    mentionSpan.className = 'mention';
    mentionSpan.textContent = match[0];
    body.appendChild(mentionSpan);
    
    lastIndex = matchEnd;
  }
  
  // Add remaining text after last mention
  if (lastIndex < message.length) {
    body.appendChild(document.createTextNode(message.substring(lastIndex)));
  }
  
  item.append(meta, body);
  chatMessagesEl.appendChild(item);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  
  // Only notify if current user is mentioned (not just a recipient)
  if (currentUserMentioned && sender !== currentUsername) {
    handleChatNotification(sender);
  }
}

function sortParticipantEntries(entries) {
  return entries.sort((a, b) => {
    const aIsSelf = a.username === currentUsername;
    const bIsSelf = b.username === currentUsername;
    if (aIsSelf && !bIsSelf) return -1;
    if (bIsSelf && !aIsSelf) return 1;
    if (a.is_presenter && !b.is_presenter) return -1;
    if (b.is_presenter && !a.is_presenter) return 1;
    if (a.hand_raised && !b.hand_raised) return -1;
    if (b.hand_raised && !a.hand_raised) return 1;
    return a.username.localeCompare(b.username, undefined, { sensitivity: "base" });
  });
}

function renderParticipants() {
  if (!participantListEl) {
    return;
  }
  participantListEl.innerHTML = "";
  ensureSelfInParticipants();
  const entries = presenceMap.size
    ? Array.from(presenceMap.values()).map((entry) => ({
        ...entry,
        is_presenter: entry.is_presenter || entry.username === currentPresenter,
      }))
    : Array.from(participants).map((username) => {
        const mediaState = peerMedia.get(username) || {};
        return {
          username,
          audio_enabled: Boolean(mediaState.audio_enabled),
          video_enabled: Boolean(mediaState.video_enabled),
          hand_raised: false,
          is_presenter: username === currentPresenter,
          is_typing: false,
          latency_ms: null,
          jitter_ms: null,
        };
      });
  sortParticipantEntries(entries).forEach((entry) => {
    const li = document.createElement("li");
    li.className = "participant-row";
    if (entry.username === currentUsername) {
      li.classList.add("self");
    }
    if (entry.hand_raised) {
      li.classList.add("hand-raised");
    }

    const avatar = createParticipantAvatar(entry.username);
    const info = document.createElement("div");
    info.className = "participant-info";

    const nameRow = document.createElement("div");
    nameRow.className = "participant-name";
    const nameSpan = document.createElement("span");
    nameSpan.textContent = entry.username === currentUsername ? `${entry.username} (You)` : entry.username;
    nameRow.appendChild(nameSpan);

    if (entry.is_presenter) {
      const badge = document.createElement("span");
      badge.className = "participant-badge";
      badge.textContent = "Presenter";
      nameRow.appendChild(badge);
    }
    if (entry.hand_raised) {
      const badge = document.createElement("span");
      badge.className = "participant-badge";
      badge.textContent = "Hand Raised";
      nameRow.appendChild(badge);
    }

    info.appendChild(nameRow);

    const metaRow = document.createElement("div");
    metaRow.className = "participant-meta";

    const micStatus = document.createElement("span");
    micStatus.textContent = entry.audio_enabled ? "🎤 On" : "🎤 Muted";
    metaRow.appendChild(micStatus);

    const cameraStatus = document.createElement("span");
    cameraStatus.textContent = entry.video_enabled ? "🎥 On" : "🎥 Off";
    metaRow.appendChild(cameraStatus);

    if (entry.is_typing) {
      const typingSpan = document.createElement("span");
      typingSpan.textContent = "⌨️ Typing";
      metaRow.appendChild(typingSpan);
    }

    if (entry.latency_ms !== null && entry.latency_ms !== undefined) {
      const latencySpan = document.createElement("span");
      latencySpan.className = "participant-latency";
      latencySpan.textContent = `Latency ${Math.round(entry.latency_ms)} ms`;
      metaRow.appendChild(latencySpan);
    }

    if (metaRow.childElementCount > 0) {
      info.appendChild(metaRow);
    }

    li.append(avatar, info);
    participantListEl.appendChild(li);
  });
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
    screenImageEl.src = "";
    screenImageEl.style.display = "none";
    screenImageEl.removeAttribute("alt");
  }
  screenWrapperEl?.classList.remove("has-content");
}

function handleFileOffer(payload) {
  if (payload.files) {
    files = new Map(payload.files.map((f) => [f.file_id, f]));
    renderFiles();
    return;
  }
  if (payload.file_id) {
    files.set(payload.file_id, payload);
    renderFiles();
    const uploader = typeof payload.uploader === "string" ? payload.uploader : null;
    const filename = typeof payload.filename === "string" ? payload.filename : null;
    handleFileNotification({ uploader, filename });
  }
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

function handleFileUploadComplete(payload) {
  const filename = typeof payload?.filename === "string" ? payload.filename : "File";
  flashStatus(`${filename} uploaded`, "info", 2500);
  sendControl("file_request_list");
}

function handleFileDownloadReady(payload) {
  const fileId = payload?.file_id;
  const url = typeof payload?.url === "string" ? payload.url : null;
  if (!fileId || !url || !pendingDownloads.has(fileId)) {
    return;
  }
  pendingDownloads.delete(fileId);
  try {
    window.open(url, "_blank", "noopener");
  } catch (err) {
    console.warn("Window open blocked, falling back to navigation", err);
    window.location.href = url;
  }
}

function handleFileShareLink(payload) {
  const fileId = payload?.file_id;
  const url = typeof payload?.url === "string" ? payload.url : null;
  if (!fileId || !pendingShareLinks.has(fileId) || !url) {
    return;
  }
  pendingShareLinks.delete(fileId);
  copyTextToClipboard(url).finally(() => {
    flashStatus("Share link copied to clipboard", "info", 2500);
  });
}

function renderFiles() {
  if (!fileListEl) {
    return;
  }
  fileListEl.innerHTML = "";
  Array.from(files.entries()).forEach(([id, file]) => {
    const li = document.createElement("li");

    const fileInfo = document.createElement("div");
    fileInfo.className = "file-info";

    const fileNameContainer = document.createElement("div");
    fileNameContainer.style.flex = "1";

    const nameSpan = document.createElement("div");
    nameSpan.className = "file-name";
    nameSpan.textContent = file.filename || id;
    fileNameContainer.appendChild(nameSpan);

    const fileMeta = document.createElement("div");
    fileMeta.className = "file-meta";

    const sizeSpan = document.createElement("div");
    sizeSpan.className = "file-size";
    const totalSize = file.total_size || file.size;
    const sizeText = typeof totalSize === "number" ? formatBytes(totalSize) : "Unknown";
    let detailText = sizeText;
    if (file.received && file.total_size) {
      const progress = Math.min(100, Math.max(0, Math.round((file.received / file.total_size) * 100)));
      detailText = `${sizeText} • ${progress}%`;
    }
    sizeSpan.textContent = detailText;
    fileMeta.appendChild(sizeSpan);

    if (file.uploader) {
      const uploaderSpan = document.createElement("div");
      uploaderSpan.className = "file-uploader";
      uploaderSpan.textContent = `Shared by ${file.uploader}`;
      fileMeta.appendChild(uploaderSpan);
    }

    fileNameContainer.appendChild(fileMeta);
    fileInfo.appendChild(fileNameContainer);

    const actions = document.createElement("div");
    actions.className = "file-actions";

    const downloadBtn = document.createElement("button");
    downloadBtn.type = "button";
    downloadBtn.textContent = "Download";
    downloadBtn.title = "Download file";
    downloadBtn.addEventListener("click", () => requestFileDownload(id));
    actions.appendChild(downloadBtn);

    const shareBtn = document.createElement("button");
    shareBtn.type = "button";
    shareBtn.className = "secondary";
    shareBtn.textContent = "Copy Link";
    shareBtn.title = "Copy share link";
    shareBtn.addEventListener("click", () => requestShareLink(id));
    actions.appendChild(shareBtn);

    li.append(fileInfo, actions);
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

function requestFileDownload(fileId) {
  if (!fileId) {
    return;
  }
  if (!joined) {
    window.location.href = `/api/files/download/${fileId}`;
    return;
  }
  pendingDownloads.add(fileId);
  const accepted = sendControl("file_download", { file_id: fileId });
  if (!accepted) {
    pendingDownloads.delete(fileId);
    window.location.href = `/api/files/download/${fileId}`;
  }
}

function requestShareLink(fileId) {
  if (!fileId) {
    return;
  }
  if (!joined) {
    const fallbackUrl = `${window.location.origin}/api/files/download/${fileId}`;
    copyTextToClipboard(fallbackUrl).finally(() => {
      flashStatus("Share link copied to clipboard", "info", 2500);
    });
    return;
  }
  pendingShareLinks.add(fileId);
  const accepted = sendControl("copy_file_link", { file_id: fileId });
  if (!accepted) {
    pendingShareLinks.delete(fileId);
    const fallbackUrl = `${window.location.origin}/api/files/download/${fileId}`;
    copyTextToClipboard(fallbackUrl).finally(() => {
      flashStatus("Share link copied to clipboard", "info", 2500);
    });
  }
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
  if (handToggleBtn) {
    handToggleBtn.classList.toggle("active", Boolean(localHandRaised));
    handToggleBtn.title = localHandRaised ? "Lower hand" : "Raise hand";
  }
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
  resetTypingState();
  setConnectedUi(false);
  const accepted = sendControl("leave_session", { force: true, auto: autoTriggered });
  setJoinStatus(accepted ? "Disconnecting..." : "Unable to signal disconnect", !accepted);
  updateControlButtons();
  updateStatusLine("Disconnecting...");
  resetChatNotifications();
  resetFileNotifications();
}

function ensureSelfInParticipants() {
  if (currentUsername) {
    participants.add(currentUsername);
  }
}

function updateParticipantSummary() {
  ensureSelfInParticipants();
  const count = presenceMap.size > 0 ? presenceMap.size : participants.size;
  if (participantCountDisplay) {
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
  if (presenceMap.size === 0) {
    renderParticipants();
  } else {
    const names = new Set(filtered);
    presenceMap.forEach((_, username) => {
      if (!names.has(username) && username !== currentUsername) {
        presenceMap.delete(username);
      }
    });
    refreshPresenceUi();
  }
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
  updateParticipantSummary();
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

function handleAudioStatus(payload) {
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
  
  // Extract mentions from the message
  const mentions = parseMentions(message);
  
  // If there are mentions, send to those users; otherwise send to all
  const payload = mentions.length > 0 ? { message, recipients: mentions } : { message };
  
  sendControl("chat_send", payload);
  chatInput.value = "";
  renderChatInputOverlay("");
  
  // Hide mention popup if still visible
  hideMentionPopup();
  
  resetTypingState();
});

if (uploadButton) {
  uploadButton.addEventListener("click", async () => {
    if (!fileInput.files?.length) return;
    await uploadFilesSequentially(Array.from(fileInput.files));
    fileInput.value = "";
  });
}

if (handToggleBtn) {
  handToggleBtn.addEventListener("click", () => {
    if (handToggleBtn.disabled) {
      return;
    }
    setHandRaised(!localHandRaised);
  });
}

joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (wasKicked) {
    setJoinStatus(kickedReason || "You were removed by an administrator.", true);
    return;
  }
  if (!socketReady) {
    setJoinStatus("Socket not ready, please try again", true);
    return;
  }
  const desiredName = nameInput.value.trim();
  if (!desiredName) {
    setJoinStatus("Please enter a name", true);
    return;
  }
  setJoinFormEnabled(false);
  setJoinStatus(`Connecting as ${desiredName}...`, false);
  const accepted = sendControl("join", { username: desiredName });
  if (!accepted) {
    setJoinStatus("Failed to send join request", true);
  }
});

randomNameBtn.addEventListener("click", () => {
  if (wasKicked) {
    return;
  }
  requestRandomName();
});

micToggleBtn.addEventListener("click", () => {
  if (micToggleBtn.disabled) return;
  micEnabled = !micEnabled;
  updateControlButtons();
  if (currentUsername) {
    updatePeerMedia(currentUsername, { audio_enabled: micEnabled });
    patchLocalPresence({ audio_enabled: micEnabled });
  }
  sendControl("toggle_audio", { enabled: micEnabled });
});

videoToggleBtn.addEventListener("click", () => {
  if (videoToggleBtn.disabled) return;
  videoEnabled = !videoEnabled;
  updateControlButtons();
  if (currentUsername) {
    applyVideoEnabledState(currentUsername, videoEnabled);
    updatePeerMedia(currentUsername, { video_enabled: videoEnabled });
    patchLocalPresence({ video_enabled: videoEnabled });
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
