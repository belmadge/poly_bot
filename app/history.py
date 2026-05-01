from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonlStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")

    def read_last(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-max(1, limit):]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
