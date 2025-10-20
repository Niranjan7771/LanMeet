# LAN Collaboration Suite

The LAN Collaboration Suite delivers real-time audio, video, screen sharing, chat, and file transfer to every device on your local network—no cloud dependency required. A lightweight Python control plane coordinates media services, while a modern web client gives participants full control in their browser.

## Highlights

| Capability | What it does |
| --- | --- |
| **Video conferencing** | Captures camera frames, compresses them as JPEG, streams over UDP, and provides a live self-preview in the browser UI. |
| **Audio rooms** | Mixes live microphone input from every participant and replays to peers with minimal latency. |
| **Screen sharing** | Lets one presenter share their desktop via reliable TCP until another takes the role, with an adjustable viewer size for attendees. |
| **Rich chat** | Maintains per-session chat history, timestamps, and sender metadata. |
| **File transfers** | Supports multiple simultaneous uploads, resumable chunks, and direct browser downloads. |
| **Admin dashboard** | Shows connected users, presenter status, chat log, recent events, and live network metrics (connection type, IP, ports, throughput, bandwidth). |

## Project Structure

```
server/            # Session manager, control server, and media services
client/            # Local client runtime that bridges sockets ↔ web UI
shared/            # Protocol constants, message schemas, and helpers
webui/             # HTML/CSS/JS rendered in participant browsers
adminui/           # Static assets for the admin dashboard
docs/              # Architecture notes and design decisions
tests/             # Pytest suites for protocol and session logic
```

## Prerequisites

- Python **3.10 or newer** (project is validated on 3.13).
- Windows, macOS, or Linux with webcam/microphone access (video/audio capture uses OpenCV and sounddevice).
- All participants must be on the same LAN; ports are configurable for stricter firewall environments.

## Quickstart

### 1. Clone and enter the repository

```powershell
git clone https://github.com/Niranjan7771/LanMeet.git
cd LanMeet
```

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

Editable install with dev extras provides the server, client, and tooling:

```powershell
pip install -e .[development]
```

If you only need runtime dependencies:

```powershell
pip install -e .
```

Verify the installation by running the test suite:

```powershell
pytest
```

### 4. Start the collaboration server

```powershell
python -m server --host 0.0.0.0 --tcp-port 55000 --video-port 56000 --audio-port 57000 --screen-port 58000 --file-port 59000 --admin-port 8700
```

**Flags explained:**

- `--host 0.0.0.0` — bind to all interfaces so other LAN devices can connect.
- `--tcp-port 55000` — control-channel TCP port (default `55000`).
- `--video-port 56000`, `--audio-port 57000`, `--screen-port 58000`, `--file-port 59000` — UDP/TCP transports for media services (defaults from `shared.protocol`).
- `--admin-port 8700` — admin dashboard HTTP port (default `8700`).
- `--admin-host 0.0.0.0` — add this if the dashboard must be reachable from other machines.
- `--open-dashboard` — open the admin dashboard in a browser after startup (enabled automatically when launched without CLI arguments, e.g., by double-clicking the executable).
- `--log-level DEBUG` — optional verbosity control for the server (defaults to `INFO`).

**Example:** Restrict access to the local machine for testing:

```powershell
python -m server --host 127.0.0.1 --admin-host 127.0.0.1
```

The console logs each media service listener, prints URLs for the admin dashboard, and now records heartbeat diagnostics when run with `--log-level debug`.

### 5. Launch a client runtime per participant

```powershell
python -m client 192.168.1.50 --tcp-port 55000 --ui-port 8100 --username "alex"
```

**Parameters:**

- `192.168.1.50` — IP of the machine running the server (if omitted, the client prompts for the value—useful when double-clicking a packaged executable).
- `--tcp-port` — must match the server’s control port.
- `--ui-port` — HTTP port serving the web UI (default `8100`).
- `--username` — optional initial display name; can be changed from the UI.
- `--log-level` — choose `DEBUG` when you need per-heartbeat logs; defaults to `INFO`.

Once started, the client automatically opens the browser at `http://127.0.0.1:8100`. Pick a display name (or randomize), then use the control bar to toggle microphone, camera, and screen sharing. A “Leave” button provides a 20-second undo window before fully disconnecting.
If the admin kicks a participant or they leave through the UI, the accompanying command-line client now shuts itself down automatically—no Ctrl+C required.

