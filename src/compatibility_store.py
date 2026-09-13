"""游戏兼容性报告的本地存储：JSON 结构化保存 + Markdown 人工可读快照。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("compatibility_store")

CompatStatus = Literal["works", "issues", "unknown"]
ReportState = Literal["published", "failed", "declined"]

STATUS_LABELS: dict[str, str] = {
    "works": "运行正常",
    "issues": "运行异常",
    "unknown": "待确认",
}


@dataclass
class CompatibilityReport:
    id: int
    created_at: str
    updated_at: str
    game: str
    status: CompatStatus
    summary: str
    source_text: str
    group_id: str
    user_id: str
    reporter_name: str = ""
    game_version: str = ""
    emulator: str = ""
    app_version: str = ""
    device: str = ""
    system_version: str = ""
    driver: str = ""
    settings: str = ""
    # 追问环节用户补充的文字说明
    extra_text: str = ""
    confidence: float = 0.0
    reason: str = ""
    state: ReportState = "published"
    media: list[dict[str, Any]] = field(default_factory=list)
    github_issue_number: int | None = None
    github_issue_url: str = ""
    github_submit_error: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)


class CompatibilityStore:
    def __init__(self, json_path: str | Path, md_path: str | Path) -> None:
        self._json_path = Path(json_path)
        self._md_path = Path(md_path)
        self._reports: list[CompatibilityReport] = []
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
            self._reports = [
                CompatibilityReport(**item) for item in data.get("reports", [])
            ]
            self._next_id = max((r.id for r in self._reports), default=0) + 1
            logger.info("加载兼容性报告 %s 条", len(self._reports))
        except Exception:
            logger.exception("加载兼容性报告失败，使用空数据")
            self._reports = []
            self._next_id = 1

    def save(self) -> None:
        self._save_json()
        self._save_md()

    def _save_json(self) -> None:
        tmp_path = self._json_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(
                    {"reports": [asdict(report) for report in self._reports]},
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
            tmp_path.replace(self._json_path)
        except Exception:
            logger.exception("保存兼容性报告 JSON 失败")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _save_md(self) -> None:
        lines: list[str] = [
            "# 游戏兼容性报告",
            "",
            f"_最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
            "",
        ]
        if not self._reports:
            lines.append("_暂无记录。_")
        for report in self._reports:
            lines.extend(self._report_lines(report))
        try:
            with open(self._md_path, "w", encoding="utf-8") as file:
                file.write("\n".join(lines).rstrip() + "\n")
        except Exception:
            logger.exception("保存兼容性报告 Markdown 失败")

    def _report_lines(self, report: CompatibilityReport) -> list[str]:
        lines = [
            f"### #{report.id} [{report.status_label}] {report.game or '未知游戏'}",
            f"- 摘要: {report.summary}",
            f"- 游戏: {report.game or '未知'}",
            f"- 游戏版本: {report.game_version or '不明'}",
            f"- 模拟器/运行方式: {report.emulator or '不明'}",
            f"- 小柚 App 版本: {report.app_version or '最新版'}",
        ]
        for key, label in (
            ("device", "设备"),
            ("system_version", "系统版本"),
            ("driver", "驱动/转译"),
            ("settings", "关键设置"),
        ):
            value = str(getattr(report, key, "")).strip()
            if value:
                lines.append(f"- {label}: {value}")
        lines.extend(
            [
                f"- 报告人: {report.reporter_name or report.user_id}",
                f"- 群组: {report.group_id or 'private'}",
                f"- 时间: {report.created_at}",
                f"- 状态: {report.state}",
            ]
        )
        if report.extra_text:
            lines.append(f"- 补充说明: {report.extra_text}")
        if report.media:
            saved = sum(1 for m in report.media if m.get("status") == "saved")
            lines.append(f"- 附件: 已本地保存 {saved}/{len(report.media)} 个")
        if report.github_issue_url:
            lines.append(f"- GitHub Issue: {report.github_issue_url}")
        elif report.github_submit_error:
            lines.append(f"- GitHub 提交: {report.github_submit_error}")
        lines.extend(["- 原文:", "", report.source_text.strip() or "(空)", ""])
        return lines

    def add_report(
        self,
        *,
        game: str,
        status: CompatStatus,
        summary: str,
        source_text: str,
        group_id: str,
        user_id: str,
        reporter_name: str = "",
        game_version: str = "",
        emulator: str = "",
        app_version: str = "",
        device: str = "",
        system_version: str = "",
        driver: str = "",
        settings: str = "",
        extra_text: str = "",
        confidence: float = 0.0,
        reason: str = "",
        state: ReportState = "published",
    ) -> CompatibilityReport:
        now = datetime.now().isoformat(timespec="seconds")
        report = CompatibilityReport(
            id=self._next_id,
            created_at=now,
            updated_at=now,
            game=game.strip()[:80],
            status=status,
            summary=summary.strip()[:200] or source_text.strip()[:100],
            source_text=source_text,
            group_id=group_id,
            user_id=user_id,
            reporter_name=reporter_name.strip()[:80],
            game_version=game_version.strip()[:80],
            emulator=emulator.strip()[:80],
            app_version=app_version.strip()[:80],
            device=device.strip()[:80],
            system_version=system_version.strip()[:80],
            driver=driver.strip()[:80],
            settings=settings.strip()[:200],
            extra_text=extra_text.strip()[:1000],
            confidence=confidence,
            reason=reason.strip()[:300],
            state=state,
        )
        self._reports.append(report)
        self._next_id += 1
        self.save()
        logger.info(
            "新增兼容性报告 #%s [%s] %s",
            report.id,
            report.status,
            report.game or "未知游戏",
        )
        return report

    def update_report(self, report_id: int, **changes: Any) -> CompatibilityReport | None:
        report = self.get_report(report_id)
        if not report:
            return None
        for key, value in changes.items():
            if hasattr(report, key):
                setattr(report, key, value)
        report.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()
        return report

    def set_media(self, report_id: int, media: list[dict[str, Any]]) -> CompatibilityReport | None:
        return self.update_report(report_id, media=media)

    def set_github_submission(
        self,
        report_id: int,
        issue_number: int | None = None,
        issue_url: str = "",
        error: str = "",
    ) -> CompatibilityReport | None:
        report = self.get_report(report_id)
        if not report:
            return None
        report.github_issue_number = issue_number
        report.github_issue_url = issue_url
        report.github_submit_error = error[:500]
        report.state = "published" if issue_url else "failed"
        report.updated_at = datetime.now().isoformat(timespec="seconds")
        self.save()
        return report

    def get_report(self, report_id: int) -> CompatibilityReport | None:
        for report in self._reports:
            if report.id == report_id:
                return report
        return None

    def get_recent(self, limit: int = 10) -> list[CompatibilityReport]:
        return list(reversed(self._reports))[: max(1, limit)]

    @property
    def size(self) -> int:
        return len(self._reports)
