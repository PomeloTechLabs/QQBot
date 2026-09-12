from __future__ import annotations

import logging
import json
import re
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_cors import CORS

from .agent_activity import AgentActivityStream
from .config import AppConfig
from .knowledge_candidates import KnowledgeCandidate, KnowledgeCandidateStore
from .knowledge_store import KnowledgeItem, KnowledgeStore

_TAG_SPLIT_RE = re.compile(r"[\n,，]+")
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


def _sse(event: dict) -> str:
    """Encode one trusted server event for EventSource clients."""
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"event: agent_activity\ndata: {payload}\n\n"


def _serialize_item(item: KnowledgeItem) -> dict:
    return item.to_dict()


def _serialize_store(
    store: KnowledgeStore,
    skill_library_path: Path | None = None,
) -> dict:
    payload = {
        "items": [_serialize_item(item) for item in store.list_items()],
        "size": store.size,
        "json_file": str(store.json_path.resolve()),
        "render_file": str(store.render_path.resolve()),
        "render_text": store.render_text(),
    }
    if skill_library_path is not None:
        payload["skill_library_file"] = str(skill_library_path.resolve())
    return payload


def _serialize_candidate(item: KnowledgeCandidate) -> dict:
    return item.to_dict()


def _serialize_candidates(store: KnowledgeCandidateStore, status: str | None = None) -> dict:
    return {
        "items": [_serialize_candidate(item) for item in store.list(status=status)],
        "size": store.size,
        "pending_size": store.pending_size,
    }


def _normalize_tags(value: object) -> list[str]:
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    elif isinstance(value, str):
        raw_items = _TAG_SPLIT_RE.split(value)
    else:
        raw_items = []
    return [item.strip() for item in raw_items if item.strip()]


def _normalize_reasoning_effort(value: object, default: str) -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized in _REASONING_EFFORTS else default


def _read_item_payload(data: dict) -> dict:
    title = str(data.get("title", "")).strip()
    category = str(data.get("category", "")).strip()
    content = str(data.get("content", "")).strip()
    updated_by = str(data.get("updated_by", "")).strip() or "web_console"
    tags = _normalize_tags(data.get("tags", []))

    if not title:
        raise ValueError("title is required")
    if not category:
        raise ValueError("category is required")
    if not content:
        raise ValueError("content is required")

    return {
        "title": title,
        "category": category,
        "content": content,
        "tags": tags,
        "updated_by": updated_by,
    }