### 6. Join through the browser

1. Enter or randomize your display name.
2. Click **Join Session**.
3. Toggle microphone, camera, or screen sharing as needed.
4. Use the chat panel for text messages; uploads appear in the File Sharing pane with download buttons for everyone.

The UI persists session state across page refreshes—chat history, presenter, files, and media toggles are restored automatically.

### 7. Monitor with the admin dashboard (optional)

Open the dashboard from any device that can reach the admin host/port:

```powershell
start http://127.0.0.1:8700
```

You’ll see live participant counts, current presenter, recent events, and chat messages. For remote access, adjust the `--admin-host` / `--admin-port` arguments on the server command.

### 8. Automate large test sessions (optional)

Use the orchestration helper in `scripts/cluster_launcher.py` to spin up the server, open the admin dashboard, and launch a configurable number of client instances on sequential UI ports:

```powershell
python scripts/cluster_launcher.py 172.17.248.69 --open-dashboard --clients 50 --ui-start-port 8100 --ui-port-step 1
```

- `--python` selects the interpreter if you are not using the current environment.
- `--server-startup-delay` and `--client-delay` tune wait times between launches.
- `--ui-start-port` and `--ui-port-step` determine the port range for the embedded client web servers.
- Pass `--workspace` if you need the subprocesses to run from a different working directory.

The script keeps track of every spawned process and tears them down cleanly when you press Ctrl+C, making it ideal for stress-testing or demo setups.

## Standalone Executables

You can distribute the client and server as standalone binaries built with [PyInstaller](https://pyinstaller.org/en/stable/).

1. Install PyInstaller in your virtual environment:

	```powershell
	pip install pyinstaller
	```

2. Run the build helper from the repository root (Windows PowerShell example shown):

	```powershell
	python scripts/build_executables.py --onefile --clean
	```

	The script packages both the server (`lanmeet-server`) and the client (`lanmeet-client`) into the `dist/` directory. Use `--skip-server` or `--skip-client` to build only one side. The `--onefile` flag bundles everything into a single executable; omit it to produce unpacked folders (faster startup).

3. Launch the generated binaries:

	```powershell
	.\dist\lanmeet-server.exe --host 0.0.0.0 --tcp-port 55000
	.\dist\lanmeet-client.exe 192.168.1.50 --tcp-port 55000 --ui-port 8100
	```

On Windows you can simply double-click the packaged binaries:

- `lanmeet-server.exe` starts with default ports and automatically opens the admin dashboard in your default browser.
- `lanmeet-client.exe` prompts for the server hostname/IP and then launches the browser UI.

The executables locate their bundled static assets automatically, so no extra configuration is required. If you prefer custom output directories, provide `--dist`, `--build`, or `--spec` arguments to the build helper.

## Troubleshooting & Tips

- **Heartbeat timeouts:** Run the server with `--log-level debug` and start clients with `--log-level DEBUG` to trace heartbeat send/receive intervals; matching timestamps quickly surface stalled links.
- **Firewall rules:** Allow inbound UDP/TCP on the configured ports (defaults: TCP `55000`, UDP `56000` series, HTTP `8100` & `8700`).
- **No video/audio devices:** The video and audio modules degrade gracefully; check logs for hardware warnings.
- **Rejoining quickly:** Use the Leave button’s cancel countdown if you disconnect accidentally.
- **File transfer limits:** Large uploads depend on LAN speed; progress updates appear in the status line and File Sharing list.
- **Headless environments:** You can run the client without auto-launching a browser by passing `--no-browser` (see `python -m client --help`).

## Running Tests

Execute all automated checks:

```powershell
pytest
```

Run a specific module:

```powershell
pytest tests/test_session_manager.py -q
```

Enable verbose output:

```powershell
pytest -vv
```

## Deployment Notes

- Intended for LAN or VPN environments; no authentication is included by default.
- For kiosk setups, launch the client as a background service and put the browser in fullscreen kiosk mode.
- Media quality parameters (frame rate, resolution, audio buffer size) can be tuned in the respective client modules.

## License

This project is released under the **MIT License**. See [`LICENSE`](LICENSE) for full details.
