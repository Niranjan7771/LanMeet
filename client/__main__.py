from __future__ import annotations

import argparse
import asyncio
import logging

from shared.protocol import DEFAULT_TCP_PORT

from .app import ClientApp

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN collaboration suite client")
    parser.add_argument("server_host", help="Hostname or IP of the collaboration server")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="Server TCP port")
    parser.add_argument("--ui-host", default="127.0.0.1", help="Host to bind the local UI web server")
    parser.add_argument("--ui-port", type=int, default=8100, help="Port for the local UI web server")
    parser.add_argument("--username", help="Optional display name to pre-fill in the UI")
    args = parser.parse_args()

    app = ClientApp(username=args.username, server_host=args.server_host, tcp_port=args.tcp_port)

    asyncio.run(app.run(host=args.ui_host, port=args.ui_port))


if __name__ == "__main__":
    main()
