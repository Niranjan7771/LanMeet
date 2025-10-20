"""Utility for launching the LAN Meet server plus multiple clients."""
from __future__ import annotations

import argparse
import atexit
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ProcessRecord = tuple[str, subprocess.Popen]

PROCESSES: list[ProcessRecord] = []


def _register_process(name: str, proc: subprocess.Popen) -> None:
    PROCESSES.append((name, proc))


def _terminate_process(name: str, proc: subprocess.Popen, timeout: float) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


def _cleanup() -> None:
    while PROCESSES:
        name, proc = PROCESSES.pop()
        try:
            _terminate_process(name, proc, timeout=5.0)
        except Exception:
            pass


def _handle_signal(signum: int, frame: object) -> None:  # pragma: no cover - signal runtime
    _cleanup()
    sys.exit(0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch server, dashboard, and multiple clients")
    parser.add_argument("client_target", help="Server hostname or IP the clients should connect to")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter to use for subprocesses")
    parser.add_argument("--server-host", default="0.0.0.0")
    parser.add_argument("--server-tcp-port", type=int, default=55000)
    parser.add_argument("--admin-port", type=int, default=8700)
    parser.add_argument("--admin-host", default="127.0.0.1")
    parser.add_argument("--clients", type=int, default=50, help="Number of client instances to launch")
    parser.add_argument("--client-tcp-port", type=int, default=55000)
    parser.add_argument("--ui-host", default="127.0.0.1", help="Host binding for each client UI server")
    parser.add_argument("--ui-start-port", type=int, default=8100, help="Base port for client UI servers")
    parser.add_argument("--ui-port-step", type=int, default=1, help="Increment between UI ports")
    parser.add_argument("--client-delay", type=float, default=0.2, help="Delay between starting clients")
    parser.add_argument("--server-startup-delay", type=float, default=2.0, help="Delay before launching clients")
    parser.add_argument("--open-dashboard", action="store_true", help="Open the admin dashboard in a browser")
    parser.add_argument("--dashboard-delay", type=float, default=1.0, help="Delay before launching the dashboard browser")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parent.parent), help="Working directory")
    return parser.parse_args()


def _launch_process(name: str, cmd: list[str], cwd: str) -> subprocess.Popen:
    proc = subprocess.Popen(cmd, cwd=cwd)
    _register_process(name, proc)
    return proc


def main() -> None:
    args = _parse_args()
    atexit.register(_cleanup)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception:
            pass

    workdir = args.workspace
    python_exec = args.python

    server_cmd = [
        python_exec,
        "-m",
        "server",
        "--host",
        args.server_host,
        "--tcp-port",
        str(args.server_tcp_port),
        "--admin-port",
        str(args.admin_port),
    ]
    print(f"Starting server: {' '.join(server_cmd)}")
    _launch_process("server", server_cmd, cwd=workdir)

    if args.open_dashboard:
        dashboard_url = f"http://{args.admin_host}:{args.admin_port}"
        time.sleep(max(args.dashboard_delay, 0.0))
        print(f"Opening admin dashboard at {dashboard_url}")
        webbrowser.open(dashboard_url)

    time.sleep(max(args.server_startup_delay, 0.0))

    for index in range(args.clients):
        ui_port = args.ui_start_port + index * args.ui_port_step
        client_cmd = [
            python_exec,
            "-m",
            "client",
            args.client_target,
            "--tcp-port",
            str(args.client_tcp_port),
            "--ui-host",
            args.ui_host,
            "--ui-port",
            str(ui_port),
        ]
        print(f"Starting client {index + 1}/{args.clients} on UI port {ui_port}")
        _launch_process(f"client-{ui_port}", client_cmd, cwd=workdir)
        time.sleep(max(args.client_delay, 0.0))

    print("All processes started. Press Ctrl+C to stop everything.")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
