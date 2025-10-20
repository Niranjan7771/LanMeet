"""Helpers to resolve resource paths in both source and frozen builds."""
from __future__ import annotations

import sys
from pathlib import Path


def _base_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent


def project_root() -> Path:
    """Return the project root regardless of execution context."""
    return _base_path()


def resolve_path(*segments: str) -> Path:
    """Resolve one or more path segments relative to the project root."""
    return project_root().joinpath(*segments)
