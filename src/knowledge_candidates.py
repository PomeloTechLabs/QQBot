from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .knowledge_store import KnowledgeItem, KnowledgeStore
from .skill_library import OperationalSkillLibrary

logger = logging.getLogger("knowledge_candidates")


@dataclass
class KnowledgeCandidate:
    id: int
    title: str
    category: str
    content: str
    tags: list[str]
    confidence: float
    evidence: str
    status: str
    created_at: str
    reviewed_at: str = ""
    reviewed_by: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class KnowledgeCandidateStore:
    """An auditable buffer between group chat and the formal knowledge base."""

    def __init__(
        self,
        path: str | Path,
        skill_library_file: str | Path | None = None,
    ) -> None:
        self._path = Path(path)
        self._skill_library = OperationalSkillLibrary(
            skill_library_file or self._path.parent / "operational_skills.md"
        )
        self._items: list[KnowledgeCandidate] = []
        self._next_id = 1
        self._lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.exists():
                self._save()
                return
            try:
                with open(self._path, encoding="utf-8") as file:
                    raw = json.load(file)
                self._items = [KnowledgeCandidate(**item) for item in raw.get("items", [])]
                self._next_id = max((item.id for item in self._items), default=0) + 1
            except Exception:
                logger.exception("知识候选库读取失败，将使用空库")
                self._items = []
                self._next_id = 1

    def add(
        self,
        *,
        title: str,
        category: str,
        content: str,
        tags: list[str],
        confidence: float,
        evidence: str,
    ) -> KnowledgeCandidate | None:
        title = title.strip()[:100]
        category = category.strip()[:40]
        content = content.strip()[:2000]
        if not title or not category or not content:
            return None
        with self._lock:
            fingerprint = self._fingerprint(title, content)
            if any(self._fingerprint(item.title, item.content) == fingerprint for item in self._items):
                return None
            item = KnowledgeCandidate(
                id=self._next_id,
                title=title,
                category=category,
                content=content,
                tags=list(dict.fromkeys(tag.strip()[:30] for tag in tags if tag.strip()))[:6],
                confidence=min(max(float(confidence), 0.0), 1.0),
                evidence=evidence.strip()[:3000],
                status="pending",
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._items.append(item)
            self._next_id += 1
            self._save()
            return KnowledgeCandidate(**item.to_dict())

    def list(self, status: str | None = None) -> list[KnowledgeCandidate]:
        with self._lock:
            items = [item for item in self._items if status is None or item.status == status]
            return [KnowledgeCandidate(**item.to_dict()) for item in items]

    def get(self, candidate_id: int) -> KnowledgeCandidate | None:
        with self._lock:
            for item in self._items:
                if item.id == candidate_id:
                    return KnowledgeCandidate(**item.to_dict())
        return None

    def approve(
        self,
        candidate_id: int,
        store: KnowledgeStore,
        reviewer: str,
    ) -> KnowledgeItem | None:
        with self._lock:
            item = self._find(candidate_id)
            if not item or item.status != "pending":
                return None
            saved = store.add_item(
                title=item.title,
                category=item.category,
                content=item.content,
                tags=item.tags,
                updated_by=f"群聊候选#{item.id} / {reviewer}",
            )
            item.status = "published"
            item.reviewed_by = reviewer
            item.reviewed_at = datetime.now().isoformat(timespec="seconds")
            self._save()
            self._skill_library.sync(store)
            return saved

    def sync_skill_library(self, store: KnowledgeStore) -> None:
        self._skill_library.sync(store)

    @property
    def skill_library_path(self) -> Path:
        return self._skill_library.path

    def reject(self, candidate_id: int, reviewer: str) -> bool:
        with self._lock:
            item = self._find(candidate_id)
            if not item or item.status != "pending":
                return False
            item.status = "rejected"
            item.reviewed_by = reviewer
            item.reviewed_at = datetime.now().isoformat(timespec="seconds")
            self._save()
            return True

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def pending_size(self) -> int:
        with self._lock:
            return sum(item.status == "pending" for item in self._items)

    def _find(self, candidate_id: int) -> KnowledgeCandidate | None:
        return next((item for item in self._items if item.id == candidate_id), None)

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as file:
                json.dump({"items": [item.to_dict() for item in self._items]}, file, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except Exception:
            logger.exception("知识候选库保存失败")
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _fingerprint(title: str, content: str) -> str:
        return "".join((title + content).lower().split())[:500]
