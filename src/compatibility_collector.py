"""游戏兼容性自动收录：识别群友报告的游戏运行情况，补全缺失信息后发布到兼容性库。

设计原则（避免打扰用户）：
- 能自己判断的自己判断：App 版本未提供默认最新版；游戏版本未提供记"不明"；
  模拟器按游戏类型推断；信息完整时静默收录，不回复群消息。
- 只有"不知道是哪个游戏"或"无法判断最终能否运行"时，才向报告者追问一次；
  超时未回复按已掌握的信息直接收录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .compatibility_store import CompatibilityStore, CompatStatus

if TYPE_CHECKING:
    from .compatibility_store import CompatibilityReport
    from .config import CompatibilityConfig

logger = logging.getLogger("compatibility_collector")

_SYSTEM_PROMPT = (
    "你现在处于 GAME_COMPAT_ANALYSIS_MODE。"
    "只输出一个 JSON 对象，不要输出其他文字。"
)

_ANALYSIS_PROMPT_TEMPLATE = """\
[GAME_COMPAT_ANALYSIS_REQUEST]
请判断下面这条消息是否报告了某个游戏在手机/模拟器上的运行情况（能玩或不能玩都算），值得收录到游戏兼容性库。

=== 触发方式 ===
{trigger_context}

=== 当前消息 ===
用户 {user_id}: {text}

=== 最近对话（供参考）===
{context_lines}

=== 用户提交的附件 ===
{media_summary}

=== 输出要求 ===
仅输出 JSON：
{{
  "is_game_compatibility": true/false,
  "jiuyou_related": true/false,
  "status": "works" | "issues" | "unknown",
  "game": "游戏名；未提供则留空",
  "game_version": "游戏版本号；未提供则留空",
  "emulator": "运行方式/模拟器（如 Winlator、Hokit、Mobox、Waydroid、ExaGear、旧柚等）；未提供则留空",
  "device": "设备型号；未提供则留空",
  "system_version": "系统版本；未提供则留空",
  "driver": "驱动/转译器（如 Turnip、DXVK、Vortek）；未提供则留空",
  "settings": "影响运行的关键设置；未提供则留空",
  "summary": "一句话摘要，保留游戏名和结论",
  "confidence": 0.0,
  "reason": "简短原因"
}}

收录标准：
- is_game_compatibility=true：消息在描述某款游戏实际跑起来的体验，正面（能玩、流畅、完美运行）和负面（玩不了、闪退、黑屏、进不去、报错、严重掉帧、贴图异常）都要收录
- jiuyou_related=true：消息或最近上下文明确与旧柚系列有关——提到旧柚、小柚、旧柚闪传、旧柚Pro，或在讨论通过旧柚跑这款游戏
- 普通群聊（未@机器人）时：只有 jiuyou_related=true 才允许 is_game_compatibility=true；讨论其他模拟器、其他工具或单纯聊游戏，一律 is_game_compatibility=false
- 用户@了机器人或私聊时：用户是在主动找机器人，描述游戏运行体验即可收录，不强制 jiuyou_related
- status=works：最终能正常玩；先出问题后来解决了也算 works，摘要里注明
- status=issues：最终玩不了或问题仍在
- status=unknown：报告了兼容性话题但无法判断最终结果
- is_game_compatibility=false：只是在提问（如“XX能玩吗”，还没有实际运行结果）、聊攻略剧情、求推荐游戏、泛泛聊手机性能
- game 填玩家通用的游戏名，不确定就留空，不要编造
- 其他字段只整理用户明确给出的事实，不要猜测

