# LAN Collaboration Suite Architecture

## Overview

The system is a hub-and-spoke client/server application operating exclusively on a local area network (LAN). A single server coordinates sessions, relays real-time media, brokers reliable data transfers, echoes latency probes, and surfaces health diagnostics. Each client hosts a local web UI that interacts with a resident Python runtime responsible for networking, media capture, playback, presence reporting, and reconnection.

```
+-----------+        +-----------+
|  Client A | <--->  |           |
|  (Python) |   UDP  |           |
|  (Web UI) |   TCP  |  Server   |
+-----------+        |           |
                     |           |
+-----------+        |           |
|  Client B | <--->  |           |
|           |        |           |
+-----------+        +-----------+
```

## Communication Channels

| Feature                | Transport | Direction        | Notes |
|------------------------|-----------|------------------|-------|
| Audio conferencing     | UDP       | Client ↔ Server  | Low latency PCM chunks compressed with Opus-like codec (via PyAV/FFmpeg). |
| Video conferencing     | UDP       | Client ↔ Server  | JPEG/VP8 frame packets with sequence numbers and timestamps. |
| Screen/slide sharing   | TCP       | Presenter → Server → Viewers | Reliable stream of PNG frames. |
| Group chat             | TCP       | Client ↔ Server  | Length-prefixed JSON messages. |
| File sharing           | TCP       | Client ↔ Server  | Chunked transfers with resumable tokens. |
| Control signalling     | TCP       | Client ↔ Server  | Session join/leave, presence sync, typing state, reactions, presenter control, hand raises. |
| Heartbeat telemetry    | TCP       | Client → Server  | Clients emit periodic heartbeats; the server records timing to detect stalled control links and triggers reconnect flows. |
| Latency probe service  | UDP       | Client ↔ Server  | Signed echo packets measure round-trip time and jitter; network operators can restrict access using external controls. |
| UI bridge              | WebSocket (loopback) | Browser ↔ Client daemon | Bridge between web UI and client runtime for local interactions. |

## Modules

1. **Session Core (`shared/` + `server/session_manager.py`)**
   - Manages authenticated users, room membership, presence attributes (media state, hand status, typing, latency), and heartbeats, with a watchdog that prunes stale clients.
   - Tracks meeting time limits, rebroadcasts countdown updates, and signals expiry so every client can shut down gracefully without operator intervention.
   - Maintains routing tables for per-user audio/video sockets and emits presence snapshots/deltas to clients; logs heartbeat intervals when debug logging is enabled.

2. **Chat Service**
   - TCP-based, ensures ordered delivery.
   - Broadcasts to clients subscribed to the session and appends to chat history.
   - The browser UI raises unread badges and brief banners when messages arrive while the chat sidebar is hidden.

3. **Screen Sharing Service**
   - Single active presenter enforced via server arbitration.
   - TCP pipeline with delta encoding and adaptive frame rate.

4. **File Transfer Service**
   - TCP chunked uploads stored temporarily on the server.
   - Clients subscribe to metadata events, request downloads on-demand, and receive generated share links for clipboard copy.
   - Newly shared files surface unread badges in the web UI so participants spot uploads without opening the Files tab immediately.

5. **Media Mixer & Relay**
   - UDP sockets per client.
   - Audio: mix PCM frames using normalized summation; handles jitter via playout buffers.
   - Video: forward frames to subscribed clients; adaptive down-scaling under high load.

6. **Latency Echo Service (`server/latency_server.py`)**
   - UDP responder that measures round-trip time for each client probe.
   - Designed for trusted LAN/VPN deployments; pair with perimeter firewalls or NAT rules to prevent spoofed probes.

7. **Admin HTTP (`server/admin_dashboard.py`)**
   - Serves dashboard assets, exposes `/api/health`, and relays kick/shutdown commands, broadcast notices, and time-limit configuration.
   - Surfaces participant filters, aggregated latency statistics, storage usage, meeting countdown status, and a rolling server log tail for operators.
   - Provides a "Stop server & clear files" workflow that flips a shutdown flag in the session manager, disconnects every participant with a reason banner, and triggers storage cleanup before asyncio services tear down.

## Technology Choices

- **Python 3.10+**: shared runtime across server and client.
- **AsyncIO**: concurrency model for sockets and service orchestration, including reconnect backoff and background latency probes.
- **FastAPI + Uvicorn**: lightweight HTTP/WebSocket server embedded in the client to host the UI, broadcast presence updates, and accept file uploads.
- **OpenCV / NumPy**: video capture, compression, and rendering.
- **PyAV**: audio encoding and decoding using Opus codec.
- **SoundDevice**: microphone capture and speaker playback.
- **Jinja2 + Vanilla JS**: front-end templating and dynamic UI updates, now covering reactions, presence, and drag/drop uploads.
- **RotatingFileHandler**: optional server log rotation without external log infrastructure.

## Deployment Model

- **Server**: packaged as a console application (later via PyInstaller). Listens on configurable ports for each service.
- **Client**: launches background daemons (media capture, control, latency probe) and exposes `http://localhost:<port>` for the UI. Opens the UI in the default browser automatically and reschedules control connections on failure.

## Security & Resilience

- **Trusted network perimeter**: Deploy behind LAN/VPN firewalls, ACLs, or other access controls to ensure only authorized hosts reach the control, media, and latency services.
- **Presence deltas**: `presence_sync` provides the full roster at join time; `presence_update` broadcasts deltas for individual participants to keep traffic lightweight.
- **Reconnect loop**: The client runtime schedules exponential backoff retries when the control channel drops, notifying the web UI so participants see a reconnect banner instead of a hard failure.
- **Latency telemetry**: The latency probe caches round-trip metrics, exposes them in the UI, and forwards updates to the control server for global presence visibility.
- **Health endpoint**: `/api/health` returns current session and service status, enabling container orchestrators or external monitors to verify readiness.
- **Graceful shutdown**: The admin shutdown endpoint is idempotent; the first request schedules disconnect-all broadcasts, file server cleanup, and orderly stopping of each async service. Subsequent requests return an "in progress" status so operators know teardown is already underway.
