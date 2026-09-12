from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .feedback_store import FeedbackItem, FeedbackStore

logger = logging.getLogger("feedback_handler")

_ANALYSIS_SYSTEM_PROMPT = (
    "你现在处于 FEEDBACK_ANALYSIS_MODE。"
    "只输出一个 JSON 对象，不要输出其他文字。"
)

_ANALYSIS_PROMPT_TEMPLATE = """\
[FEEDBACK_ANALYSIS_REQUEST]
请判断下面这条消息是否值得记录为反馈问题。

=== 当前消息 ===
用户 {user_id}: {text}

=== 最近对话（供参考）===
{context_lines}

=== 用户提交的附件 ===
{media_summary}

=== 输出要求 ===
仅输出 JSON：
{{
  "is_product_related": true/false,
  "should_record": true/false,
  "type": "bug" | "feature" | "feedback" | "todo",
  "summary": "一句话摘要",
  "confidence": 0.0,
  "reason": "简短原因",
    "details": {{
    "product": "涉及的 App 或组件；未提供则留空",
    "app_version": "App 版本号；未提供则留空",
    "device": "设备型号；未提供则留空",
    "system_version": "系统版本；未提供则留空",
    "environment": "版本、设备、系统等；未提供则留空",
    "reproduction_steps": "用户明确描述的复现步骤；未提供则留空",
    "expected": "用户期望的结果；未提供则留空",
    "actual": "实际现象或报错；未提供则留空"
  }}
}}

记录标准：
- is_product_related=true：消息或最近上下文明确是在说旧柚、旧柚闪传、旧柚Pro，或其安装、导入、传输、解压、Wine、Z: 映射、游戏运行等功能。仅提到手机、系统、其他 App、游戏作品或泛泛故障时必须为 false。
- 只有 is_product_related=true 时才能 should_record=true；不能仅因出现“报错”“崩溃”“建议”等词就关联到旧柚系列。
- bug：明确的报错、崩溃、闪退、无法启动、兼容性异常、功能失效
- feature：明确的新功能诉求、改进建议
- feedback：明确的使用感受、吐槽、体验问题、产品意见
- todo：明确希望后续处理、跟进、补充、排查的事项
- should_record=false：纯闲聊、单纯问用法、普通咨询、寒暄、感谢、与产品无关内容

要求：
- summary 要简洁、具体、可读，尽量保留问题主体
- details 只能整理用户明确给出的事实，不得猜测版本、环境或复现步骤
- 如果只是普通问答，不要记录
- 如果不确定，但看起来像真实问题或需求，优先记录
"""

_TYPE_LABELS: dict[str, str] = {
    "bug": "BUG",
    "feature": "需求",
    "feedback": "反馈",
    "todo": "待办",
}


@dataclass
class FeedbackQuery:
    mode: Literal["summary", "records", "detail"]
    include_done: bool = False
    item_id: int | None = None


@dataclass
class _AnalysisResult:
    is_product_related: bool = False
    should_record: bool = False
    type: str = "feedback"
    summary: str = ""
    confidence: float = 0.0
    reason: str = ""
    details: dict[str, str] | None = None


