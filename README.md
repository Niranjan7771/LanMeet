# LAN Collaboration Suite

A LAN-only collaboration platform that provides real-time multi-user audio/video conferencing, screen sharing, group chat, and file transfer without any internet connectivity. The project is built with Python for the networking stack and a web-based user interface served from the client application.

## Features

- ✅ **Multi-user video conferencing** over UDP with compressed JPEG frames rendered in the web UI.
- ✅ **Multi-user audio conferencing** over UDP with server-side mixing and playback.
- ✅ **Slide & screen sharing** using reliable TCP streams with presenter control.
- ✅ **Group text chat** over TCP with persistent history per session.
- ✅ **File sharing** with resumable uploads/downloads and progress reporting.

## Repository Layout

```
server/      # Core server application managing sessions and media routing
client/      # Client runtime and web UI host
shared/      # Shared protocol definitions and utilities
webui/       # HTML/CSS/JS assets for the client interface
docs/        # Architecture and user documentation
tests/       # Automated tests
```

## Getting Started

> **Status:** End-to-end real-time collaboration stack implemented. Adjust media quality settings as needed for your hardware and LAN.

1. Create and activate a virtual environment (Python 3.10+).
2. Install dependencies: `pip install -e .[development]`.
3. Launch the server (LAN host):
	```powershell
	python -m server --host 0.0.0.0
	```
4. Launch the client (per participant):
	```powershell
	python -m client <username> <server_ip>
	```
	The client opens `http://127.0.0.1:8100` in your default browser.

## License

TBD.
