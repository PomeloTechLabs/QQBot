from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class BotConfig:
    qq_id: str
    nickname: str


@dataclass
class NapCatConfig:
    host: str = "localhost"
    port: int = 5004
    access_token: str = ""
    reconnect_interval: int = 5


@dataclass
class DeepSeekConfig:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout: int = 60
    thinking_mode: str = "disabled"
    reply_temperature: float = 0.3
    json_temperature: float = 0.0


@dataclass
class OllamaConfig:
    """Configuration for the local Ollama chat API."""

    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3.8:latest"
    context_length: int = 65536
    timeout: int = 180
    think: bool = True
    reply_temperature: float = 0.25
    json_temperature: float = 0.0


@dataclass
class AgentConfig:
    """Boundaries for the external, mature agent runtime."""

    enabled: bool = True
    runtime: str = "copilot"
    executable: str = "copilot"
    profile: str = "jiuyou-support"
    command_timeout: int = 300
    monitor_all_group_messages: bool = False
    # Unmentioned group messages are triaged together, rather than spending an
    # Agent turn on every line of normal chat.
    group_batch_size: int = 50
    group_batch_wait_seconds: int = 600
    # Final support answers and KB reviews use the mature runtime's extended
    # reasoning; simple batch classification stays deliberately lightweight.
    reasoning_effort: str = "high"
    triage_reasoning_effort: str = "minimal"
    intent_threshold: float = 0.72
    max_tool_turns: int = 4
    web_search_enabled: bool = True
    github_study_enabled: bool = True
    allow_all_web_urls: bool = True
    web_allow_urls: list[str] = field(
        default_factory=lambda: [
            "https://www.bing.com",
            "https://github.com",
            "https://*.github.com",
            "https://raw.githubusercontent.com",
            "https://docs.github.com",
            "https://developer.android.com",
            "https://developer.huawei.com",
            "https://appgallery.huawei.com",
        ]
    )
    web_timeout: int = 15
    max_search_results: int = 5
    group_learning_enabled: bool = True
    # A completed Agent support reply can also be distilled into a candidate.
    # It is reviewed by the owner in the web console before publication.
    agent_answer_learning_enabled: bool = True
    agent_answer_learning_min_confidence: float = 0.90
    candidate_file: str = "data/knowledge_candidates.json"
    skill_library_file: str = "data/operational_skills.md"
    auto_publish_learning: bool = False
    learning_min_confidence: float = 0.90
    learning_question_ttl: int = 900


@dataclass
class FilterConfig:
    app_keywords: list[str] = field(default_factory=list)
    help_keywords: list[str] = field(default_factory=list)
    blocked_keywords: list[str] = field(default_factory=list)
    violation_reply_template: str = (
        "请注意文明发言，本群不允许发送不当内容，请及时撤回或修改。"
    )
    strict_mode: bool = True


@dataclass
class CacheConfig:
    window_size: int = 3
    ttl_seconds: int = 300


@dataclass
class MemoryConfig:
    """Bounded persistent memory for one user inside one conversation scope."""

    enabled: bool = True
    directory: str = "data/conversation_memories"
    recent_turn_limit: int = 10
    retain_after_compaction: int = 7
    summary_max_chars: int = 1200
    max_turn_chars: int = 2000


@dataclass
class AdminConfig:
    admin_qq_list: list[str] = field(default_factory=list)
    command_prefix: str = "/bot"


@dataclass
class GroupsConfig:
    mode: str = "blacklist"
    group_list: list[str] = field(default_factory=list)


@dataclass
class WelcomeConfig:
    enabled: bool = True
    enable_at: bool = True
    message_template: str = (
        "欢迎加入本群，我是{bot_nickname}。"
        "如果你是来咨询小柚 App 的，可以直接@我描述问题。"
    )
    notice_items: list[str] = field(default_factory=list)


@dataclass
class FeedbackConfig:
    enabled: bool = True
    data_file: str = "data/feedback.json"
    md_file: str = "data/feedback.md"
    query_keywords: list[str] = field(
        default_factory=lambda: ["查待办", "待办事项", "查反馈", "反馈列表"]
    )
    clarify_timeout: int = 120
    archive_media: bool = True
    media_directory: str = "data/feedback_media"
    max_image_bytes: int = 15_000_000
    max_video_bytes: int = 100_000_000


