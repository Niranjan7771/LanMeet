# LAN Collaboration Suite Architecture

This document provides an in-depth look at the LAN Collaboration Suite. It explains how the platform is assembled, how traffic flows between components, and which operational guarantees are offered when the system is deployed on a local network. The goal is to equip operators and contributors with a mental model that covers both day-to-day usage and the reasoning behind critical design decisions.

## 1. Conceptual Overview

At its core the suite follows a hub-and-spoke design. One process acts as the authoritative server while every participant runs a lightweight client that bridges their browser UI with local audio, video, and screen sharing capabilities. No public cloud is required; every packet remains inside the LAN or VPN selected by the operator.

```
                +-------------------------+
                |         Browser UI       |
                +-------------+-----------+
                              |
                              | HTTP / WebSocket (loopback)
                              v
  +---------------------------+---------------------------+
  |                   Client Runtime                      |
  |  - Control channel over TCP                           |
  |  - UDP media capture / playback                       |
  |  - Local file cache                                   |
  +---------------------------+---------------------------+
                              |
                              | LAN sockets
                              v
                   +----------+-----------+
                   |     Collaboration    |
                   |        Server        |
                   +----------------------+ 
```

The server is responsible for session orchestration, state storage, and media relays. Clients focus on capturing local media, subscribing to server broadcasts, and reflecting state transitions in their embedded browser UI. Because every client bundles its own HTTP server, the user experience remains consistent across desktop environments.

## 2. End-to-End Flow

1. **Startup**: The operator launches the server process specifying control and admin ports. Each client is started with the server address and optional UI port.
2. **Handshake**: A client opens the TCP control socket, announces its identity, and retrieves chat history, presence information, media configuration, and any active time limit.
3. **Media Joining**: When a participant toggles microphone, camera, or screen sharing, the client negotiates UDP or TCP streams and the server propagates status updates to other attendees.
4. **Administration**: The admin dashboard communicates with the server over HTTP to retrieve snapshots, kick participants, adjust meeting limits, and initiate shutdown.
5. **Teardown**: During shutdown the server disconnects participants, stops every media service, clears `server_storage/`, and finally terminates the admin API.

## 3. Communication Channels

Each capability is delivered through a dedicated transport. Channels are intentionally isolated to keep media pipelines predictable and to make troubleshooting straightforward.

| Feature                | Transport | Direction        | Purpose |
|------------------------|-----------|------------------|---------|
| Audio conferencing     | UDP       | Client <-> Server | Streams PCM frames captured locally; server forwards to subscribers after optional mixing. |
| Video conferencing     | UDP       | Client <-> Server | Sends JPEG frames with timestamps; server relays to viewers and records throughput metrics. |
| Screen or slide sharing| TCP       | Presenter -> Server -> Viewers | Delivers PNG frames with guaranteed ordering so text and cursor updates remain crisp. |
| Group chat             | TCP       | Client <-> Server | Uses length-prefixed JSON messages to keep ordering and delivery guarantees. |
| File sharing           | TCP       | Client <-> Server | Handles chunked uploads, metadata events, and download requests. |
| Control signalling     | TCP       | Client <-> Server | Coordinates user presence, presenter assignment, media toggles, reactions, and administrative notices. |
| Heartbeat telemetry    | TCP       | Client -> Server  | Emits periodic markers so the server can spot stalled control links and trigger reconnect flows. |
| Latency probe service  | UDP       | Client <-> Server | Sends timestamped echo packets; operators can restrict access with firewall rules when required. |
| UI bridge              | WebSocket (loopback) | Browser <-> Client runtime | Connects the embedded Vue/Vanilla JS UI with the Python runtime that executes LAN traffic. |

### Reliability Considerations

- **TCP channels** (control, chat, file transfer, screen sharing) are backpressure-aware and tolerate transient congestion while maintaining ordering.
- **UDP media** (audio, video, latency probes) prioritizes low latency. Packet loss is expected and handled with jitter buffers or lightweight error concealment.
- **Loopback HTTP/WebSocket** ensures the browser UI does not depend on the main server for asset hosting. If the control channel drops the UI can still display reconnect status.

## 4. Core Modules

### 4.1 Session Core (`shared/`, `server/session_manager.py`)
- Maintains the authoritative registry of participants, including presence attributes (audio/video flags, hand raise status, typing indicators, latency metrics).
- Emits broadcasts through helper methods so higher level services can focus on their domain logic.
- Persists event logs (join, leave, kick, notice, time limit changes) and exposes snapshots to the admin API.
- Provides `disconnect_all` and `mark_shutdown_requested` to guarantee a coherent shutdown story.

