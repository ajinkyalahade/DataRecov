"""
Scan checkpoint — persists scan state for resume support.
Written to <output_dir>/.drt_checkpoint.json every 60 seconds.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_CHECKPOINT_FILENAME = ".drt_checkpoint.json"


def load(output_dir: str) -> dict | None:
    """Load existing checkpoint from output_dir. Returns None if not found."""
    path = Path(output_dir) / _CHECKPOINT_FILENAME
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(output_dir: str, state: dict) -> None:
    """Atomically write checkpoint (write to .tmp then rename)."""
    path = Path(output_dir) / _CHECKPOINT_FILENAME
    tmp_path = Path(output_dir) / (_CHECKPOINT_FILENAME + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        state_copy = dict(state)
        state_copy["last_checkpoint"] = datetime.now(timezone.utc).isoformat()
        tmp_path.write_text(
            json.dumps(state_copy, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(path))
    except Exception:
        pass


def delete(output_dir: str) -> None:
    """Remove checkpoint file on successful scan completion."""
    path = Path(output_dir) / _CHECKPOINT_FILENAME
    try:
        if path.is_file():
            path.unlink()
    except Exception:
        pass


class CheckpointWriter:
    """
    Background thread that periodically calls save().
    Usage:
        cw = CheckpointWriter(output_dir, state_fn, interval_seconds=60)
        cw.start()
        # ... scan runs ...
        cw.stop()
    state_fn is a callable that returns the current state dict to save.
    """

    def __init__(self, output_dir: str, state_fn, interval_seconds: int = 60) -> None:
        self._output_dir = output_dir
        self._state_fn = state_fn
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="CheckpointWriter",
        )

    def start(self) -> None:
        self._stop_event.clear()
        # Recreate the thread so start() is safe to call more than once.
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="CheckpointWriter",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval):
            try:
                state = self._state_fn()
                save(self._output_dir, state)
            except Exception:
                pass
