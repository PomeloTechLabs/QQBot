from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("feedback_store")

ItemType = Literal["bug", "feature", "feedback", "todo"]
ItemStatus = Literal["pending", "done"]

_TYPE_LABELS: dict[str, str] = {
    "bug": "BUG",
    "feature": "需求",
    "feedback": "反馈",
    "todo": "待办",
}


@dataclass
class FeedbackItem:
    id: int
    type: ItemType
    content: str
    source_text: str
    user_id: str
    group_id: str
    status: ItemStatus
    created_at: str
    updated_at: str
    reporter_name: str = ""
    product_related: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    media: list[dict[str, Any]] = field(default_factory=list)
    github_issue_number: int | None = None
    github_issue_url: str = ""
    github_submit_error: str = ""


class FeedbackStore:
    def __init__(self, json_path: str | Path, md_path: str | Path) -> None:
        self._json_path = Path(json_path)
        self._md_path = Path(md_path)
        self._items: list[FeedbackItem] = []
        self._next_id = 1
        self._load()

    def _load(self) -> None:
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        self._md_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._json_path.exists():
            return

        try:
            with open(self._json_path, encoding="utf-8") as file:
                data = json.load(file)
            self._items = [FeedbackItem(**item) for item in data.get("items", [])]
            self._next_id = max((item.id for item in self._items), default=0) + 1
            logger.info("加载反馈记录 %s 条", len(self._items))
        except Exception:
            logger.exception("加载反馈记录失败，使用空数据")
            self._items = []
            self._next_id = 1

    def save(self) -> None:
        self._save_json()
        self._save_md()

    def _save_json(self) -> None:
        tmp_path = self._json_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(
                    {"items": [asdict(item) for item in self._items]},
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
            tmp_path.replace(self._json_path)
        except Exception:
            logger.exception("保存 feedback JSON 失败")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _save_md(self) -> None:
        lines: list[str] = [
            "# 用户反馈记录",
            "",
            f"_最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
            "",
        ]

        pending = [item for item in self._items if item.status == "pending"]
        done = [item for item in self._items if item.status == "done"]

        if pending:
            lines.extend(["## 待处理", ""])
            for item in pending:
                lines.extend(self._item_lines(item))

        if done:
            lines.extend(["## 已完成", ""])
            for item in done:
                lines.extend(self._item_lines(item))

        if not pending and not done:
            lines.append("_暂无记录。_")

        try:
            with open(self._md_path, "w", encoding="utf-8") as file:
                file.write("\n".join(lines).rstrip() + "\n")
        except Exception:
            logger.exception("保存 feedback Markdown 失败")

    def _item_lines(self, item: FeedbackItem) -> list[str]:
        label = _TYPE_LABELS.get(item.type, item.type)
        lines = [
            f"### #{item.id} {label}",
            f"- 摘要: {item.content}",
            f"- 用户: {item.user_id}",
            *([f"- 群昵称: {item.reporter_name}"] if item.reporter_name else []),
            f"- 群组: {item.group_id or 'private'}",
            f"- 状态: {'已完成' if item.status == 'done' else '待处理'}",
            f"- 创建: {item.created_at}",
        ]
        if item.status == "done":
            lines.append(f"- 更新: {item.updated_at}")
        if item.details:
            for key, label in (
                ("product", "产品"),
                ("app_version", "App 版本"),
                ("device", "设备"),
                ("system_version", "系统版本"),
                ("environment", "环境"),
                ("reproduction_steps", "复现步骤"),
                ("expected", "预期结果"),
                ("actual", "实际结果"),
            ):
                value = str(item.details.get(key, "")).strip()
                if value:
                    lines.append(f"- {label}: {value}")
        if item.media:
            saved = sum(1 for media in item.media if media.get("status") == "saved")
            lines.append(f"- 附件: 已本地保存 {saved}/{len(item.media)} 个")
        if item.github_issue_url:
            lines.append(f"- GitHub Issue: {item.github_issue_url}")
        elif item.github_submit_error:
            lines.append(f"- GitHub 提交状态: {item.github_submit_error}")
        lines.extend(
            [
                "- 原文:",
                "",
                item.source_text.strip() or "(空)",
                "",
            ]
        )
        return lines

    def add_item(
        self,
        type_: ItemType,
        content: str,
        source_text: str,
        user_id: str,
        group_id: str,
        reporter_name: str = "",
        product_related: bool = False,
        details: dict[str, Any] | None = None,
    ) -> FeedbackItem:
        now = datetime.now().isoformat(timespec="seconds")
        item = FeedbackItem(
            id=self._next_id,
            type=type_,
            content=content,
            source_text=source_text,
            user_id=user_id,
            group_id=group_id,
            status="pending",
            created_at=now,
            updated_at=now,
            reporter_name=reporter_name.strip()[:80],
            product_related=product_related,
            details=details or {},
        )
        self._items.append(item)
        self._next_id += 1
        self.save()
        logger.info("新增反馈记录 #%s [%s]: %s", item.id, item.type, item.content[:60])
        return item

    def update_media(self, item_id: int, media: list[dict[str, Any]]) -> FeedbackItem | None:
        item = self.get_item(item_id)
        if not item:
            return None
        item.media = media
        item.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()
        return item

    def set_github_submission(
        self,
        item_id: int,
        issue_number: int | None = None,
        issue_url: str = "",
        error: str = "",
    ) -> FeedbackItem | None:
        item = self.get_item(item_id)
        if not item:
            return None
        item.github_issue_number = issue_number
        item.github_issue_url = issue_url
        item.github_submit_error = error[:500]
        item.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()
        return item

    def get_pending(self, group_id: str | None = None) -> list[FeedbackItem]:
        items = [item for item in self._items if item.status == "pending"]
        if group_id is not None:
            items = [item for item in items if item.group_id == group_id]
        return items

    def get_all(self, group_id: str | None = None) -> list[FeedbackItem]:
        items = list(self._items)
        if group_id is not None:
            items = [item for item in items if item.group_id == group_id]
        return items

    def get_item(self, item_id: int) -> FeedbackItem | None:
        for item in self._items:
            if item.id == item_id:
                return item
        return None

    def mark_done(self, item_id: int) -> FeedbackItem | None:
        for item in self._items:
            if item.id == item_id:
                item.status = "done"
                item.updated_at = datetime.now().isoformat(timespec="seconds")
                self.save()
                logger.info("标记反馈记录 #%s 已完成", item_id)
                return item
        return None

    def delete(self, item_id: int) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != item_id]
        if len(self._items) == before:
            return False
        self.save()
        logger.info("删除反馈记录 #%s", item_id)
        return True

    def clear_done(self) -> int:
        before = len(self._items)
        self._items = [item for item in self._items if item.status != "done"]
        count = before - len(self._items)
        if count:
            self.save()
            logger.info("批量清除 %s 条已完成反馈", count)
        return count

    @property
    def size(self) -> int:
        return len(self._items)