def create_app(
    config: AppConfig,
    knowledge_store: KnowledgeStore | None = None,
    knowledge_candidates: KnowledgeCandidateStore | None = None,
    agent_activity: AgentActivityStream | None = None,
) -> Flask:
    app = Flask(__name__, static_folder="../static")
    CORS(app)
    logger = logging.getLogger("web_server")
    store = knowledge_store or KnowledgeStore(
        json_path=config.knowledge_base.json_file,
        render_path=config.knowledge_base.render_file,
    )
    candidates = knowledge_candidates or KnowledgeCandidateStore(config.agent.candidate_file)
    activity_stream = agent_activity or AgentActivityStream()

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/api/agent-activity/stream")
    def stream_agent_activity():
        """Live-only Server-Sent Events; no history is written or replayed."""
        subscriber_id, event_queue, active = activity_stream.subscribe()

        @stream_with_context
        def generate():
            try:
                # Send a padded first comment so local WSGI/TCP buffering does
                # not delay EventSource's ``open`` event until the heartbeat.
                # It is a comment, therefore carries no business data.
                yield ": connected " + (" " * 2048) + "\n\n"
                for event in active:
                    yield _sse(event)
                while True:
                    event = activity_stream.next_event(event_queue, timeout=15.0)
                    if event is None:
                        yield ": keepalive\n\n"
                    else:
                        yield _sse(event)
            finally:
                activity_stream.unsubscribe(subscriber_id)

        response = Response(generate(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.route("/api/config", methods=["GET"])
    def get_config():
        return jsonify(
            {
                "bot": {
                    "nickname": config.bot.nickname,
                    "qq_id": config.bot.qq_id,
                },
                "deepseek": {
                    "base_url": config.deepseek.base_url,
                    "model": config.deepseek.model,
                    "timeout": config.deepseek.timeout,
                    "thinking_mode": config.deepseek.thinking_mode,
                    "reply_temperature": config.deepseek.reply_temperature,
                    "json_temperature": config.deepseek.json_temperature,
                },
                "ollama": {
                    "base_url": config.ollama.base_url,
                    "model": config.ollama.model,
                    "context_length": config.ollama.context_length,
                    "timeout": config.ollama.timeout,
                    "think": config.ollama.think,
                    "reply_temperature": config.ollama.reply_temperature,
                    "json_temperature": config.ollama.json_temperature,
                },
                "agent": {
                    "enabled": config.agent.enabled,
                    "runtime": config.agent.runtime,
                    "executable": config.agent.executable,
                    "profile": config.agent.profile,
                    "command_timeout": config.agent.command_timeout,
                    "monitor_all_group_messages": config.agent.monitor_all_group_messages,
                    "group_batch_size": config.agent.group_batch_size,
                    "group_batch_wait_seconds": config.agent.group_batch_wait_seconds,
                    "reasoning_effort": config.agent.reasoning_effort,
                    "triage_reasoning_effort": config.agent.triage_reasoning_effort,
                    "web_search_enabled": config.agent.web_search_enabled,
                    "allow_all_web_urls": config.agent.allow_all_web_urls,
                    "web_allow_urls": config.agent.web_allow_urls,
                    "group_learning_enabled": config.agent.group_learning_enabled,
                    "agent_answer_learning_enabled": config.agent.agent_answer_learning_enabled,
                    "agent_answer_learning_min_confidence": config.agent.agent_answer_learning_min_confidence,
                    "skill_library_file": config.agent.skill_library_file,
                    "auto_publish_learning": config.agent.auto_publish_learning,
                },
                "welcome": {
                    "enabled": config.welcome.enabled,
                    "enable_at": config.welcome.enable_at,
                    "message_template": config.welcome.message_template,
                    "notice_items": config.welcome.notice_items,
                },
                "filter": {
                    "app_keywords": config.filter.app_keywords,
                    "help_keywords": config.filter.help_keywords,
                    "blocked_keywords": config.filter.blocked_keywords,
                    "violation_reply_template": config.filter.violation_reply_template,
                },
                "knowledge_base": {
                    "json_file": config.knowledge_base.json_file,
                    "render_file": config.knowledge_base.render_file,
                    "history_turns": config.knowledge_base.history_turns,
                    "uncertain_to_kb": config.knowledge_base.uncertain_to_kb,
                },
                "memory": {
                    "enabled": config.memory.enabled,
                    "directory": config.memory.directory,
                    "recent_turn_limit": config.memory.recent_turn_limit,
                    "retain_after_compaction": config.memory.retain_after_compaction,
                    "summary_max_chars": config.memory.summary_max_chars,
                },
            }
        )

    @app.route("/api/config", methods=["POST"])
    def update_config():
        data = request.get_json(silent=True) or {}
        if not data:
            return jsonify({"error": "No data"}), 400

        try:
            if "bot" in data:
                bot = data["bot"]
                config.bot.nickname = bot.get("nickname", config.bot.nickname)

            if "deepseek" in data:
                deepseek = data["deepseek"]
                config.deepseek.base_url = deepseek.get("base_url", config.deepseek.base_url)
                config.deepseek.model = deepseek.get("model", config.deepseek.model)
                config.deepseek.timeout = int(deepseek.get("timeout", config.deepseek.timeout))
                config.deepseek.thinking_mode = deepseek.get(
                    "thinking_mode",
                    config.deepseek.thinking_mode,
                )
                config.deepseek.reply_temperature = float(
                    deepseek.get("reply_temperature", config.deepseek.reply_temperature)
                )
                config.deepseek.json_temperature = float(
                    deepseek.get("json_temperature", config.deepseek.json_temperature)
                )

            if "ollama" in data:
                ollama = data["ollama"]
                config.ollama.base_url = ollama.get("base_url", config.ollama.base_url)
                config.ollama.model = ollama.get("model", config.ollama.model)
                config.ollama.context_length = max(
                    4096,
                    int(ollama.get("context_length", config.ollama.context_length)),
                )
                config.ollama.timeout = int(ollama.get("timeout", config.ollama.timeout))
                config.ollama.think = bool(ollama.get("think", config.ollama.think))
                config.ollama.reply_temperature = float(
                    ollama.get("reply_temperature", config.ollama.reply_temperature)
                )
                config.ollama.json_temperature = float(
                    ollama.get("json_temperature", config.ollama.json_temperature)
                )

            if "agent" in data:
                agent = data["agent"]
                config.agent.enabled = bool(agent.get("enabled", config.agent.enabled))
                config.agent.command_timeout = int(
                    agent.get("command_timeout", config.agent.command_timeout)
                )
                config.agent.monitor_all_group_messages = bool(
                    agent.get("monitor_all_group_messages", config.agent.monitor_all_group_messages)
                )
                config.agent.group_batch_size = max(
                    2,
                    int(agent.get("group_batch_size", config.agent.group_batch_size)),
                )
                config.agent.group_batch_wait_seconds = max(
                    15,
                    int(
                        agent.get(
                            "group_batch_wait_seconds",
                            config.agent.group_batch_wait_seconds,
                        )
                    ),
                )
                if "reasoning_effort" in agent:
                    config.agent.reasoning_effort = _normalize_reasoning_effort(
                        agent["reasoning_effort"],
                        "high",
                    )
                if "triage_reasoning_effort" in agent:
                    config.agent.triage_reasoning_effort = _normalize_reasoning_effort(
                        agent["triage_reasoning_effort"],
                        "minimal",
                    )
                config.agent.web_search_enabled = bool(
                    agent.get("web_search_enabled", config.agent.web_search_enabled)
                )
                config.agent.allow_all_web_urls = bool(
                    agent.get("allow_all_web_urls", config.agent.allow_all_web_urls)
                )
                if "web_allow_urls" in agent:
                    config.agent.web_allow_urls = _normalize_tags(agent["web_allow_urls"])
                config.agent.group_learning_enabled = bool(
                    agent.get("group_learning_enabled", config.agent.group_learning_enabled)
                )
                config.agent.agent_answer_learning_enabled = bool(
                    agent.get(
                        "agent_answer_learning_enabled",
                        config.agent.agent_answer_learning_enabled,
                    )
                )
                config.agent.agent_answer_learning_min_confidence = min(
                    1.0,
                    max(
                        0.0,
                        float(
                            agent.get(
                                "agent_answer_learning_min_confidence",
                                config.agent.agent_answer_learning_min_confidence,
                            )
                        ),
                    ),
                )
                if "skill_library_file" in agent:
                    config.agent.skill_library_file = str(agent["skill_library_file"])
                config.agent.auto_publish_learning = bool(
                    agent.get("auto_publish_learning", config.agent.auto_publish_learning)
                )

            if "welcome" in data:
                welcome = data["welcome"]
                config.welcome.enabled = welcome.get("enabled", config.welcome.enabled)
                config.welcome.enable_at = welcome.get("enable_at", config.welcome.enable_at)
                config.welcome.message_template = welcome.get(
                    "message_template",
                    config.welcome.message_template,
                )
                config.welcome.notice_items = welcome.get(
                    "notice_items",
                    config.welcome.notice_items,
                )

            if "filter" in data:
                filters = data["filter"]
                config.filter.app_keywords = _normalize_tags(
                    filters.get(
                        "app_keywords",
                        config.filter.app_keywords,
                    )
                )
                config.filter.help_keywords = _normalize_tags(
                    filters.get(
                        "help_keywords",
                        config.filter.help_keywords,
                    )
                )
                config.filter.blocked_keywords = _normalize_tags(
                    filters.get(
                        "blocked_keywords",
                        config.filter.blocked_keywords,
                    )
                )
                config.filter.violation_reply_template = str(
                    filters.get(
                        "violation_reply_template",
                        config.filter.violation_reply_template,
                    )
                )

            if "knowledge_base" in data:
                kb = data["knowledge_base"]
                config.knowledge_base.json_file = kb.get(
                    "json_file",
                    config.knowledge_base.json_file,
                )
                config.knowledge_base.render_file = kb.get(
                    "render_file",
                    config.knowledge_base.render_file,
                )
                config.knowledge_base.history_turns = int(
                    kb.get("history_turns", config.knowledge_base.history_turns)
                )
                config.knowledge_base.uncertain_to_kb = bool(
                    kb.get("uncertain_to_kb", config.knowledge_base.uncertain_to_kb)
                )

            config.save()
            logger.info("Config updated from web console")
            return jsonify({"status": "success"})
        except Exception as exc:
            logger.exception("Failed to update config")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/knowledge-base", methods=["GET"])
    def get_knowledge_base():
        return jsonify(_serialize_store(store, candidates.skill_library_path))

    @app.route("/api/knowledge-base/reload", methods=["POST"])
    def reload_knowledge_base():
        try:
            store.reload()
            candidates.sync_skill_library(store)
            logger.info("Knowledge base reloaded from disk")
            return jsonify(_serialize_store(store, candidates.skill_library_path))
        except Exception as exc:
            logger.exception("Failed to reload knowledge base")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/knowledge-base/items", methods=["POST"])
    def create_knowledge_item():
        data = request.get_json(silent=True) or {}
        try:
            payload = _read_item_payload(data)
            item = store.add_item(**payload)
            candidates.sync_skill_library(store)
            logger.info("Knowledge item created: #%s %s", item.id, item.title)
            return jsonify({"item": _serialize_item(item), "size": store.size}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("Failed to create knowledge item")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/knowledge-base/items/<int:item_id>", methods=["PUT"])
    def update_knowledge_item(item_id: int):
        data = request.get_json(silent=True) or {}
        try:
            payload = _read_item_payload(data)
            item = store.update_item(item_id, **payload)
            if not item:
                return jsonify({"error": f"item #{item_id} not found"}), 404
            logger.info("Knowledge item updated: #%s %s", item.id, item.title)
            candidates.sync_skill_library(store)
            return jsonify({"item": _serialize_item(item), "size": store.size})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("Failed to update knowledge item")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/knowledge-base/items/<int:item_id>", methods=["DELETE"])
    def delete_knowledge_item(item_id: int):
        try:
            deleted = store.delete_item(item_id)
            if not deleted:
                return jsonify({"error": f"item #{item_id} not found"}), 404
            candidates.sync_skill_library(store)
            logger.info("Knowledge item deleted: #%s", item_id)
            return jsonify({"status": "deleted", "size": store.size})
        except Exception as exc:
            logger.exception("Failed to delete knowledge item")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/knowledge-candidates", methods=["GET"])
    def get_knowledge_candidates():
        requested_status = str(request.args.get("status", "pending")).strip().lower()
        status = requested_status if requested_status in {"pending", "published", "rejected"} else None
        return jsonify(_serialize_candidates(candidates, status=status))

    @app.route("/api/knowledge-candidates/<int:candidate_id>/approve", methods=["POST"])
    def approve_knowledge_candidate(candidate_id: int):
        try:
            saved = candidates.approve(candidate_id, store, reviewer="web_console")
            if not saved:
                return jsonify({"error": f"pending candidate #{candidate_id} not found"}), 404
            logger.info("Knowledge candidate approved from web console: #%s -> KB #%s", candidate_id, saved.id)
            return jsonify(
                {
                    "status": "published",
                    "item": _serialize_item(saved),
                    "candidates": _serialize_candidates(candidates, status="pending"),
                }
            )
        except Exception as exc:
            logger.exception("Failed to approve knowledge candidate")
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/knowledge-candidates/<int:candidate_id>/reject", methods=["POST"])
    def reject_knowledge_candidate(candidate_id: int):
        try:
            if not candidates.reject(candidate_id, reviewer="web_console"):
                return jsonify({"error": f"pending candidate #{candidate_id} not found"}), 404
            logger.info("Knowledge candidate rejected from web console: #%s", candidate_id)
            return jsonify(
                {
                    "status": "rejected",
                    "candidates": _serialize_candidates(candidates, status="pending"),
                }
            )
        except Exception as exc:
            logger.exception("Failed to reject knowledge candidate")
            return jsonify({"error": str(exc)}), 500

    return app


def run_web_server(
    config: AppConfig,
    knowledge_store: KnowledgeStore | None = None,
    knowledge_candidates: KnowledgeCandidateStore | None = None,
    agent_activity: AgentActivityStream | None = None,
    port: int = 8080,
) -> None:
    app = create_app(
        config,
        knowledge_store=knowledge_store,
        knowledge_candidates=knowledge_candidates,
        agent_activity=agent_activity,
    )
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.ERROR)
    print(f"\n[WebUI] Control panel started: http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def start_web_thread(
    config: AppConfig,
    knowledge_store: KnowledgeStore | None = None,
    knowledge_candidates: KnowledgeCandidateStore | None = None,
    agent_activity: AgentActivityStream | None = None,
    port: int = 8080,
) -> threading.Thread:
    thread = threading.Thread(
        target=run_web_server,
        args=(config, knowledge_store, knowledge_candidates, agent_activity, port),
        daemon=True,
    )
    thread.start()
    return thread
