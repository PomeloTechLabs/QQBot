from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("knowledge_store")


@dataclass
class KnowledgeItem:
    id: int
    title: str
    category: str
    content: str
    tags: list[str]
    updated_at: str
    updated_by: str

    def to_dict(self) -> dict:
        return asdict(self)


class KnowledgeStore:
    def __init__(self, json_path: str | Path, render_path: str | Path) -> None:
        self._json_path = Path(json_path)
        self._render_path = Path(render_path)
        self._items: list[KnowledgeItem] = []
        self._next_id = 1
        self._render_cache = ""
        self._lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._json_path.parent.mkdir(parents=True, exist_ok=True)
            self._render_path.parent.mkdir(parents=True, exist_ok=True)

            items: list[KnowledgeItem] = []
            next_id = 1
            if self._json_path.exists():
                try:
                    with open(self._json_path, encoding="utf-8") as file:
                        data = json.load(file)
                    items = [KnowledgeItem(**item) for item in data.get("items", [])]
                    next_id = max((item.id for item in items), default=0) + 1
                except Exception:
                    logger.exception("Failed to load knowledge base, using an empty store")

            self._items = items
            self._next_id = next_id
            self._render_cache = self._build_render_text()

            if not self._json_path.exists():
                self._save_json()
                self._save_render()
            elif not self._render_path.exists():
                self._save_render()

    def save(self) -> None:
        with self._lock:
            self._render_cache = self._build_render_text()
            self._save_json()
            self._save_render()

    def _save_json(self) -> None:
        tmp_path = self._json_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(
                    {"items": [item.to_dict() for item in self._items]},
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
            tmp_path.replace(self._json_path)
        except Exception:
            logger.exception("Failed to save knowledge base JSON")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _save_render(self) -> None:
        try:
            with open(self._render_path, "w", encoding="utf-8") as file:
                file.write(self._render_cache)
        except Exception:
            logger.exception("Failed to save knowledge base markdown snapshot")

    def _build_render_text(self) -> str:
        lines = [
            "# 小柚知识库全文",
            "",
            f"_最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
            "",
        ]

        if not self._items:
            lines.append("当前知识库为空。")
            return "\n".join(lines) + "\n"

        for item in self._items:
            tags = ", ".join(item.tags) if item.tags else "无"
            lines.extend(
                [
                    f"## #{item.id} {item.title}",
                    f"- 分类: {item.category}",
                    f"- 标签: {tags}",
                    f"- 更新人: {item.updated_by}",
                    f"- 更新时间: {item.updated_at}",
                    "",
                    item.content.strip(),
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def add_item(
        self,
        *,
        title: str,
        category: str,
        content: str,
        tags: list[str],
        updated_by: str,
    ) -> KnowledgeItem:
        with self._lock:
            now = datetime.now().isoformat(timespec="seconds")
            item = KnowledgeItem(
                id=self._next_id,
                title=title.strip(),
                category=category.strip(),
                content=content.strip(),
                tags=[tag.strip() for tag in tags if tag.strip()],
                updated_at=now,
                updated_by=updated_by.strip() or "web_console",
            )
            self._items.append(item)
            self._next_id += 1
            self.save()
            return item

    def update_item(
        self,
        item_id: int,
        *,
        title: str,
        category: str,
        content: str,
        tags: list[str],
        updated_by: str,
    ) -> KnowledgeItem | None:
        with self._lock:
            item = self.get_item(item_id)
            if not item:
                return None

            item.title = title.strip()
            item.category = category.strip()
            item.content = content.strip()
            item.tags = [tag.strip() for tag in tags if tag.strip()]
            item.updated_at = datetime.now().isoformat(timespec="seconds")
            item.updated_by = updated_by.strip() or "web_console"
            self.save()
            return item

    def delete_item(self, item_id: int) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.id != item_id]
            if len(self._items) == before:
                return False
            self.save()
            return True

    def get_item(self, item_id: int) -> KnowledgeItem | None:
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    return item
        return None

    def list_items(self) -> list[KnowledgeItem]:
        with self._lock:
            return [KnowledgeItem(**item.to_dict()) for item in self._items]

    def render_text(self) -> str:
        with self._lock:
            return self._render_cache

    def search(self, query: str, limit: int = 5) -> list[KnowledgeItem]:
        """Return the most relevant small set of entries for an agent turn.

        This intentionally stays dependency-free.  It combines exact phrase
        matching with Latin word and Chinese character overlap, which is a much
        safer prompt budget than sending the entire Markdown knowledge base.
        """
        normalized = self._normalize_search_text(query)
        if not normalized:
            return []
        tokens = self._search_tokens(normalized)
        scored: list[tuple[int, KnowledgeItem]] = []
        with self._lock:
            for item in self._items:
                title = self._normalize_search_text(item.title)
                tags = self._normalize_search_text(" ".join(item.tags))
                content = self._normalize_search_text(item.content)
                score = 0
                if normalized in title:
                    score += 28
                if normalized in tags:
                    score += 16
                if normalized in content:
                    score += 10
                for token in tokens:
                    if token in title:
                        score += 8
                    if token in tags:
                        score += 5
                    if token in content:
                        score += 2
                if score:
                    scored.append((score, item))

        scored.sort(key=lambda value: (-value[0], -value[1].id))
        return [KnowledgeItem(**item.to_dict()) for _, item in scored[:max(1, limit)]]

    def format_search_results(self, query: str, limit: int = 5) -> str:
        items = self.search(query, limit=limit)
        if not items:
            return "本地知识库没有检索到相关条目。"
        lines = [f"本地知识库命中 {len(items)} 条："]
        for item in items:
            content = re.sub(r"\s+", " ", item.content).strip()
            lines.extend(
                [
                    f"[#{item.id}] {item.title}（{item.category}）",
                    f"标签：{', '.join(item.tags) or '无'}",
                    content[:1200],
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def json_path(self) -> Path:
        return self._json_path

    @property
    def render_path(self) -> Path:
        return self._render_path

    @staticmethod
    def _normalize_search_text(value: str) -> str:
        return re.sub(r"\s+", "", value.lower().strip())

    @staticmethod
    def _search_tokens(value: str) -> list[str]:
        latin = re.findall(r"[a-z0-9_.-]{2,}", value)
        chinese_bigrams = [value[index : index + 2] for index in range(len(value) - 1)]
        tokens = [*latin, *chinese_bigrams]
        # Preserve order while suppressing one-character noise and duplicates.
        return list(dict.fromkeys(token for token in tokens if len(token) >= 2))[:30]
