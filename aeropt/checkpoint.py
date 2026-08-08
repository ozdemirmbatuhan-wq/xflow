from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import uuid
from typing import Any


CHECKPOINT_SCHEMA = 1


def optimizer_fingerprint(label: str, payload: dict[str, Any]) -> str:
    """Return a stable key for one exact optimizer problem."""
    encoded = json.dumps(
        {"label": label, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_checkpoint_dir() -> Path:
    platform_cache = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if platform_cache:
        base = Path(platform_cache)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path.home() / ".cache"
    return base / "AeroOpt" / "optimizer-checkpoints-v1"


class OptimizerCheckpointStore:
    """Atomic JSON checkpoints for DE populations and RNG state."""

    def __init__(self, *, enabled: bool = True, directory: str | Path | None = None):
        self.enabled = bool(enabled)
        self.directory = (
            Path(directory).expanduser() if directory and str(directory).strip() else _default_checkpoint_dir()
        )
        self._lock = threading.Lock()
        self.loads = 0
        self.saves = 0
        self.clears = 0

    def _path(self, key: str) -> Path:
        safe_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / safe_key[:2] / f"{safe_key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            not isinstance(value, dict)
            or value.get("schema") != CHECKPOINT_SCHEMA
            or value.get("key") != key
            or not isinstance(value.get("state"), dict)
        ):
            return None
        with self._lock:
            self.loads += 1
        return value["state"]

    def save(self, key: str, state: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        path = self._path(key)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    {"schema": CHECKPOINT_SCHEMA, "key": key, "state": state},
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        with self._lock:
            self.saves += 1
        return True

    def clear(self, key: str) -> bool:
        if not self.enabled:
            return False
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:
            return False
        with self._lock:
            self.clears += 1
        return True

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "loads": self.loads,
                "saves": self.saves,
                "clears": self.clears,
                "schema": CHECKPOINT_SCHEMA,
            }
