from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from .message_filter import MessageFilter

if TYPE_CHECKING:
    from .bot_core import BotCore

logger = logging.getLogger("admin_handler")

_ROOT_COMMANDS = {
    "help",
    "status",
    "clearcache",
    "allow",
    "deny",
    "addkw",
    "reload",
    "todo",
    "feedback",
    "record",
    "kb",
    "learn",
}

_KB_JSON_PROMPT = """请把管理员给出的知识整理成精简、稳定、可复用的知识库条目，并输出 JSON。
输出格式：
{
  "title": "标题",
  "category": "分类",
  "content": "整理后的正文",
  "tags": ["标签1", "标签2"]
}

要求：
- 只输出 JSON
- title 简洁明确
- category 适合游戏/App 知识，例如：安装、兼容性、报错、规则、版本、功能
- content 用简洁中文整理，不要废话，不要寒暄
- tags 保持 2 到 6 个
"""

_KB_UPDATE_JSON_PROMPT = """请根据已有知识条目和管理员的修订要求，输出更新后的完整知识条目 JSON。
输出格式：
{
  "title": "标题",
  "category": "分类",
  "content": "更新后的正文",
  "tags": ["标签1", "标签2"]
}

要求：
- 只输出 JSON
- 输出的是更新后的完整结果，不是补丁
- 保留已有条目中仍然正确的信息
- 按管理员要求修正或补充
- content 要简洁、明确、适合长期放进知识库
"""


