"""Persistent, scoped conversation memory for human-friendly follow-ups.

Each group/member pair has one JSON file under the project workspace.  The
file deliberately contains only a short rolling exchange plus an LLM-produced
summary; it is not a full group-chat archive.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import MemoryConfig

logger = logging.getLogger("conversation_memory")

SummaryFunction = Callable[[str, list[dict[str, str]]], Awaitable[str]]
_SAFE_COMPONENT_RE = re.compile(r"[^0-9A-Za-z_-]+")
_VALID_ROLES = {"user", "assistant"}


class ConversationMemoryStore:
    """Keep a small persistent memory per conversation scope and QQ user."""

    def __init__(self, config: MemoryConfig, working_dir: str | Path) -> None:
        self._config = config
        self._working_dir = Path(working_dir).resolve()
        configured = Path(config.directory)
        self._directory = (
            configured if configured.is_absolute() else self._working_dir / configured
        ).resolve()
        try:
            self._directory.relative_to(self._working_dir)
        except ValueError as exc:
            raise ValueError("conversation memory directory must stay in the project workspace") from exc
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @property
    def directory(self) -> Path:
        return self._directory

    def memory_path(self, scope_id: str, user_id: str) -> Path:
        scope_name = self._component(str(scope_id))
        user_name = self._component(str(user_id))
        return self._directory / f"scope_{scope_name}" / f"user_{user_name}.json"

    def get_history(self, scope_id: str, user_id: str) -> list[dict[str, str]]:
        """Return the durable recent turns only, in chronological order."""
        if not self._config.enabled:
            return []
        data = self._load(scope_id, user_id)
        return [
            {"role": turn["role"], "content": turn["content"]}
            for turn in data["recent_turns"]
        ]

    def summary_for_prompt(self, scope_id: str, user_id: str) -> str:
        """Return a bounded, non-authoritative long-term memory summary."""
        if not self._config.enabled:
            return ""
        summary = self._load(scope_id, user_id)["summary"].strip()
        if not summary:
            return ""
        return summary[: self._config.summary_max_chars]

    async def add_turn(
        self,
        scope_id: str,
        user_id: str,
        *,
        role: str,
        content: str,
        summarize: SummaryFunction,
    ) -> None:
        """Persist one exchange, compacting old turns only when the limit is crossed."""
        if not self._config.enabled:
            return
        if role not in _VALID_ROLES:
            raise ValueError(f"unsupported memory role: {role}")
        text = str(content).strip()
        if not text:
            return

        key = (str(scope_id), str(user_id))
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            data = self._load(scope_id, user_id)
            turns = data["recent_turns"]
            turns.append(
                {
                    "role": role,
                    "content": text[: self._config.max_turn_chars],
                    "at": self._now(),
                }
            )

            if len(turns) > self._config.recent_turn_limit:
                retain_count = min(
                    self._config.retain_after_compaction,
                    self._config.recent_turn_limit,
                )
                older_turns = turns[:-retain_count]
                try:
                    summary = await summarize(data["summary"], older_turns)
                except Exception:
                    logger.exception("对话记忆摘要失败；改用本地压缩回退")
                    summary = ""
                data["summary"] = self._clean_summary(
                    summary or self._fallback_summary(data["summary"], older_turns)
                )
                data["recent_turns"] = turns[-retain_count:]

            data["updated_at"] = self._now()
            self._write(scope_id, user_id, data)

    def _load(self, scope_id: str, user_id: str) -> dict[str, Any]:
        path = self.memory_path(scope_id, user_id)
        if not path.exists():
            return self._empty(scope_id, user_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"无法读取对话记忆文件: {path}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"对话记忆文件格式错误: {path}")

        summary = raw.get("summary", "")
        turns_raw = raw.get("recent_turns", [])
        turns: list[dict[str, str]] = []
        if isinstance(turns_raw, list):
            for item in turns_raw[-self._config.recent_turn_limit :]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", ""))
                content = str(item.get("content", "")).strip()
                if role in _VALID_ROLES and content:
                    turns.append(
                        {
                            "role": role,
                            "content": content[: self._config.max_turn_chars],
                            "at": str(item.get("at", "")),
                        }
                    )
        return {
            "version": 1,
            "scope_id": str(scope_id),
            "user_id": str(user_id),
            "summary": self._clean_summary(str(summary)),
            "recent_turns": turns,
            "updated_at": str(raw.get("updated_at", "")),
        }

    def _write(self, scope_id: str, user_id: str, data: dict[str, Any]) -> None:
        path = self.memory_path(scope_id, user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _empty(self, scope_id: str, user_id: str) -> dict[str, Any]:
        return {
            "version": 1,
            "scope_id": str(scope_id),
            "user_id": str(user_id),
            "summary": "",
            "recent_turns": [],
            "updated_at": "",
        }

    def _clean_summary(self, value: str) -> str:
        compact = re.sub(r"\s+", " ", value).strip()
        return compact[: self._config.summary_max_chars]

    def _fallback_summary(self, previous: str, older_turns: list[dict[str, str]]) -> str:
        """Loss-minimising fallback used only if the local summary call fails."""
        parts = [previous.strip()] if previous.strip() else []
        for turn in older_turns:
            speaker = "用户" if turn.get("role") == "user" else "机器人"
            text = str(turn.get("content", "")).strip()
            if text:
                parts.append(f"{speaker}：{text[:180]}")
        return "；".join(parts)

    @staticmethod
    def _component(value: str) -> str:
        normalized = _SAFE_COMPONENT_RE.sub("_", value).strip("_") or "unknown"
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        return f"{normalized[:48]}_{digest}"

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
