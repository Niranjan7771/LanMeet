"""Build standalone executables for the LAN Meet server and client using PyInstaller."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent


def _format_data_arg(source: Path, target: str) -> str:
    return f"{source}{os.pathsep}{target}"


def _run_pyinstaller(python: str, args: Sequence[str]) -> None:
    print("Executing:", " ".join([python, "-m", "PyInstaller", *args]))
    subprocess.run([python, "-m", "PyInstaller", *args], check=True)


def _common_args(
    name: str,
    dist_dir: Path,
    build_dir: Path,
    spec_dir: Path,
    onefile: bool,
    clean: bool,
) -> list[str]:
    cmd = [
        "--name",
        name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir / name),
        "--specpath",
        str(spec_dir),
        "--console",
    ]
    cmd.append("--onefile" if onefile else "--onedir")
    if clean:
        cmd.append("--clean")
    return cmd


def _add_data_options(cmd: list[str], entries: Iterable[tuple[Path, str]]) -> None:
    for source, target in entries:
        cmd.extend(["--add-data", _format_data_arg(source, target)])


def _add_hidden_imports(cmd: list[str], imports: Iterable[str]) -> None:
    for value in imports:
        cmd.extend(["--hidden-import", value])


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build standalone executables for server and client")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter used to invoke PyInstaller")
    parser.add_argument("--dist", default=str(ROOT_DIR / "dist"), help="Directory for finalized executables")
    parser.add_argument("--build", default=str(ROOT_DIR / "build"), help="Directory for PyInstaller build artifacts")
    parser.add_argument("--spec", default=str(ROOT_DIR / "build" / "spec"), help="Directory to store generated spec files")
    parser.add_argument("--onefile", action="store_true", help="Produce single-file executables")
    parser.add_argument("--clean", action="store_true", help="Clean PyInstaller cache before building")
    parser.add_argument("--skip-server", action="store_true", help="Skip building the server executable")
    parser.add_argument("--skip-client", action="store_true", help="Skip building the client executable")
    parser.add_argument("--server-name", default="lanmeet-server")
    parser.add_argument("--client-name", default="lanmeet-client")
    return parser.parse_args()


def main() -> None:
    args = parse_cli()
    dist_dir = Path(args.dist)
    build_dir = Path(args.build)
    spec_dir = Path(args.spec)

    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_server:
        server_cmd = _common_args(
            name=args.server_name,
            dist_dir=dist_dir,
            build_dir=build_dir,
            spec_dir=spec_dir,
            onefile=args.onefile,
            clean=args.clean,
        )
        _add_data_options(
            server_cmd,
            [
                (ROOT_DIR / "adminui", "adminui"),
                (ROOT_DIR / "assets", "assets"),
            ],
        )
        _add_hidden_imports(server_cmd, ["shared.resource_paths"])
        server_entry = ROOT_DIR / "server" / "__main__.py"
        _run_pyinstaller(args.python, [*server_cmd, str(server_entry)])

    if not args.skip_client:
        client_cmd = _common_args(
            name=args.client_name,
            dist_dir=dist_dir,
            build_dir=build_dir,
            spec_dir=spec_dir,
            onefile=args.onefile,
            clean=args.clean,
        )
        _add_data_options(
            client_cmd,
            [
                (ROOT_DIR / "webui", "webui"),
                (ROOT_DIR / "assets", "assets"),
            ],
        )
        _add_hidden_imports(client_cmd, ["shared.resource_paths"])
        client_entry = ROOT_DIR / "client" / "__main__.py"
        _run_pyinstaller(args.python, [*client_cmd, str(client_entry)])

    print("Build complete. Executables are located in", dist_dir)


if __name__ == "__main__":
    main()