class AdminHandler:
    def __init__(self, core: "BotCore") -> None:
        self._core = core

    def is_admin(self, user_id: str) -> bool:
        return user_id in self._core.config.admin.admin_qq_list

    def has_command_permission(self, user_id: str, event: dict) -> bool:
        if self.is_admin(user_id):
            return True
        if event.get("message_type") != "group":
            return False
        role = str(event.get("sender", {}).get("role", "")).lower()
        return role in {"admin", "owner"}

    def extract_command_text(
        self,
        text: str,
        event: dict,
        segments: list[dict],
    ) -> str | None:
        normalized = self._normalize_leading_space(text)
        if not normalized:
            return None

        prefix = self._core.config.admin.command_prefix
        user_id = str(event.get("user_id", ""))
        if normalized.startswith(prefix):
            return normalized if self.has_command_permission(user_id, event) else None

        if event.get("message_type") != "group":
            return None
        if not self.has_command_permission(user_id, event):
            return None
        if not MessageFilter.mentions_qq(segments, self._core.config.bot.qq_id):
            return None

        candidate = self._strip_leading_bot_text(normalized)
        if not candidate:
            return None
        if candidate.startswith(prefix):
            return candidate

        first = candidate.split(maxsplit=1)[0].lower()
        if first not in _ROOT_COMMANDS:
            return None
        return f"{prefix} {candidate}"

    async def handle(self, text: str, event: dict) -> str | None:
        prefix = self._core.config.admin.command_prefix
        if not text.startswith(prefix):
            return None

        rest = text[len(prefix) :].strip()
        parts = rest.split() if rest else []
        cmd = parts[0].lower() if parts else ""
        args = parts[1:]
        logger.info("admin command cmd=%r args=%s", cmd, args)

        if cmd in {"", "help"}:
            return self._help()
        if cmd == "status":
            return self._status()
        if cmd == "clearcache":
            return self._clear_cache(args)
        if cmd == "allow":
            if not args:
                return "用法: /bot allow <group_id>"
            return self._group_allow(args[0])
        if cmd == "deny":
            if not args:
                return "用法: /bot deny <group_id>"
            return self._group_deny(args[0])
        if cmd == "addkw":
            if not args:
                return "用法: /bot addkw <keyword>"
            keyword = " ".join(args).strip()
            self._core.config.filter.app_keywords.append(keyword)
            return f"已添加触发关键词: {keyword}"
        if cmd == "reload":
            return "当前版本仍需要重启 bot.py 才会重新加载文件配置。"
        if cmd == "todo":
            return await self._handle_feedback(args, default_mode="todo")
        if cmd in {"feedback", "record"}:
            return await self._handle_feedback(args, default_mode=cmd)
        if cmd == "kb":
            return await self._handle_kb(args, event)
        if cmd == "learn":
            return self._handle_learning(args, event)
        return f"未知命令: {cmd!r}\n{self._help()}"

    def _help(self) -> str:
        prefix = self._core.config.admin.command_prefix
        return (
            "管理命令：\n"
            f"  {prefix} help\n"
            f"  {prefix} status\n"
            f"  {prefix} allow <group_id>\n"
            f"  {prefix} deny <group_id>\n"
            f"  {prefix} clearcache [group_id]\n"
            f"  {prefix} addkw <keyword>\n"
            f"  {prefix} reload\n"
            f"  {prefix} todo [list|all|done <id>|del <id>|clear done]\n"
            f"  {prefix} feedback [list|all|records|show #id|done <id>|del <id>|clear done]\n"
            f"  {prefix} record [records|show #id]\n"
            f"  {prefix} kb status\n"
            f"  {prefix} kb list\n"
            f"  {prefix} kb show #id\n"
            f"  {prefix} kb add <内容>\n"
            f"  {prefix} kb update #id <修订内容>\n"
            f"  {prefix} kb delete #id\n"
            f"  {prefix} learn [list|all|show #id|approve #id|reject #id]"
        )

    def _status(self) -> str:
        cfg = self._core.config
        feedback_store = self._core._feedback.store if self._core._feedback else None
        return (
            "小柚状态：\n"
            f"  NapCat 连接: {'已连接' if self._core.napcat.is_connected else '未连接'}\n"
            f"  对话缓存: {self._core.cache.size}\n"
            f"  Ollama 模型: {cfg.ollama.model}\n"
            f"  Ollama 思考: {'开启' if cfg.ollama.think else '关闭'}\n"
            f"  支持 Agent: {self._core.support_agent.status}\n"
            f"  知识库条目: {self._core.knowledge_store.size}\n"
            f"  待审核群聊知识: {self._core.knowledge_candidates.pending_size}\n"
            f"  反馈记录: {feedback_store.size if feedback_store else '未启用'}\n"
            f"  过滤模式: {'严格(app+help)' if cfg.filter.strict_mode else '宽松(仅app)'}\n"
            f"  管理员: {', '.join(cfg.admin.admin_qq_list) or '(未设置)'}"
        )

    def _clear_cache(self, args: list[str]) -> str:
        group_id = args[0] if args else None
        scoped_count = self._core.cache.clear(group_id)
        group_count = self._core.cache.clear_group_global(group_id)
        if group_id:
            return (
                f"已清空群 {group_id} 的对话缓存({scoped_count})"
                f" 和群上下文({group_count})"
            )
        return f"已清空全部对话缓存({scoped_count}) 和群上下文({group_count})"

    def _group_allow(self, group_id: str) -> str:
        groups = self._core.config.groups
        if groups.mode == "blacklist":
            if group_id in groups.group_list:
                groups.group_list.remove(group_id)
                return f"已将群 {group_id} 移出黑名单"
            return f"群 {group_id} 不在黑名单中"

        if group_id not in groups.group_list:
            groups.group_list.append(group_id)
            return f"已将群 {group_id} 加入白名单"
        return f"群 {group_id} 已在白名单中"

    def _group_deny(self, group_id: str) -> str:
        groups = self._core.config.groups
        if groups.mode == "blacklist":
            if group_id not in groups.group_list:
                groups.group_list.append(group_id)
                return f"已将群 {group_id} 加入黑名单"
            return f"群 {group_id} 已在黑名单中"

        if group_id in groups.group_list:
            groups.group_list.remove(group_id)
            return f"已将群 {group_id} 移出白名单"
        return f"群 {group_id} 不在白名单中"

    async def _handle_feedback(self, args: list[str], default_mode: str) -> str:
        feedback = self._core._feedback
        if not feedback:
            return "反馈收集未启用，请在 config.toml 里设置 feedback.enabled = true"

        if not args:
            if default_mode == "todo":
                return feedback.format_todo_list(include_done=False)
            return feedback.format_feedback_records(include_done=True)

        sub = args[0].lower()
        if sub == "list":
            return feedback.format_todo_list(include_done=False)
        if sub == "all":
            return feedback.format_todo_list(include_done=True)
        if sub == "records":
            include_done = True
            if len(args) >= 2 and args[1].lower() in {"pending", "todo", "open"}:
                include_done = False
            return feedback.format_feedback_records(include_done=include_done)
        if sub == "show":
            if len(args) < 2:
                return "用法: /bot feedback show #id"
            item_id = self._parse_item_id(args[1])
            if item_id is None:
                return f"ID 格式错误: {args[1]!r}"
            return feedback.format_feedback_detail(item_id)
        if sub == "done":
            if len(args) < 2:
                return "用法: /bot feedback done <id>"
            item_id = self._parse_item_id(args[1])
            if item_id is None:
                return f"ID 格式错误: {args[1]!r}"
            item = feedback.store.mark_done(item_id)
            if not item:
                return f"未找到 ID #{item_id}"
            return f"已标记完成 #{item.id}\n{item.content}"
        if sub == "del":
            if len(args) < 2:
                return "用法: /bot feedback del <id>"
            item_id = self._parse_item_id(args[1])
            if item_id is None:
                return f"ID 格式错误: {args[1]!r}"
            if feedback.store.delete(item_id):
                return f"已删除 #{item_id}"
            return f"未找到 ID #{item_id}"
        if sub == "clear" and len(args) >= 2 and args[1].lower() == "done":
            count = feedback.store.clear_done()
            return f"已批量清除 {count} 条已完成记录"
        return self._feedback_help()

    def _feedback_help(self) -> str:
        prefix = self._core.config.admin.command_prefix
        return (
            "反馈命令：\n"
            f"  {prefix} todo list\n"
            f"  {prefix} todo all\n"
            f"  {prefix} feedback records\n"
            f"  {prefix} feedback records pending\n"
            f"  {prefix} feedback show #id\n"
            f"  {prefix} feedback done <id>\n"
            f"  {prefix} feedback del <id>\n"
            f"  {prefix} feedback clear done\n"
            f"  {prefix} record records\n"
            f"  {prefix} record show #id"
        )

    async def _handle_kb(self, args: list[str], event: dict) -> str:
        if not args:
            return self._kb_help()

        sub = args[0].lower()
        sub_args = args[1:]
        user_id = str(event.get("user_id", ""))

        if sub == "status":
            store = self._core.knowledge_store
            return (
                "知识库状态：\n"
                f"  条目数: {store.size}\n"
                f"  JSON: {store.json_path}\n"
                f"  Markdown: {store.render_path}"
            )

        if sub == "list":
            items = self._core.knowledge_store.list_items()
            if not items:
                return "知识库还是空的。"
            lines = ["知识库条目："]
            for item in items:
                lines.append(f"  #{item.id} [{item.category}] {item.title}")
            return "\n".join(lines)

        if sub == "show":
            if len(sub_args) != 1:
                return "用法: /bot kb show #id"
            item_id = self._parse_item_id(sub_args[0])
            if item_id is None:
                return "show 只接受 #id"
            item = self._core.knowledge_store.get_item(item_id)
            if not item:
                return f"未找到知识条目 #{item_id}"
            tags = ", ".join(item.tags) if item.tags else "无"
            return (
                f"#{item.id} {item.title}\n"
                f"分类: {item.category}\n"
                f"标签: {tags}\n"
                f"更新人: {item.updated_by}\n"
                f"更新时间: {item.updated_at}\n\n"
                f"{item.content}"
            )

        if sub == "add":
            if not sub_args:
                return "用法: /bot kb add <自然语言内容>"
            structured = await self._draft_entry(" ".join(sub_args), user_id)
            if not structured:
                return "知识整理失败，请稍后再试。"
            item = self._core.knowledge_store.add_item(
                title=structured["title"],
                category=structured["category"],
                content=structured["content"],
                tags=structured["tags"],
                updated_by=user_id,
            )
            return self._format_kb_saved("已新增知识条目", item)

        if sub == "update":
            if len(sub_args) < 2:
                return "用法: /bot kb update #id <自然语言修订内容>"
            item_id = self._parse_item_id(sub_args[0])
            if item_id is None:
                return "update 只接受 #id"
            existing = self._core.knowledge_store.get_item(item_id)
            if not existing:
                return f"未找到知识条目 #{item_id}"
            structured = await self._rewrite_entry(existing, " ".join(sub_args[1:]), user_id)
            if not structured:
                return "知识更新失败，请稍后再试。"
            updated = self._core.knowledge_store.update_item(
                item_id,
                title=structured["title"],
                category=structured["category"],
                content=structured["content"],
                tags=structured["tags"],
                updated_by=user_id,
            )
            if not updated:
                return f"未找到知识条目 #{item_id}"
            return self._format_kb_saved("已更新知识条目", updated)

        if sub == "delete":
            if len(sub_args) != 1:
                return "用法: /bot kb delete #id"
            item_id = self._parse_item_id(sub_args[0])
            if item_id is None:
                return "delete 只接受 #id"
            if self._core.knowledge_store.delete_item(item_id):
                return f"已删除知识条目 #{item_id}"
            return f"未找到知识条目 #{item_id}"

        return self._kb_help()

    def _handle_learning(self, args: list[str], event: dict) -> str:
        prefix = self._core.config.admin.command_prefix
        sub = args[0].lower() if args else "list"
        rest = args[1:]
        store = self._core.knowledge_candidates

        if sub in {"list", "pending", "all"}:
            status = None if sub == "all" else "pending"
            items = store.list(status=status)
            if not items:
                return "当前没有符合条件的群聊知识候选。"
            title = "全部群聊知识候选" if status is None else "待审核群聊知识候选"
            lines = [f"{title}（{len(items)} 条）："]
            for item in items:
                lines.append(
                    f"#{item.id} [{item.status}] 置信度 {item.confidence:.2f} - {item.title}"
                )
            return "\n".join(lines)

        if sub == "show":
            candidate_id = self._parse_item_id(rest[0]) if len(rest) == 1 else None
            if candidate_id is None:
                return f"用法: {prefix} learn show #id"
            item = store.get(candidate_id)
            if not item:
                return f"未找到知识候选 #{candidate_id}"
            return (
                f"候选 #{item.id} [{item.status}]\n"
                f"置信度: {item.confidence:.2f}\n"
                f"标题: {item.title}\n"
                f"分类: {item.category}\n"
                f"标签: {', '.join(item.tags) or '无'}\n\n"
                f"内容:\n{item.content}\n\n"
                f"证据（群聊问答摘要）:\n{item.evidence}"
            )

        if sub == "approve":
            candidate_id = self._parse_item_id(rest[0]) if len(rest) == 1 else None
            if candidate_id is None:
                return f"用法: {prefix} learn approve #id"
            item = store.approve(
                candidate_id,
                self._core.knowledge_store,
                str(event.get("user_id", "admin")),
            )
            return (
                f"已将候选 #{candidate_id} 写入正式知识库，条目 #{item.id}。"
                if item
                else f"候选 #{candidate_id} 不存在或已处理。"
            )

        if sub == "reject":
            candidate_id = self._parse_item_id(rest[0]) if len(rest) == 1 else None
            if candidate_id is None:
                return f"用法: {prefix} learn reject #id"
            if store.reject(candidate_id, str(event.get("user_id", "admin"))):
                return f"已拒绝候选 #{candidate_id}，不会写入知识库。"
            return f"候选 #{candidate_id} 不存在或已处理。"

        return (
            "群聊学习命令：\n"
            f"  {prefix} learn list\n"
            f"  {prefix} learn all\n"
            f"  {prefix} learn show #id\n"
            f"  {prefix} learn approve #id\n"
            f"  {prefix} learn reject #id"
        )

    def _kb_help(self) -> str:
        prefix = self._core.config.admin.command_prefix
        return (
            "知识库命令：\n"
            f"  {prefix} kb status\n"
            f"  {prefix} kb list\n"
            f"  {prefix} kb show #id\n"
            f"  {prefix} kb add <内容>\n"
            f"  {prefix} kb update #id <修订内容>\n"
            f"  {prefix} kb delete #id"
        )

    async def _draft_entry(self, raw_text: str, user_id: str) -> dict[str, Any] | None:
        result = await self._core.llm.chat_json(
            messages=[{"role": "user", "content": raw_text}],
            system_prompt=_KB_JSON_PROMPT,
            user_id=user_id,
            temperature=self._core.config.ollama.json_temperature,
            max_tokens=600,
        )
        return self._normalize_structured_entry(result)

    async def _rewrite_entry(
        self,
        item: Any,
        instruction: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        prompt = (
            "已有知识条目：\n"
            f"title={item.title}\n"
            f"category={item.category}\n"
            f"tags={', '.join(item.tags)}\n"
            f"content={item.content}\n\n"
            f"管理员修订要求：\n{instruction}"
        )
        result = await self._core.llm.chat_json(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_KB_UPDATE_JSON_PROMPT,
            user_id=user_id,
            temperature=self._core.config.ollama.json_temperature,
            max_tokens=700,
        )
        return self._normalize_structured_entry(result)

    @staticmethod
    def _normalize_structured_entry(result: object) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None

        title = str(result.get("title", "")).strip()
        category = str(result.get("category", "")).strip()
        content = str(result.get("content", "")).strip()
        raw_tags = result.get("tags", [])

        if not title or not category or not content:
            return None
        if not isinstance(raw_tags, list):
            raw_tags = []

        tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
        if not tags:
            tags = [category]

        return {
            "title": title,
            "category": category,
            "content": content,
            "tags": tags[:6],
        }

    @staticmethod
    def _format_kb_saved(prefix: str, item: Any) -> str:
        tags = ", ".join(item.tags) if item.tags else "无"
        return (
            f"{prefix} #{item.id}\n"
            f"标题: {item.title}\n"
            f"分类: {item.category}\n"
            f"标签: {tags}\n"
            f"摘要: {item.content}"
        )

    def _strip_leading_bot_text(self, text: str) -> str:
        normalized = self._normalize_leading_space(text)
        nickname = self._core.config.bot.nickname.strip()
        if nickname:
            pattern = rf"^(?:@?{re.escape(nickname)})[\s,，:：。!！?？]*"
            normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)
        return self._normalize_leading_space(normalized)

    @staticmethod
    def _normalize_leading_space(text: str) -> str:
        return text.lstrip(" \t\r\n\u00a0\u2005\u200b\u3000")

    @staticmethod
    def _parse_item_id(raw: str) -> int | None:
        text = raw.strip()
        if text.startswith("#"):
            text = text[1:]
        if not text.isdigit():
            return None
        return int(text)