要求：
- summary 简洁、具体，例如“《赛博酒保》 Winlator 下 30 流畅运行”
- 如果不确定，但看起来像真实运行体验，优先收录
"""

_PC_GAME_HINTS = (
    "exe", "steam", "端游", "pc版", "pc游戏", "windows版", "硬盘版", "学习版", "galgame",
)
_ANDROID_GAME_HINTS = ("apk", "手游", "安卓版", "安卓游戏", "渠道服")
_DECLINE_MARKERS = ("不用了", "不用", "别问", "不要问", "算了", "取消", "跳过", "别收录")

_EMULATOR_TYPE_HINTS = {
    "winlator": "Winlator",
    "hokit": "Hokit",
    "mobox": "Mobox",
    "waydroid": "Waydroid",
    "exagear": "ExaGear",
    "mojo": "Mojo",
    "旧柚": "旧柚",
    "闪传": "旧柚闪传",
}


@dataclass
class _AnalysisResult:
    is_game_compatibility: bool = False
    jiuyou_related: bool = False
    status: CompatStatus = "unknown"
    game: str = ""
    game_version: str = ""
    emulator: str = ""
    device: str = ""
    system_version: str = ""
    driver: str = ""
    settings: str = ""
    summary: str = ""
    confidence: float = 0.0
    reason: str = ""


@dataclass
class _PendingClarify:
    scope_id: str
    user_id: str
    group_id: str
    question: str
    source_text: str
    group_context: list[dict[str, str]]
    media: list[dict[str, str]]
    reporter_name: str
    fields: dict[str, Any]
    task: asyncio.Task | None = field(default=None, repr=False)


class CompatibilityCollector:
    def __init__(
        self,
        store: CompatibilityStore,
        llm: Any,
        config: "CompatibilityConfig",
        media_archiver: Any | None = None,
        issue_publisher: Any | None = None,
        sender: Callable[[dict, str], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._llm = llm
        self._config = config
        self._media_archiver = media_archiver
        self._publisher = issue_publisher
        self._sender = sender
        self._pending: dict[tuple[str, str], _PendingClarify] = {}
        self._last_analysis: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------
    # 触发与主流程
    # ------------------------------------------------------------------

    @staticmethod
    def should_observe(text: str) -> bool:
        """高召回本地门槛：命中兼容性相关词才值得花一次轻量 JSON 推理。"""
        compact = text.strip().lower()
        markers = (
            "兼容", "能跑", "跑起来", "跑不起", "带得动", "带不动",
            "运行正常", "正常运行", "完美运行", "完美", "流畅", "卡顿", "掉帧",
            "帧数", "帧率", "可以玩", "能玩", "玩不了", "玩不成", "没法玩",
            "进不去", "进不了", "卡在", "黑屏", "白屏", "花屏", "闪退", "崩溃",
            "崩了", "报错", "贴图", "渲染", "模拟器", "转译",
            "winlator", "hokit", "mobox", "waydroid", "exagear", "mojo",
            "dxvk", "turnip", "vortek", "wine",
        )
        return any(marker in compact for marker in markers)

    async def observe(
        self,
        event: dict,
        group_id: str,
        user_id: str,
        text: str,
        group_context: list[dict[str, str]],
        media: list[dict[str, str]],
        reporter_name: str,
        *,
        directed: bool = False,
    ) -> None:
        """directed=True 表示用户@了机器人/昵称点名或私聊，此时才允许追问。"""
        scope_id = group_id or f"private:{user_id}"
        key = (scope_id, user_id)
        now = time.monotonic()
        last = self._last_analysis.get(key, 0.0)
        if now - last < self._config.analysis_cooldown:
            return
        self._last_analysis[key] = now

        result = await self._analyze(
            text=text,
            user_id=user_id,
            group_context=group_context,
            media=media,
            directed=directed,
        )
        if not result.is_game_compatibility:
            return
        # 未@机器人的普通群聊：只有明确与旧柚相关才记录，绝不打扰
        if not directed and not result.jiuyou_related:
            logger.info(
                "兼容性话题与旧柚无关，保持沉默 group=%s user=%s reason=%s",
                group_id or "-",
                user_id,
                result.reason[:80],
            )
            return

        report_fields = self._build_report_fields(result, text)
        needs_clarify = (not report_fields["game"]) or report_fields["status"] == "unknown"
        if needs_clarify:
            if directed and self._sender is not None:
                question = self._clarify_question(report_fields)
                await self._create_pending(
                    key=key,
                    question=question,
                    source_text=text,
                    group_context=group_context,
                    media=media,
                    reporter_name=reporter_name,
                    group_id=group_id,
                    fields=report_fields,
                )
                await self._sender(event, question)
                logger.info(
                    "兼容性收录需要追问 group=%s user=%s game=%r status=%s",
                    group_id or "-",
                    user_id,
                    report_fields["game"],
                    report_fields["status"],
                )
                return
            if not report_fields["game"]:
                # 不允许追问且缺游戏名：无法形成有效报告，保持沉默
                logger.info(
                    "兼容性报告缺少游戏名且未@机器人，跳过 user=%s", user_id
                )
                return
            # 游戏已知但结果不确定：静默按 unknown 记录，不打扰

        await self._finalize_report(
            group_id=group_id,
            user_id=user_id,
            reporter_name=reporter_name,
            source_text=text,
            extra_text="",
            media=media,
            fields=report_fields,
        )

    # ------------------------------------------------------------------
    # 追问环节
    # ------------------------------------------------------------------

    def has_pending_clarification(self, scope_id: str, user_id: str) -> bool:
        return (scope_id, user_id) in self._pending

    async def consume_clarification(
        self,
        scope_id: str,
        user_id: str,
        text: str,
        media: list[dict[str, str]] | None = None,
        reporter_name: str = "",
    ) -> str | None:
        """把用户对追问的回复并入报告；返回给用户的确认文本。"""
        key = (scope_id, user_id)
        pending = self._pending.pop(key, None)
        if not pending:
            return None
        if pending.task and not pending.task.done():
            pending.task.cancel()

        compact = text.strip()
        if compact and any(marker in compact.lower() for marker in _DECLINE_MARKERS):
            logger.info("兼容性收录被用户拒绝 user=%s", user_id)
            return "好的，这次就不收录了。"

        merged_text = (
            f"原始消息：{pending.source_text}\n"
            f"机器人追问：{pending.question}\n"
            f"用户补充：{compact or '（用户只发送了附件，没有文字）'}"
        )
        combined_media = list(pending.media) + list(media or [])
        result = await self._analyze(
            text=merged_text,
            user_id=user_id,
            group_context=pending.group_context,
            media=combined_media,
        )
        if not result.is_game_compatibility:
            return "好的，这次就不收录了。"

        fields = self._build_report_fields(result, merged_text)
        if not fields["game"]:
            fields["game"] = "未知游戏"
        report = await self._finalize_report(
            group_id=pending.group_id,
            user_id=user_id,
            reporter_name=reporter_name or pending.reporter_name,
            source_text=pending.source_text,
            extra_text=compact,
            media=combined_media,
            fields=fields,
            reason=result.reason,
        )
        if not report:
            return "已记录，但这次没能发布到兼容性库，管理员会稍后处理。"
        if report.github_issue_url:
            return f"已收录《{report.game}》的兼容性记录：{report.github_issue_url}\n感谢反馈！"
        return f"已记录《{report.game}》的兼容性情况，感谢反馈！"

    async def _create_pending(
        self,
        *,
        key: tuple[str, str],
        question: str,
        source_text: str,
        group_context: list[dict[str, str]],
        media: list[dict[str, str]],
        reporter_name: str,
        group_id: str,
        fields: dict[str, Any],
    ) -> None:
        scope_id, user_id = key
        pending = _PendingClarify(
            scope_id=scope_id,
            user_id=user_id,
            group_id=group_id,
            question=question,
            source_text=source_text,
            group_context=group_context,
            media=media,
            reporter_name=reporter_name,
            fields=fields,
        )
        pending.task = asyncio.create_task(self._expire_pending(key))
        old = self._pending.get(key)
        if old and old.task and not old.task.done():
            old.task.cancel()
        self._pending[key] = pending

    async def _expire_pending(self, key: tuple[str, str]) -> None:
        try:
            await asyncio.sleep(self._config.clarify_timeout)
        except asyncio.CancelledError:
            return
        pending = self._pending.pop(key, None)
        if not pending:
            return
        logger.info(
            "兼容性追问超时，按已有信息收录 user=%s", pending.user_id
        )
        # 超时收录时保持追问前的判断结果，不凭空补游戏名
        await self._finalize_report(
            group_id=pending.group_id,
            user_id=pending.user_id,
            reporter_name=pending.reporter_name,
            source_text=pending.source_text,
            extra_text="",
            media=pending.media,
            fields=pending.fields,
        )

    def _clarify_question(self, fields: dict[str, Any]) -> str:
        game = fields.get("game", "")
        if not game:
            return (
                "我把你刚才说的游戏运行情况记到兼容性库里，方便其他玩家查。"
                "请问你说的是哪个游戏？最终是能正常玩还是有问题？"
                "（回复“不用了”我就跳过）"
            )
        return (
            f"我准备把《{game}》的运行情况记到兼容性库里。"
            "请问最终是能正常玩，还是一直有问题？"
            "（回复“不用了”我就跳过）"
        )

    # ------------------------------------------------------------------
    # 字段补全与发布
    # ------------------------------------------------------------------

    def _build_report_fields(self, result: _AnalysisResult, text: str) -> dict[str, Any]:
        status = result.status if result.status in {"works", "issues", "unknown"} else "unknown"
        game = result.game.strip()
        emulator = result.emulator.strip() or self._infer_emulator(game, text)
        return {
            "game": game,
            "status": status,
            "game_version": result.game_version.strip() or self._config.unknown_game_version,
            "emulator": emulator,
            "app_version": self._config.default_app_version,
            "device": result.device.strip(),
            "system_version": result.system_version.strip(),
            "driver": result.driver.strip(),
            "settings": result.settings.strip(),
            "summary": result.summary.strip(),
            "confidence": result.confidence,
        }

    @staticmethod
    def _infer_emulator(game: str, text: str) -> str:
        """按游戏类型推断运行方式；消息里直接提到的模拟器优先。"""
        combined = f"{game} {text}".lower()
        for marker, label in _EMULATOR_TYPE_HINTS.items():
            if marker in combined or marker in text:
                return f"{label}（报告者提及）"
        if any(hint in combined for hint in _PC_GAME_HINTS):
            return "Winlator/Hokit（按 PC 游戏推断）"
        if any(hint in combined for hint in _ANDROID_GAME_HINTS):
            return "Android 原生（按手游推断）"
        return ""

    async def _finalize_report(
        self,
        *,
        group_id: str,
        user_id: str,
        reporter_name: str,
        source_text: str,
        extra_text: str,
        media: list[dict[str, str]],
        fields: dict[str, Any],
        reason: str = "",
    ) -> "CompatibilityReport | None":
        report = self._store.add_report(
            game=fields.get("game", ""),
            status=fields.get("status", "unknown"),
            summary=fields.get("summary", ""),
            source_text=source_text,
            group_id=group_id,
            user_id=user_id,
            reporter_name=reporter_name,
            game_version=fields.get("game_version", ""),
            emulator=fields.get("emulator", ""),
            app_version=fields.get("app_version", ""),
            device=fields.get("device", ""),
            system_version=fields.get("system_version", ""),
            driver=fields.get("driver", ""),
            settings=fields.get("settings", ""),
            extra_text=extra_text,
            confidence=fields.get("confidence", 0.0),
            reason=reason,
        )
        if media and self._media_archiver:
            try:
                archived = await self._media_archiver.archive(report.id, media)
                self._store.set_media(report.id, archived)
            except Exception:
                logger.exception("保存兼容性附件失败: #%s", report.id)
        if self._publisher:
            try:
                ok, number, url, error = await self._publisher.submit(report)
                self._store.set_github_submission(report.id, issue_number=number, issue_url=url, error=error)
            except Exception:
                logger.exception("兼容性 Issue 提交失败: #%s", report.id)
                self._store.set_github_submission(report.id, error="提交兼容性 Issue 时发生内部异常")
        logger.info(
            "兼容性报告已收录 #%s [%s] game=%s",
            report.id,
            report.status,
            report.game or "未知",
        )
        return report

    # ------------------------------------------------------------------
    # LLM 分析
    # ------------------------------------------------------------------

    async def _analyze(
        self,
        *,
        text: str,
        user_id: str,
        group_context: list[dict[str, str]],
        media: list[dict[str, str]],
        directed: bool = False,
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

        trigger_context = (
            "用户@了机器人或私聊机器人，属于主动咨询"
            if directed
            else "普通群聊，用户没有@机器人"
        )
        prompt = _ANALYSIS_PROMPT_TEMPLATE.format(
            trigger_context=trigger_context,
            user_id=user_id,
            text=text[:600],
            context_lines=context_lines,
            media_summary=self._media_summary(media),
        )
        try:
            raw = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_SYSTEM_PROMPT,
                user_id=user_id,
                temperature=0.0,
                max_tokens=400,
                think=False,
            )
        except Exception:
            logger.exception("兼容性分析请求失败")
            return _AnalysisResult()
        return self._parse_result(raw, text)

    @staticmethod
    def _parse_result(raw: str, fallback_text: str) -> _AnalysisResult:
        if not raw:
            return _AnalysisResult()
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            logger.warning("兼容性分析结果缺少 JSON: %s", raw[:200])
            return _AnalysisResult()
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("兼容性分析 JSON 解析失败: %s", match.group()[:200])
            return _AnalysisResult()

        status = str(data.get("status", "unknown")).strip().lower()
        if status not in {"works", "issues", "unknown"}:
            status = "unknown"
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        def _field(key: str, limit: int = 200) -> str:
            value = data.get(key, "")
            if isinstance(value, list):
                value = "；".join(str(part).strip() for part in value if str(part).strip())
            return str(value).strip()[:limit]

        summary = _field("summary") or fallback_text.strip()[:100]
        return _AnalysisResult(
            is_game_compatibility=bool(data.get("is_game_compatibility", False)),
            jiuyou_related=bool(data.get("jiuyou_related", False)),
            status=status,  # type: ignore[arg-type]
            game=_field("game", 80),
            game_version=_field("game_version", 80),
            emulator=_field("emulator", 80),
            device=_field("device", 80),
            system_version=_field("system_version", 80),
            driver=_field("driver", 80),
            settings=_field("settings"),
            summary=summary,
            confidence=min(max(confidence, 0.0), 1.0),
            reason=_field("reason", 300),
        )

    @staticmethod
    def _media_summary(media: list[dict[str, str]]) -> str:
        images = sum(1 for item in media if item.get("kind") == "image")
        videos = sum(1 for item in media if item.get("kind") == "video")
        if not images and not videos:
            return "无附件"
        return f"图片 {images} 个，视频 {videos} 个（模型不可直接查看附件，只能依据文字判断）"

    # ------------------------------------------------------------------
    # 管理员查询
    # ------------------------------------------------------------------

    def format_recent(self, limit: int = 10) -> str:
        reports = self._store.get_recent(limit)
        if not reports:
            return "暂时没有兼容性报告记录。"
        lines = [f"最近的兼容性报告（共 {self._store.size} 条，显示 {len(reports)} 条）：", ""]
        for report in reports:
            issue = (
                f"Issue: {report.github_issue_url}"
                if report.github_issue_url
                else (f"提交失败: {report.github_submit_error}" if report.github_submit_error else "未提交")
            )
            lines.append(
                f"#{report.id} [{report.status_label}] 《{report.game or '未知'}》"
                f" {report.emulator or '运行方式不明'} by {report.reporter_name or report.user_id}"
                f" @ {report.created_at[:10]}"
            )
            lines.append(f"  {report.summary}")
            lines.append(f"  {issue}")
        return "\n".join(lines).rstrip()

    def format_report_detail(self, report_id: int) -> str:
        report = self._store.get_report(report_id)
        if not report:
            return f"未找到兼容性报告 #{report_id}"
        lines = [
            f"#{report.id} [{report.status_label}] {report.state}",
            f"游戏: 《{report.game or '未知'}》 版本: {report.game_version or '不明'}",
            f"运行方式: {report.emulator or '不明'} App: {report.app_version or '最新版'}",
            f"设备: {report.device or '不明'} 系统: {report.system_version or '不明'}",
            f"摘要: {report.summary}",
            f"报告人: {report.reporter_name or report.user_id} 群: {report.group_id or 'private'}",
            f"时间: {report.created_at}",
        ]
        if report.github_issue_url:
            lines.append(f"GitHub Issue: {report.github_issue_url}")
        elif report.github_submit_error:
            lines.append(f"GitHub 提交: {report.github_submit_error}")
        lines.extend(["原文:", report.source_text.strip() or "(空)"])
        if report.extra_text:
            lines.extend(["补充:", report.extra_text])
        return "\n".join(lines)
