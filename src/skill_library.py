from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .knowledge_store import KnowledgeStore

logger = logging.getLogger("skill_library")


class OperationalSkillLibrary:
    """Renders approved operational playbooks into a compact local file.

    It intentionally mirrors only formal knowledge-base items in the exact
    ``操作技能`` category. Unreviewed chat or web content can therefore never
    rewrite the bot's core agent profile or its permissions.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def sync(self, knowledge_store: KnowledgeStore) -> None:
        skills = [
            item
            for item in knowledge_store.list_items()
            if item.category.strip() == "操作技能"
        ]
        lines = [
            "# 小柚已审批操作技能库",
            "",
            "仅包含已发布到正式知识库、分类为“操作技能”的可复用客服流程。",
            f"_同步时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
            "",
        ]
        if not skills:
            lines.append("当前没有已审批的操作技能。")
        for item in skills:
            lines.extend(
                [
                    f"## #{item.id} {item.title}",
                    f"- 标签: {', '.join(item.tags) or '无'}",
                    f"- 更新: {item.updated_at} / {item.updated_by}",
                    "",
                    item.content.strip(),
                    "",
                ]
            )
        content = "\n".join(lines).rstrip() + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(self._path)
        except Exception:
            logger.exception("操作技能库保存失败: %s", self._path)
            temporary.unlink(missing_ok=True)