@dataclass
class GitHubIssuesConfig:
    """GitHub Issue delivery settings. The token stays local and is never exposed by WebUI."""

    enabled: bool = True
    repository: str = "yifengling0/VintagePomeloPro"
    token: str = field(default="", repr=False)
    token_env: str = "GITHUB_TOKEN"
    submit_feature_requests: bool = True
    include_reporter_name: bool = True
    include_media_links: bool = True
    api_base_url: str = "https://api.github.com"
    timeout: int = 20


@dataclass
class CompatibilityConfig:
    """游戏兼容性自动收录设置：把群友报告的游戏运行情况整理成 Issue。

    缺失信息按默认值补全（App 版本默认最新版、游戏版本默认不明、
    模拟器按游戏类型推断），仅在无法判断时向报告者追问一次。
    """

    enabled: bool = True
    repository: str = "yifengling0/VintagePomeloPro-Compatibility"
    token: str = field(default="", repr=False)
    token_env: str = "GITHUB_TOKEN"
    data_file: str = "data/compatibility_reports.json"
    md_file: str = "data/compatibility_reports.md"
    archive_media: bool = True
    media_directory: str = "data/compatibility_media"
    max_image_bytes: int = 15_000_000
    max_video_bytes: int = 100_000_000
    # 向报告者追问的等待时间（秒）；超时按已补全的信息直接收录
    clarify_timeout: int = 120
    # 同一用户两次兼容性分析的最小间隔（秒），避免频繁消耗 GPU 推理
    analysis_cooldown: int = 30
    default_app_version: str = "最新版"
    unknown_game_version: str = "不明"
    include_reporter_name: bool = True
    include_media_links: bool = True
    api_base_url: str = "https://api.github.com"
    timeout: int = 20


@dataclass
class KnowledgeBaseConfig:
    json_file: str = "data/knowledge_base.json"
    render_file: str = "data/knowledge_base.md"
    history_turns: int = 6
    uncertain_to_kb: bool = True


