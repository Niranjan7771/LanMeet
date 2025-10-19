# LAN Collaboration Suite Architecture

## Overview

The system is a hub-and-spoke client/server application operating exclusively on a local area network (LAN). A single server coordinates sessions, relays real-time media, and brokers reliable data transfers. Each client hosts a local web UI that interacts with a resident Python runtime responsible for networking, media capture, and playback.

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
| Control signalling     | TCP       | Client ↔ Server  | Session join/leave, user presence, presenter control. |
| Heartbeat telemetry    | TCP       | Client → Server  | Clients emit periodic heartbeats; the server records timing to detect stalled control links. |
| UI bridge              | WebSocket (loopback) | Browser ↔ Client daemon | Bridge between web UI and client runtime for local interactions. |

## Modules

1. **Session Core (`shared/` + `server/session_manager.py`)**
   - Manages authenticated users, room membership, and heartbeats, with a watchdog that prunes stale clients.
   - Maintains routing tables for per-user audio/video sockets and logs heartbeat intervals when debug logging is enabled.

2. **Chat Service**
   - TCP-based, ensures ordered delivery.
   - Broadcasts to clients subscribed to the session and appends to chat history.

3. **Screen Sharing Service**
   - Single active presenter enforced via server arbitration.
   - TCP pipeline with delta encoding and adaptive frame rate.

4. **File Transfer Service**
   - TCP chunked uploads stored temporarily on the server.
   - Clients subscribe to metadata events and request downloads on-demand.

5. **Media Mixer & Relay**
   - UDP sockets per client.
   - Audio: mix PCM frames using normalized summation; handles jitter via playout buffers.
   - Video: forward frames to subscribed clients; adaptive down-scaling under high load.

## Technology Choices

- **Python 3.10+**: shared runtime across server and client.
- **AsyncIO**: concurrency model for sockets and service orchestration.
- **FastAPI + Uvicorn**: lightweight HTTP/WebSocket server embedded in the client to host the UI.
- **OpenCV / NumPy**: video capture, compression, and rendering.
- **PyAV**: audio encoding and decoding using Opus codec.
- **SoundDevice**: microphone capture and speaker playback.
- **Jinja2 + Vanilla JS**: front-end templating and dynamic UI updates.

## Deployment Model

- **Server**: packaged as a console application (later via PyInstaller). Listens on configurable ports for each service.
- **Client**: launches background daemons (media capture, control) and exposes `http://localhost:<port>` for the UI. Opens the UI in the default browser automatically.

## Next Steps

1. Implement shared protocol schema (`shared/protocol.py`).
2. Build session manager and chat subsystem (foundation for remaining modules).
3. Add screen sharing pipeline.
4. Implement file transfer orchestration.
5. Layer in UDP media streaming and mixing.
6. Polish UI and integrate with the above services.
7. Add automated integration tests and documentation.
