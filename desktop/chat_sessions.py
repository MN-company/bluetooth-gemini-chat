from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class ChatSessionsStore:
    def __init__(
        self,
        file_path: str,
        max_sessions: int = 80,
        max_messages_per_session: int = 400,
        max_message_chars: int = 5000,
    ) -> None:
        self._path = Path(file_path)
        self._max_sessions = max_sessions
        self._max_messages_per_session = max_messages_per_session
        self._max_message_chars = max_message_chars

        self._sessions: list[dict[str, Any]] = []
        self._active_session_id: str | None = None
        self._load()

        if not self._sessions:
            self._active_session_id = self.create_session("Nuova chat")
        elif self._active_session_id is None:
            self._active_session_id = self._sessions[-1]["id"]

    @property
    def active_session_id(self) -> str:
        if self._active_session_id is None:
            self._active_session_id = self.create_session("Nuova chat")
        return self._active_session_id

    def list_sessions(self) -> list[dict[str, Any]]:
        ordered = sorted(self._sessions, key=lambda s: float(s.get("updatedAt", 0)), reverse=True)
        out: list[dict[str, Any]] = []
        for session in ordered:
            out.append(
                {
                    "id": session["id"],
                    "title": str(session.get("title", "Nuova chat")),
                    "updatedAt": float(session.get("updatedAt", 0)),
                    "messageCount": len(session.get("messages", [])),
                }
            )
        return out

    def set_active_session(self, session_id: str) -> bool:
        if self._get_session(session_id) is None:
            return False
        self._active_session_id = session_id
        self._save()
        return True

    def create_session(self, title: str = "Nuova chat") -> str:
        now = time.time()
        session_id = str(uuid.uuid4())
        session = {
            "id": session_id,
            "title": (title.strip() or "Nuova chat")[:120],
            "createdAt": now,
            "updatedAt": now,
            "messages": [],
        }
        self._sessions.append(session)
        self._trim_sessions()
        self._active_session_id = session_id
        self._save()
        return session_id

    def delete_session(self, session_id: str) -> bool:
        initial = len(self._sessions)
        self._sessions = [s for s in self._sessions if s.get("id") != session_id]
        if len(self._sessions) == initial:
            return False

        if not self._sessions:
            self._active_session_id = self.create_session("Nuova chat")
            return True

        if self._active_session_id == session_id:
            self._active_session_id = sorted(
                self._sessions, key=lambda s: float(s.get("updatedAt", 0)), reverse=True
            )[0]["id"]
        self._save()
        return True

    def rename_session(self, session_id: str, title: str) -> bool:
        session = self._get_session(session_id)
        if session is None:
            return False
        new_title = (title.strip() or "Nuova chat")[:120]
        session["title"] = new_title
        session["updatedAt"] = time.time()
        self._save()
        return True

    def get_messages(self, session_id: str) -> list[dict[str, str]]:
        session = self._get_session(session_id)
        if session is None:
            return []
        out: list[dict[str, str]] = []
        for msg in session.get("messages", []):
            role = str(msg.get("role", "")).strip().lower()
            text = str(msg.get("text", "")).strip()
            if not role or not text:
                continue
            out.append({"role": role, "text": text})
        return out

    def add_message(self, session_id: str, role: str, text: str) -> bool:
        session = self._get_session(session_id)
        if session is None:
            return False

        normalized_role = role.strip().lower()
        if normalized_role not in {"user", "assistant", "system", "phone", "error", "thought"}:
            normalized_role = "system"

        clean_text = " ".join(text.split()).strip()
        if not clean_text:
            return False

        now = time.time()
        messages = session.setdefault("messages", [])
        messages.append(
            {
                "role": normalized_role,
                "text": clean_text[: self._max_message_chars],
                "ts": now,
            }
        )
        if len(messages) > self._max_messages_per_session:
            session["messages"] = messages[-self._max_messages_per_session :]

        if normalized_role == "user" and len(messages) <= 2:
            session["title"] = self._auto_title(clean_text)

        session["updatedAt"] = now
        self._save()
        return True

    def recent_turns(
        self,
        session_id: str,
        max_items: int = 10,
        max_chars: int = 2600,
    ) -> list[dict[str, str]]:
        session = self._get_session(session_id)
        if session is None or max_items <= 0 or max_chars <= 0:
            return []

        selected: list[dict[str, str]] = []
        total_chars = 0

        for msg in reversed(session.get("messages", [])):
            role = str(msg.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            text = str(msg.get("text", "")).strip()
            if not text:
                continue

            projected = total_chars + len(text)
            if selected and projected > max_chars:
                break

            selected.append({"role": role, "text": text})
            total_chars = projected
            if len(selected) >= max_items:
                break

        selected.reverse()
        return selected

    def clear_messages(self, session_id: str) -> bool:
        session = self._get_session(session_id)
        if session is None:
            return False
        session["messages"] = []
        session["updatedAt"] = time.time()
        self._save()
        return True

    def _get_session(self, session_id: str) -> dict[str, Any] | None:
        for session in self._sessions:
            if session.get("id") == session_id:
                return session
        return None

    def _auto_title(self, user_text: str) -> str:
        compact = user_text.replace("\n", " ").strip()
        if len(compact) > 48:
            compact = compact[:47].rstrip() + "…"
        return compact or "Nuova chat"

    def _trim_sessions(self) -> None:
        if len(self._sessions) <= self._max_sessions:
            return
        ordered = sorted(self._sessions, key=lambda s: float(s.get("updatedAt", 0)), reverse=True)
        keep = ordered[: self._max_sessions]
        keep_ids = {s["id"] for s in keep}
        self._sessions = [s for s in self._sessions if s.get("id") in keep_ids]
        if self._active_session_id not in keep_ids:
            self._active_session_id = keep[0]["id"]

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(raw, dict):
            return

        sessions_raw = raw.get("sessions")
        if not isinstance(sessions_raw, list):
            sessions_raw = []

        sessions: list[dict[str, Any]] = []
        for item in sessions_raw:
            if not isinstance(item, dict):
                continue
            session_id = str(item.get("id", "")).strip()
            if not session_id:
                continue
            title = str(item.get("title", "Nuova chat")).strip() or "Nuova chat"
            created_at = float(item.get("createdAt", 0) or 0)
            updated_at = float(item.get("updatedAt", 0) or 0)
            messages_raw = item.get("messages", [])
            messages: list[dict[str, Any]] = []
            if isinstance(messages_raw, list):
                for msg in messages_raw:
                    if not isinstance(msg, dict):
                        continue
                    role = str(msg.get("role", "")).strip().lower()
                    text = str(msg.get("text", "")).strip()
                    ts = float(msg.get("ts", 0) or 0)
                    if not text:
                        continue
                    if role not in {"user", "assistant", "system", "phone", "error", "thought"}:
                        role = "system"
                    messages.append(
                        {
                            "role": role,
                            "text": text[: self._max_message_chars],
                            "ts": ts,
                        }
                    )
            sessions.append(
                {
                    "id": session_id,
                    "title": title[:120],
                    "createdAt": created_at,
                    "updatedAt": max(updated_at, created_at),
                    "messages": messages[-self._max_messages_per_session :],
                }
            )

        self._sessions = sessions[-self._max_sessions :]
        active = str(raw.get("activeSessionId", "")).strip()
        if active and self._get_session(active) is not None:
            self._active_session_id = active
        elif self._sessions:
            self._active_session_id = self._sessions[-1]["id"]

    def _save(self) -> None:
        payload = {
            "activeSessionId": self._active_session_id,
            "sessions": self._sessions,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            pass
