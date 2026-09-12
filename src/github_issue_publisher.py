"""Minimal GitHub Issues publisher for structured, locally recorded bug reports."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from .config import GitHubIssuesConfig

if TYPE_CHECKING:
    from .feedback_store import FeedbackItem


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")


@dataclass(frozen=True)
class GitHubIssueResult:
    submitted: bool
    number: int | None = None
    url: str = ""
    error: str = ""


class GitHubIssuePublisher:
    """Create GitHub Issues for classified bugs and opted-in feature requests."""

    def __init__(self, config: GitHubIssuesConfig) -> None:
        self._config = config

    async def submit_bug(self, item: "FeedbackItem") -> GitHubIssueResult:
        if item.type != "bug":
            return GitHubIssueResult(False, error="该记录不是 Bug，不能走 Bug 提交通道")
        return await self._submit(item)

    async def submit_feedback(self, item: "FeedbackItem") -> GitHubIssueResult:
        if item.type == "bug":
            return await self._submit(item)
        if item.type == "feature" and self._config.submit_feature_requests:
            return await self._submit(item)
        return GitHubIssueResult(False, error="该反馈类型未启用 GitHub 自动提交")

    async def _submit(self, item: "FeedbackItem") -> GitHubIssueResult:
        if not item.product_related:
            return GitHubIssueResult(False, error="该记录未确认与旧柚系列 App 有关")
        if not self._config.enabled:
            return GitHubIssueResult(False, error="GitHub 自动提交已关闭")
        if not self._config.repository or "/" not in self._config.repository:
            return GitHubIssueResult(False, error="GitHub 仓库配置无效")
        token = self._resolve_token()
        if not token:
            return GitHubIssueResult(
                False,
                error="未配置 GitHub 令牌（github_issues.token 或环境变量）",
            )

        endpoint = (
            f"{self._config.api_base_url.rstrip('/')}/repos/"
            f"{self._config.repository}/issues"
        )
        label = "BUG" if item.type == "bug" else "FEATURE"
        payload = {
            "title": f"[{label}] {item.content[:110]}",
            "body": self._build_issue_body(item),
        }
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
        except Exception as exc:
            return GitHubIssueResult(False, error=f"GitHub 请求失败：{exc}")
        if response.status_code != 201:
            return GitHubIssueResult(
                False,
                error=f"GitHub 创建 Issue 失败（HTTP {response.status_code}）：{response.text[:240]}",
            )
        try:
            data = response.json()
            number = int(data.get("number"))
            url = str(data.get("html_url", ""))
        except (TypeError, ValueError):
            return GitHubIssueResult(False, error="GitHub 返回的 Issue 数据无效")
        return GitHubIssueResult(True, number=number, url=url)

    def _resolve_token(self) -> str:
        """Prefer the local config value; preserve the environment fallback."""
        # Neither source is logged or returned by the WebUI.
        return self._config.token.strip() or os.environ.get(self._config.token_env, "").strip()

    def _build_issue_body(self, item: "FeedbackItem") -> str:
        details = item.details if isinstance(item.details, dict) else {}
        type_label = "Bug" if item.type == "bug" else "功能建议"
        rows = ["## 机器人整理", f"- 类型：{type_label}", f"- 摘要：{item.content}"]
        if self._config.include_reporter_name and item.reporter_name:
            rows.extend(["", "## 提出者", f"- 群昵称：{self._safe_inline(item.reporter_name)}"])
        detail_labels = {
            "product": "产品",
            "app_version": "App 版本",
            "device": "设备",
            "system_version": "系统版本",
            "environment": "环境",
            "reproduction_steps": "复现步骤",
            "expected": "预期结果",
            "actual": "实际结果",
        }
        for key, label in detail_labels.items():
            value = str(details.get(key, "")).strip()
            if value:
                rows.append(f"- {label}：{value}")
        rows.extend(
            [
                "",
                "## 用户原始描述（已脱敏）",
                "> " + GitHubIssuePublisher._redact(item.source_text).replace("\n", "\n> "),
                "",
                "## 附件",
                f"- 已在机器人工作目录本地保存 {sum(1 for media in item.media if media.get('status') == 'saved')} 个附件。",
            ]
        )
        if self._config.include_media_links:
            image_index = 0
            video_index = 0
            for media in item.media:
                source_url = str(media.get("source_url", "")).strip()
                if not source_url.startswith(("http://", "https://")):
                    continue
                safe_url = source_url.replace(">", "%3E")
                if media.get("kind") == "image":
                    image_index += 1
                    rows.append(f"![用户图片 {image_index}](<{safe_url}>)")
                elif media.get("kind") == "video":
                    video_index += 1
                    rows.append(f"- [用户视频 {video_index}](<{safe_url}>)")
            rows.append("- 图片/视频通过 QQ CDN 链接提供，链接可能过期；本地仍保留原始证据。")
        else:
            rows.append("- 附件仅保存在机器人工作目录，未同步外部链接。")
        return "\n".join(rows)

    @staticmethod
    def _safe_inline(value: str) -> str:
        return re.sub(r"[\r\n]+", " ", value).strip()[:80]

    @staticmethod
    def _redact(value: str) -> str:
        compact = _EMAIL_RE.sub("[已隐藏邮箱]", value.strip())
        compact = _LONG_NUMBER_RE.sub("[已隐藏编号]", compact)
        return compact[:1600] or "（用户未提供文字描述）"