class FeedbackHandler:
    def __init__(
        self,
        store: "FeedbackStore",
        llm: Any,
        clarify_timeout: int = 120,
        media_archiver: Any | None = None,
        issue_publisher: Any | None = None,
    ) -> None:
        self._store = store
        self._llm = llm
        self._clarify_timeout = clarify_timeout
        self._media_archiver = media_archiver
        self._issue_publisher = issue_publisher

    async def analyze_and_record(
        self,
        group_id: str,
        user_id: str,
        text: str,
        group_context: list[dict[str, str]],
        media: list[dict[str, str]] | None = None,
        reporter_name: str = "",
    ) -> "FeedbackItem | None":
        result = await self._call_analysis_llm(
            text=text,
            user_id=user_id,
            group_context=group_context,
            media=media or [],
        )
        if not result.is_product_related or not result.should_record:
            return None

        summary = result.summary.strip() or text.strip()[:100]
        item = self._store.add_item(
            type_=result.type,  # type: ignore[arg-type]
            content=summary,
            source_text=text,
            user_id=user_id,
            group_id=group_id,
            reporter_name=reporter_name,
            product_related=result.is_product_related,
            details=result.details or {},
        )
        if media and self._media_archiver:
            try:
                archived = await self._media_archiver.archive(item.id, media)
                self._store.update_media(item.id, archived)
            except Exception:
                logger.exception("保存反馈附件失败: #%s", item.id)
        if item.type in {"bug", "feature"} and self._issue_publisher:
            try:
                submitted = await self._issue_publisher.submit_feedback(item)
                self._store.set_github_submission(
                    item.id,
                    issue_number=submitted.number,
                    issue_url=submitted.url,
                    error=submitted.error,
                )
            except Exception:
                logger.exception("提交 GitHub Issue 失败: #%s", item.id)
                self._store.set_github_submission(item.id, error="提交 GitHub Issue 时发生内部异常")
        logger.info(
            "Automatically recorded feedback #%s [%s] confidence=%.2f reason=%s",
            item.id,
            item.type,
            result.confidence,
            result.reason,
        )
        return item

    @staticmethod
    def should_analyze(text: str, media: list[dict[str, str]], *, is_private: bool) -> bool:
        """High-recall local gate so ordinary chat does not consume GPU inference."""
        compact = text.strip().lower()
        # A private message is intentionally sent to the bot. Run the compact,
        # no-thinking classifier for it even when it lacks an obvious keyword,
        # so reports phrased indirectly are not lost. Normal greetings still
        # receive should_record=false and are not written anywhere.
        if is_private:
            return bool(compact or media)
        if not compact:
            return False
        markers = (
            "bug", "报错", "闪退", "崩溃", "崩了", "无法", "不能", "失败", "异常",
            "卡死", "黑屏", "白屏", "打不开", "没反应", "不显示", "故障", "失效",
            "不兼容", "兼容性", "错误", "crash", "error", "反馈", "建议", "需求",
            "希望增加", "希望改进", "优化",
        )
        return any(marker in compact for marker in markers)

    def detect_query(self, text: str) -> FeedbackQuery | None:
        compact = re.sub(r"\s+", "", text.strip())
        if not compact:
            return None

        lowered = compact.lower()
        wants_all = "all" in lowered or any(flag in compact for flag in ("全部", "所有", "完整"))
        wants_pending = any(flag in compact for flag in ("待办", "待处理", "未处理"))

        wants_records = any(
            phrase in compact
            for phrase in (
                "反馈记录",
                "反馈详情",
                "反馈信息",
                "反馈原文",
                "问题记录",
                "待办记录",
                "记录列表",
                "记录详情",
            )
        ) or (
            any(flag in compact for flag in ("记录", "详情", "原文"))
            and any(flag in compact for flag in ("反馈", "待办", "问题"))
        )

        wants_summary = any(
            phrase in compact
            for phrase in (
                "查待办",
                "待办事项",
                "待办列表",
                "查反馈",
                "反馈列表",
                "反馈情况",
                "待办情况",
            )
        )

        item_id = self._extract_item_id(compact)
        if wants_records:
            if item_id is not None:
                return FeedbackQuery(mode="detail", include_done=True, item_id=item_id)
            include_done = wants_all or not wants_pending
            return FeedbackQuery(mode="records", include_done=include_done)

        if wants_summary:
            return FeedbackQuery(mode="summary", include_done=wants_all)

        return None

    def format_todo_list(
        self,
        group_id: str | None = None,
        include_done: bool = False,
    ) -> str:
        items = self._store.get_all(group_id) if include_done else self._store.get_pending(group_id)
        if not items:
            return "暂时没有符合条件的反馈记录。"

        title = (
            f"全部反馈摘要（共 {len(items)} 条）"
            if include_done
            else f"待处理反馈摘要（共 {len(items)} 条）"
        )
        lines = [title, ""]
        for item in items:
            label = _TYPE_LABELS.get(item.type, item.type)
            status = "已完成" if item.status == "done" else "待处理"
            lines.append(f"#{item.id} [{label}] {status}")
            lines.append(item.content)
            lines.append(f"by {item.user_id} @ {item.created_at[:10]}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def format_feedback_records(
        self,
        group_id: str | None = None,
        include_done: bool = True,
    ) -> str:
        items = self._store.get_all(group_id) if include_done else self._store.get_pending(group_id)
        if not items:
            return "暂时没有符合条件的反馈记录。"

        title = (
            f"全部反馈记录（共 {len(items)} 条）"
            if include_done
            else f"待处理反馈记录（共 {len(items)} 条）"
        )
        lines = [title, ""]
        for item in items:
            lines.extend(self._item_lines(item))
            lines.append("")
        return "\n".join(lines).rstrip()

    def format_feedback_detail(self, item_id: int, group_id: str | None = None) -> str:
        item = self._store.get_item(item_id)
        if not item or (group_id is not None and item.group_id != group_id):
            return f"未找到反馈记录 #{item_id}"
        return "\n".join(self._item_lines(item)).rstrip()

    @property
    def store(self) -> "FeedbackStore":
        return self._store

    def _item_lines(self, item: "FeedbackItem") -> list[str]:
        label = _TYPE_LABELS.get(item.type, item.type)
        status = "已完成" if item.status == "done" else "待处理"
        lines = [
            f"#{item.id} [{label}] {status}",
            f"摘要: {item.content}",
            f"用户: {item.reporter_name or item.user_id}",
            f"群组: {item.group_id or 'private'}",
            f"创建: {item.created_at}",
        ]
        if item.status == "done":
            lines.append(f"更新: {item.updated_at}")
        if item.media:
            saved = sum(1 for media in item.media if media.get("status") == "saved")
            lines.append(f"附件: 本地已保存 {saved}/{len(item.media)} 个")
        if item.github_issue_url:
            lines.append(f"GitHub Issue: {item.github_issue_url}")
        elif item.github_submit_error:
            lines.append(f"GitHub 提交: {item.github_submit_error}")
        lines.extend(["原文:", item.source_text.strip() or "(空)"])
        return lines

    async def _call_analysis_llm(
        self,
        text: str,
        user_id: str,
        group_context: list[dict[str, str]],
        media: list[dict[str, str]],
    ) -> _AnalysisResult:
        context_lines = "（无上下文）"
        if group_context:
            lines = [
                f"[群成员] {item['text'][:80]}"
                for item in group_context[-12:]
                if item.get("text")
            ]
            if lines:
                context_lines = "\n".join(lines)

        prompt = _ANALYSIS_PROMPT_TEMPLATE.format(
            user_id=user_id,
            text=text[:500],
            context_lines=context_lines,
            media_summary=self._media_summary(media),
        )

        try:
            raw = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_ANALYSIS_SYSTEM_PROMPT,
                user_id=user_id,
                temperature=0.0,
                max_tokens=300,
                think=False,
            )
        except Exception:
            logger.exception("Feedback analysis request failed")
            return _AnalysisResult()

        return self._parse_result(raw, text)

    @staticmethod
    def _parse_result(raw: str, fallback_text: str) -> _AnalysisResult:
        if not raw:
            return _AnalysisResult()

        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            logger.warning("Feedback analysis result missing JSON: %s", raw[:200])
            return _AnalysisResult()

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("Feedback analysis JSON parse failed: %s", match.group()[:200])
            return _AnalysisResult()

        item_type = str(data.get("type", "feedback")).strip().lower()
        if item_type not in {"bug", "feature", "feedback", "todo"}:
            item_type = "feedback"

        summary = str(data.get("summary", "")).strip() or fallback_text[:100]
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        details_raw = data.get("details") if isinstance(data.get("details"), dict) else {}
        details: dict[str, str] = {}
        for key in (
            "product",
            "app_version",
            "device",
            "system_version",
            "environment",
            "reproduction_steps",
            "expected",
            "actual",
        ):
            value = details_raw.get(key, "")
            if isinstance(value, list):
                value = "；".join(str(part).strip() for part in value if str(part).strip())
            value = str(value).strip()
            if value:
                details[key] = value[:500]

        return _AnalysisResult(
            is_product_related=bool(data.get("is_product_related", False)),
            should_record=bool(data.get("should_record", False)),
            type=item_type,
            summary=summary,
            confidence=min(max(confidence, 0.0), 1.0),
            reason=str(data.get("reason", "")).strip(),
            details=details,
        )

    @staticmethod
    def _media_summary(media: list[dict[str, str]]) -> str:
        images = sum(1 for item in media if item.get("kind") == "image")
        videos = sum(1 for item in media if item.get("kind") == "video")
        if not images and not videos:
            return "无附件"
        return f"图片 {images} 个，视频 {videos} 个（模型不可直接查看附件，只能依据文字判断）"

    @staticmethod
    def _extract_item_id(text: str) -> int | None:
        match = re.search(r"#(\d+)", text)
        if match:
            return int(match.group(1))
        match = re.search(r"(?:记录|反馈|问题|待办)(\d+)", text)
        if match:
            return int(match.group(1))
        return None