### 4.2 Control Server (`server/control_server.py`)
- Exposes the TCP control socket and performs the HELLO handshake.
- Rejects duplicate usernames, tracks banned users, and coordinates presenter transitions.
- Bridges chat messages, reactions, and presence deltas to all participants.
- Invokes `force_disconnect` when the admin dashboard removes a user.

### 4.3 Media Services
- **Audio Server**: Manages UDP sockets per participant, mixes active speakers, and publishes mixdowns.
- **Video Server**: Relays video frames; can be extended with transcoding if bandwidth shaping is required later.
- **Screen Server**: Accepts PNG frames from a single presenter at a time, relays them to viewers, and controls access when presenters switch.
- **Latency Server**: Replies to timestamped probes; results feed presence overlays in the UI.

### 4.4 File Server (`server/file_server.py`)
- Accepts chunked uploads and stores them in `server_storage/`.
- Maintains in-memory metadata to drive file offer broadcasts.
- Cleans up storage on demand or during shutdown using the asynchronous purge routine.

### 4.5 Admin Dashboard (`server/admin_dashboard.py`, `adminui/`)
- Provides the HTML/JS single-page app plus JSON endpoints for state snapshots, notices, kicks, time limit adjustments, and shutdown.
- Caches storage stats and log-tail data for quick refreshes.
- Returns explicit status codes when shutdown is already in progress so operators avoid duplicate requests.

## 5. Data Flow Details

### 5.1 Join Sequence
1. Client opens TCP control socket.
2. Server validates identity and bans, registers the client, and sends a `WELCOME` packet containing chat history, file offers, presenter status, and configuration.
3. Client acknowledges and begins periodic heartbeats and latency probes.
4. Admin dashboard snapshot now includes the new participant.

### 5.2 File Upload Life Cycle
1. User drops a file onto the browser UI; the client runtime begins a TCP upload session.
2. Each chunk is acknowledged by the server; progress updates are broadcast to other participants.
3. When complete the server emits a `FILE_OFFER` event; peers can download directly from the file server.
4. On shutdown or manual cleanup the storage purge removes persisted chunks and logs the outcome.

### 5.3 Shutdown Life Cycle
1. Admin clicks **Stop server & clear files**.
2. Server marks shutdown as requested, disconnects participants with a reason payload, cancels background watchers, and stops every media service in order.
3. `file_server.cleanup_storage()` purges residual artifacts.
4. Admin API reports `in_progress` if additional shutdown requests arrive before completion.

## 6. Technology Stack

- **Python 3.10+** for both client and server runtimes ensures consistent standard library features and typing support.
- **AsyncIO** provides cooperative multitasking; every network service awaits events without blocking others.
- **FastAPI + Uvicorn** power both the admin API and the embedded client UI server, offering type hints, automatic docs, and a predictable concurrency model.
- **OpenCV / NumPy / PyAV / SoundDevice** deliver media capture, encoding, and playback across platforms.
- **Jinja2 + Vanilla JS** underpin the admin dashboard and participant UI so operators can customize layouts without adopting a full framework.
- **RotatingFileHandler** (optional) keeps server logs bounded when long-running sessions are expected.

## 7. Deployment Model

- **Server process**: Started via `python -m server` or distributed as a PyInstaller executable. Ports for control, audio, video, screen, file, latency, and admin dashboard are configurable at launch.
- **Client process**: Started via `python -m client` or as a packaged binary. Runs a local HTTP server (default port 8100) that renders the UI and exposes REST endpoints the browser can call.
- **Packaging**: `scripts/build_executables.py` bundles both sides into standalone executables. Assets are embedded so no separate installation steps are required.

## 8. Security and Resilience

- **Perimeter enforcement**: Rely on firewall rules, VLAN segmentation, or VPN policies to control access. Because all traffic is LAN-local, administrators retain full control over routing.
- **Session integrity**: Duplicate usernames, banned users, and invalid handshakes are rejected during the HELLO stage.
- **Fault tolerance**: Heartbeat watcher identifies stalled clients and clears them, preventing zombie entries in the session list.
- **Observability**: Admin dashboard exposes snapshots, log tails, storage usage, and event exports to aid troubleshooting.
- **Graceful recovery**: Clients automatically reconnect with backoff and display a reconnect banner in the UI. Operators can always initiate a controlled shutdown that clears residual state.

## 9. Extensibility Notes

- New media types can be added by following the existing pattern: introduce a service module, register it in the main server start/stop sequence, and expose control actions via the session manager.
- Custom authentication or directory integration can wrap the control handshake without disturbing media transports.
- Observability hooks (metrics, structured logs) can piggyback on the admin dashboard by extending the state snapshot payloads.

---

This architecture emphasizes predictable local performance, straightforward administration, and a shutdown story that always leaves storage clean. The separation between control, media, and UI surfaces keeps responsibilities clear and simplifies future enhancements.
