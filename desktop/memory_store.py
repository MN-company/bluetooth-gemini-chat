from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")


class ChatMemoryStore:
    def __init__(
        self,
        file_path: str,
        max_turns: int = 120,
        max_turn_chars: int = 1200,
    ) -> None:
        self._path = Path(file_path)
        self._max_turns = max_turns
        self._max_turn_chars = max_turn_chars
        self._turns: list[dict[str, str]] = []
        self._load()

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def add_turn(self, role: str, text: str) -> None:
        normalized_role = role.strip().lower()
        if normalized_role not in {"user", "assistant"}:
            return

        cleaned_text = self._normalize(text)
        if not cleaned_text:
            return

        turn = {
            "role": normalized_role,
            "text": cleaned_text[: self._max_turn_chars],
        }
        self._turns.append(turn)
        if len(self._turns) > self._max_turns:
            self._turns = self._turns[-self._max_turns :]
        self._save()

    def clear(self) -> None:
        self._turns = []
        self._save()

    def recent_turns(self, max_items: int = 10, max_chars: int = 2600) -> list[dict[str, str]]:
        if max_items <= 0 or max_chars <= 0:
            return []

        selected: list[dict[str, str]] = []
        total_chars = 0

        for turn in reversed(self._turns):
            text = turn.get("text", "")
            if not text:
                continue

            projected = total_chars + len(text)
            if selected and projected > max_chars:
                break

            selected.append({"role": turn["role"], "text": text})
            total_chars = projected

            if len(selected) >= max_items:
                break

        selected.reverse()
        return selected

    def _normalize(self, value: str) -> str:
        return _WHITESPACE_RE.sub(" ", value).strip()

    def _load(self) -> None:
        if not self._path.exists():
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(raw, list):
            return

        turns: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            text = self._normalize(str(item.get("text", "")))
            if role not in {"user", "assistant"} or not text:
                continue
            turns.append({"role": role, "text": text[: self._max_turn_chars]})

        self._turns = turns[-self._max_turns :]

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._turns, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            # If write fails we keep runtime memory; next write may succeed.
            pass