@dataclass
class AppConfig:
    bot: BotConfig
    napcat: NapCatConfig
    deepseek: DeepSeekConfig
    filter: FilterConfig
    cache: CacheConfig
    admin: AdminConfig
    groups: GroupsConfig
    welcome: WelcomeConfig
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    github_issues: GitHubIssuesConfig = field(default_factory=GitHubIssuesConfig)
    compatibility: CompatibilityConfig = field(default_factory=CompatibilityConfig)
    knowledge_base: KnowledgeBaseConfig = field(default_factory=KnowledgeBaseConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    _path: Path | None = None

    def save(self) -> None:
        if not self._path:
            return

        import toml as toml_lib

        data = {
            "bot": {
                "qq_id": self.bot.qq_id,
                "nickname": self.bot.nickname,
            },
            "napcat": {
                "host": self.napcat.host,
                "port": self.napcat.port,
                "access_token": self.napcat.access_token,
                "reconnect_interval": self.napcat.reconnect_interval,
            },
            "deepseek": {
                "api_key": self.deepseek.api_key,
                "base_url": self.deepseek.base_url,
                "model": self.deepseek.model,
                "timeout": self.deepseek.timeout,
                "thinking_mode": self.deepseek.thinking_mode,
                "reply_temperature": self.deepseek.reply_temperature,
                "json_temperature": self.deepseek.json_temperature,
            },
            "filter": {
                "app_keywords": self.filter.app_keywords,
                "help_keywords": self.filter.help_keywords,
                "blocked_keywords": self.filter.blocked_keywords,
                "violation_reply_template": self.filter.violation_reply_template,
                "strict_mode": self.filter.strict_mode,
            },
            "cache": {
                "window_size": self.cache.window_size,
                "ttl_seconds": self.cache.ttl_seconds,
            },
            "memory": {
                "enabled": self.memory.enabled,
                "directory": self.memory.directory,
                "recent_turn_limit": self.memory.recent_turn_limit,
                "retain_after_compaction": self.memory.retain_after_compaction,
                "summary_max_chars": self.memory.summary_max_chars,
                "max_turn_chars": self.memory.max_turn_chars,
            },
            "admin": {
                "admin_qq_list": [
                    int(qq) if qq.isdigit() else qq for qq in self.admin.admin_qq_list
                ],
                "command_prefix": self.admin.command_prefix,
            },
            "groups": {
                "mode": self.groups.mode,
                "group_list": self.groups.group_list,
            },
            "welcome": {
                "enabled": self.welcome.enabled,
                "enable_at": self.welcome.enable_at,
                "message_template": self.welcome.message_template,
                "notice_items": self.welcome.notice_items,
            },
            "feedback": {
                "enabled": self.feedback.enabled,
                "data_file": self.feedback.data_file,
                "md_file": self.feedback.md_file,
                "query_keywords": self.feedback.query_keywords,
                "clarify_timeout": self.feedback.clarify_timeout,
                "archive_media": self.feedback.archive_media,
                "media_directory": self.feedback.media_directory,
                "max_image_bytes": self.feedback.max_image_bytes,
                "max_video_bytes": self.feedback.max_video_bytes,
            },
            "github_issues": {
                "enabled": self.github_issues.enabled,
                "repository": self.github_issues.repository,
                "token": self.github_issues.token,
                "token_env": self.github_issues.token_env,
                "submit_feature_requests": self.github_issues.submit_feature_requests,
                "include_reporter_name": self.github_issues.include_reporter_name,
                "include_media_links": self.github_issues.include_media_links,
                "api_base_url": self.github_issues.api_base_url,
                "timeout": self.github_issues.timeout,
            },
            "compatibility": {
                "enabled": self.compatibility.enabled,
                "repository": self.compatibility.repository,
                "token": self.compatibility.token,
                "token_env": self.compatibility.token_env,
                "data_file": self.compatibility.data_file,
                "md_file": self.compatibility.md_file,
                "archive_media": self.compatibility.archive_media,
                "media_directory": self.compatibility.media_directory,
                "max_image_bytes": self.compatibility.max_image_bytes,
                "max_video_bytes": self.compatibility.max_video_bytes,
                "clarify_timeout": self.compatibility.clarify_timeout,
                "analysis_cooldown": self.compatibility.analysis_cooldown,
                "default_app_version": self.compatibility.default_app_version,
                "unknown_game_version": self.compatibility.unknown_game_version,
                "include_reporter_name": self.compatibility.include_reporter_name,
                "include_media_links": self.compatibility.include_media_links,
                "api_base_url": self.compatibility.api_base_url,
                "timeout": self.compatibility.timeout,
            },
            "knowledge_base": {
                "json_file": self.knowledge_base.json_file,
                "render_file": self.knowledge_base.render_file,
                "history_turns": self.knowledge_base.history_turns,
                "uncertain_to_kb": self.knowledge_base.uncertain_to_kb,
            },
            "ollama": {
                "base_url": self.ollama.base_url,
                "model": self.ollama.model,
                "context_length": self.ollama.context_length,
                "timeout": self.ollama.timeout,
                "think": self.ollama.think,
                "reply_temperature": self.ollama.reply_temperature,
                "json_temperature": self.ollama.json_temperature,
            },
            "agent": {
                "enabled": self.agent.enabled,
                "runtime": self.agent.runtime,
                "executable": self.agent.executable,
                "profile": self.agent.profile,
                "command_timeout": self.agent.command_timeout,
                "monitor_all_group_messages": self.agent.monitor_all_group_messages,
                "group_batch_size": self.agent.group_batch_size,
                "group_batch_wait_seconds": self.agent.group_batch_wait_seconds,
                "reasoning_effort": self.agent.reasoning_effort,
                "triage_reasoning_effort": self.agent.triage_reasoning_effort,
                "intent_threshold": self.agent.intent_threshold,
                "max_tool_turns": self.agent.max_tool_turns,
                "web_search_enabled": self.agent.web_search_enabled,
                "github_study_enabled": self.agent.github_study_enabled,
                "allow_all_web_urls": self.agent.allow_all_web_urls,
                "web_allow_urls": self.agent.web_allow_urls,
                "web_timeout": self.agent.web_timeout,
                "max_search_results": self.agent.max_search_results,
                "group_learning_enabled": self.agent.group_learning_enabled,
                "agent_answer_learning_enabled": self.agent.agent_answer_learning_enabled,
                "agent_answer_learning_min_confidence": self.agent.agent_answer_learning_min_confidence,
                "candidate_file": self.agent.candidate_file,
                "skill_library_file": self.agent.skill_library_file,
                "auto_publish_learning": self.agent.auto_publish_learning,
                "learning_min_confidence": self.agent.learning_min_confidence,
                "learning_question_ttl": self.agent.learning_question_ttl,
            },
        }

        with open(self._path, "w", encoding="utf-8") as file:
            toml_lib.dump(data, file)


def _list_of_str(data: dict[str, Any], key: str, default: list[str]) -> list[str]:
    return [str(item) for item in data.get(key, default)]


def load_config(path: str | Path = "config.toml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path.resolve()}")

    with open(config_path, "rb") as file:
        raw = tomllib.load(file)

    bot_raw = raw.get("bot", {})
    napcat_raw = raw.get("napcat", {})
    deepseek_raw = raw.get("deepseek", raw.get("coze", {}))
    filter_raw = raw.get("filter", {})
    cache_raw = raw.get("cache", {})
    memory_raw = raw.get("memory", {})
    admin_raw = raw.get("admin", {})
    groups_raw = raw.get("groups", {})
    welcome_raw = raw.get("welcome", {})
    feedback_raw = raw.get("feedback", {})
    kb_raw = raw.get("knowledge_base", {})
    github_issues_raw = raw.get("github_issues", {})
    compatibility_raw = raw.get("compatibility", {})
    ollama_raw = raw.get("ollama", {})
    agent_raw = raw.get("agent", {})

    if not bot_raw.get("qq_id"):
        raise ValueError("配置项 bot.qq_id 不能为空")

    return AppConfig(
        bot=BotConfig(
            qq_id=str(bot_raw["qq_id"]),
            nickname=str(bot_raw.get("nickname", "小柚")),
        ),
        napcat=NapCatConfig(
            host=str(napcat_raw.get("host", "localhost")),
            port=int(napcat_raw.get("port", 5004)),
            access_token=str(napcat_raw.get("access_token", "")),
            reconnect_interval=int(napcat_raw.get("reconnect_interval", 5)),
        ),
        deepseek=DeepSeekConfig(
            api_key=str(deepseek_raw.get("api_key", "")),
            base_url=str(deepseek_raw.get("base_url", "https://api.deepseek.com")),
            model=str(deepseek_raw.get("model", "deepseek-v4-flash")),
            timeout=int(deepseek_raw.get("timeout", 60)),
            thinking_mode=str(deepseek_raw.get("thinking_mode", "disabled")),
            reply_temperature=float(deepseek_raw.get("reply_temperature", 0.3)),
            json_temperature=float(deepseek_raw.get("json_temperature", 0.0)),
        ),
        filter=FilterConfig(
            app_keywords=_list_of_str(filter_raw, "app_keywords", []),
            help_keywords=_list_of_str(filter_raw, "help_keywords", []),
            blocked_keywords=_list_of_str(filter_raw, "blocked_keywords", []),
            violation_reply_template=str(
                filter_raw.get(
                    "violation_reply_template",
                    "请注意文明发言，本群不允许发送不当内容，请及时撤回或修改。",
                )
            ),
            strict_mode=bool(filter_raw.get("strict_mode", True)),
        ),
        cache=CacheConfig(
            window_size=int(cache_raw.get("window_size", 3)),
            ttl_seconds=int(cache_raw.get("ttl_seconds", 300)),
        ),
        memory=_memory_config(memory_raw),
        admin=AdminConfig(
            admin_qq_list=[str(item) for item in admin_raw.get("admin_qq_list", [])],
            command_prefix=str(admin_raw.get("command_prefix", "/bot")),
        ),
        groups=GroupsConfig(
            mode=str(groups_raw.get("mode", "blacklist")),
            group_list=[str(item) for item in groups_raw.get("group_list", [])],
        ),
        welcome=WelcomeConfig(
            enabled=bool(welcome_raw.get("enabled", True)),
            enable_at=bool(welcome_raw.get("enable_at", True)),
            message_template=str(
                welcome_raw.get(
                    "message_template",
                    "欢迎加入本群，我是{bot_nickname}。如果你是来咨询小柚 App 的，可以直接@我描述问题。",
                )
            ),
            notice_items=[str(item) for item in welcome_raw.get("notice_items", [])],
        ),
        feedback=FeedbackConfig(
            enabled=bool(feedback_raw.get("enabled", True)),
            data_file=str(feedback_raw.get("data_file", "data/feedback.json")),
            md_file=str(feedback_raw.get("md_file", "data/feedback.md")),
            query_keywords=_list_of_str(
                feedback_raw,
                "query_keywords",
                ["查待办", "待办事项", "查反馈", "反馈列表"],
            ),
            clarify_timeout=int(feedback_raw.get("clarify_timeout", 120)),
            archive_media=bool(feedback_raw.get("archive_media", True)),
            media_directory=str(feedback_raw.get("media_directory", "data/feedback_media")),
            max_image_bytes=max(100_000, int(feedback_raw.get("max_image_bytes", 15_000_000))),
            max_video_bytes=max(1_000_000, int(feedback_raw.get("max_video_bytes", 100_000_000))),
        ),
        github_issues=GitHubIssuesConfig(
            enabled=bool(github_issues_raw.get("enabled", True)),
            repository=str(
                github_issues_raw.get("repository", "yifengling0/VintagePomeloPro")
            ).strip(),
            token=str(github_issues_raw.get("token", "")).strip(),
            token_env=str(github_issues_raw.get("token_env", "GITHUB_TOKEN")).strip()
            or "GITHUB_TOKEN",
            submit_feature_requests=bool(
                github_issues_raw.get("submit_feature_requests", True)
            ),
            include_reporter_name=bool(
                github_issues_raw.get("include_reporter_name", True)
            ),
            include_media_links=bool(github_issues_raw.get("include_media_links", True)),
            api_base_url=str(
                github_issues_raw.get("api_base_url", "https://api.github.com")
            ).rstrip("/"),
            timeout=max(5, int(github_issues_raw.get("timeout", 20))),
        ),
        compatibility=CompatibilityConfig(
            enabled=bool(compatibility_raw.get("enabled", True)),
            repository=str(
                compatibility_raw.get(
                    "repository", "yifengling0/VintagePomeloPro-Compatibility"
                )
            ).strip(),
            token=str(compatibility_raw.get("token", "")).strip(),
            token_env=str(compatibility_raw.get("token_env", "GITHUB_TOKEN")).strip()
            or "GITHUB_TOKEN",
            data_file=str(
                compatibility_raw.get("data_file", "data/compatibility_reports.json")
            ),
            md_file=str(
                compatibility_raw.get("md_file", "data/compatibility_reports.md")
            ),
            archive_media=bool(compatibility_raw.get("archive_media", True)),
            media_directory=str(
                compatibility_raw.get("media_directory", "data/compatibility_media")
            ),
            max_image_bytes=max(
                100_000, int(compatibility_raw.get("max_image_bytes", 15_000_000))
            ),
            max_video_bytes=max(
                1_000_000, int(compatibility_raw.get("max_video_bytes", 100_000_000))
            ),
            clarify_timeout=max(
                30, int(compatibility_raw.get("clarify_timeout", 120))
            ),
            analysis_cooldown=max(
                0, int(compatibility_raw.get("analysis_cooldown", 30))
            ),
            default_app_version=str(
                compatibility_raw.get("default_app_version", "最新版")
            ),
            unknown_game_version=str(
                compatibility_raw.get("unknown_game_version", "不明")
            ),
            include_reporter_name=bool(
                compatibility_raw.get("include_reporter_name", True)
            ),
            include_media_links=bool(
                compatibility_raw.get("include_media_links", True)
            ),
            api_base_url=str(
                compatibility_raw.get("api_base_url", "https://api.github.com")
            ).rstrip("/"),
            timeout=max(5, int(compatibility_raw.get("timeout", 20))),
        ),
        knowledge_base=KnowledgeBaseConfig(
            json_file=str(kb_raw.get("json_file", "data/knowledge_base.json")),
            render_file=str(kb_raw.get("render_file", "data/knowledge_base.md")),
            history_turns=int(kb_raw.get("history_turns", 6)),
            uncertain_to_kb=bool(kb_raw.get("uncertain_to_kb", True)),
        ),
        ollama=OllamaConfig(
            base_url=str(ollama_raw.get("base_url", "http://127.0.0.1:11434")),
            model=str(ollama_raw.get("model", "qwen3.8:latest")),
            context_length=max(4096, int(ollama_raw.get("context_length", 65536))),
            timeout=int(ollama_raw.get("timeout", 180)),
            think=bool(ollama_raw.get("think", True)),
            reply_temperature=float(ollama_raw.get("reply_temperature", 0.25)),
            json_temperature=float(ollama_raw.get("json_temperature", 0.0)),
        ),
        agent=AgentConfig(
            enabled=bool(agent_raw.get("enabled", True)),
            runtime=str(agent_raw.get("runtime", "copilot")).strip().lower(),
            executable=str(agent_raw.get("executable", "copilot")).strip() or "copilot",
            profile=str(agent_raw.get("profile", "jiuyou-support")).strip() or "jiuyou-support",
            command_timeout=max(30, int(agent_raw.get("command_timeout", 300))),
            monitor_all_group_messages=bool(
                agent_raw.get("monitor_all_group_messages", False)
            ),
            group_batch_size=max(
                2,
                int(agent_raw.get("group_batch_size", AgentConfig().group_batch_size)),
            ),
            group_batch_wait_seconds=max(
                15,
                int(
                    agent_raw.get(
                        "group_batch_wait_seconds",
                        AgentConfig().group_batch_wait_seconds,
                    )
                ),
            ),
            reasoning_effort=_reasoning_effort(
                agent_raw.get("reasoning_effort", "high"),
                default="high",
            ),
            triage_reasoning_effort=_reasoning_effort(
                agent_raw.get("triage_reasoning_effort", "minimal"),
                default="minimal",
            ),
            intent_threshold=float(agent_raw.get("intent_threshold", 0.72)),
            max_tool_turns=max(1, int(agent_raw.get("max_tool_turns", 4))),
            web_search_enabled=bool(agent_raw.get("web_search_enabled", True)),
            github_study_enabled=bool(agent_raw.get("github_study_enabled", True)),
            allow_all_web_urls=bool(agent_raw.get("allow_all_web_urls", True)),
            web_allow_urls=_list_of_str(
                agent_raw,
                "web_allow_urls",
                AgentConfig().web_allow_urls,
            ),
            web_timeout=max(1, int(agent_raw.get("web_timeout", 15))),
            max_search_results=max(1, int(agent_raw.get("max_search_results", 5))),
            group_learning_enabled=bool(agent_raw.get("group_learning_enabled", True)),
            agent_answer_learning_enabled=bool(
                agent_raw.get("agent_answer_learning_enabled", True)
            ),
            agent_answer_learning_min_confidence=float(
                agent_raw.get("agent_answer_learning_min_confidence", 0.90)
            ),
            candidate_file=str(agent_raw.get("candidate_file", "data/knowledge_candidates.json")),
            skill_library_file=str(
                agent_raw.get("skill_library_file", "data/operational_skills.md")
            ),
            auto_publish_learning=bool(agent_raw.get("auto_publish_learning", False)),
            learning_min_confidence=float(agent_raw.get("learning_min_confidence", 0.90)),
            learning_question_ttl=max(60, int(agent_raw.get("learning_question_ttl", 900))),
        ),
        _path=config_path,
    )


def _reasoning_effort(value: Any, *, default: str) -> str:
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
    normalized = str(value).strip().lower()
    return normalized if normalized in allowed else default


def _memory_config(raw: dict[str, Any]) -> MemoryConfig:
    recent_turn_limit = max(2, int(raw.get("recent_turn_limit", 10)))
    retain_after_compaction = min(
        recent_turn_limit - 1,
        max(1, int(raw.get("retain_after_compaction", 7))),
    )
    return MemoryConfig(
        enabled=bool(raw.get("enabled", True)),
        directory=str(raw.get("directory", "data/conversation_memories")),
        recent_turn_limit=recent_turn_limit,
        retain_after_compaction=retain_after_compaction,
        summary_max_chars=max(200, int(raw.get("summary_max_chars", 1200))),
        max_turn_chars=max(200, int(raw.get("max_turn_chars", 2000))),
    )
