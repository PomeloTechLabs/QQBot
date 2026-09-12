from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .config import AgentConfig
from .copilot_agent import CopilotAgent
from .knowledge_candidates import KnowledgeCandidateStore
from .knowledge_store import KnowledgeStore

logger = logging.getLogger("group_knowledge_learner")


@dataclass
class _Question:
    user_id: str
    text: str
    created: float


class GroupKnowledgeLearner:
    """Observes question/solution pairs and asks Copilot to draft candidates.

    It never makes an agent capable of editing the knowledge base directly.
    """

    def __init__(
        self,
        config: AgentConfig,
        agent: CopilotAgent,
        candidates: KnowledgeCandidateStore,
        knowledge_store: KnowledgeStore,
    ) -> None:
        self._config = config
        self._agent = agent
        self._candidates = candidates
        self._knowledge_store = knowledge_store
        self._questions: dict[str, deque[_Question]] = defaultdict(lambda: deque(maxlen=12))

    async def observe(
        self,
        group_id: str,
        user_id: str,
        text: str,
        context: list[dict[str, str]],
    ) -> None:
        if not self._config.group_learning_enabled or not self._agent.available:
            return
        self._cleanup(group_id)
        if self._looks_like_product_question(text):
            self._questions[group_id].append(_Question(user_id=user_id, text=text, created=time.monotonic()))
            return
        if not self._looks_like_explanation(text):
            return
        question = next(
            (item for item in reversed(self._questions[group_id]) if item.user_id != user_id),
            None,
        )
        if not question:
            return
        self._questions[group_id].remove(question)
        proposal = await self._agent.propose_knowledge(question.text, text, context)
        candidate = self._validate_and_add(proposal, question.text, text)
        if not candidate:
            return
        logger.info("已生成群聊知识候选 #%s, confidence=%.2f", candidate.id, candidate.confidence)
        if self._config.auto_publish_learning and candidate.confidence >= self._config.learning_min_confidence:
            saved = self._candidates.approve(candidate.id, self._knowledge_store, "auto-high-confidence")
            if saved:
                logger.info("已自动发布高置信知识候选 #%s -> KB #%s", candidate.id, saved.id)

    async def observe_agent_answer(
        self,
        question: str,
        answer: str,
        context: list[dict[str, str]],
    ) -> None:
        """Review a completed support answer as a *candidate*, never as fact.

        This keeps the useful operational knowledge discovered through web/KB
        research, while retaining a human approval point before it can affect
        future answers.
        """
        if (
            not self._config.group_learning_enabled
            or not self._config.agent_answer_learning_enabled
            or not self._agent.available
            or not self._looks_like_reusable_answer(answer)
        ):
            return
        proposal = await self._agent.propose_knowledge(
            question,
            answer,
            context,
            source_label="机器人技术答复（待人工核验）",
        )
        candidate = self._validate_and_add(
            proposal,
            question,
            answer,
            min_confidence=self._config.agent_answer_learning_min_confidence,
            evidence_label="机器人技术答复（待人工核验）",
        )
        if candidate:
            logger.info(
                "已生成机器人答复知识候选 #%s, confidence=%.2f",
                candidate.id,
                candidate.confidence,
            )

    def _validate_and_add(
        self,
        proposal: object,
        question: str,
        answer: str,
        *,
        min_confidence: float = 0.70,
        evidence_label: str = "群友解答",
    ):
        if not isinstance(proposal, dict) or not proposal.get("should_learn"):
            return None
        try:
            confidence = float(proposal.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if confidence < min_confidence:
            return None
        tags = proposal.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        return self._candidates.add(
            title=str(proposal.get("title", "")),
            category=str(proposal.get("category", "")),
            content=str(proposal.get("content", "")),
            tags=[str(tag) for tag in tags],
            confidence=confidence,
            evidence=f"问题：{question}\n\n{evidence_label}：{answer}",
        )

    def _cleanup(self, group_id: str) -> None:
        deadline = time.monotonic() - self._config.learning_question_ttl
        questions = self._questions[group_id]
        while questions and questions[0].created < deadline:
            questions.popleft()

    @staticmethod
    def _looks_like_product_question(text: str) -> bool:
        compact = text.strip().lower()
        product = any(word in compact for word in ("旧柚", "小柚", "闪传", "jiuyou", "vintage pomelo"))
        question = "?" in compact or "？" in compact or any(word in compact for word in ("怎么", "如何", "为什么", "能不能", "无法", "不行", "报错", "闪退", "失败"))
        return product and question

    @staticmethod
    def _looks_like_explanation(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 24 or "?" in compact or "？" in compact:
            return False
        return any(word in compact for word in ("先", "需要", "可以", "安装", "设置", "版本", "尝试", "解决", "检查", "更新", "确认"))

    @staticmethod
    def _looks_like_reusable_answer(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 80:
            return False
        if any(word in compact for word in ("抱歉", "不能", "不支持", "不确定", "无法确认")):
            return False
        return any(
            word in compact
            for word in ("旧柚", "小柚", "闪传", "旧柚Pro", "导入", "安装", "设置", "版本", "报错", "兼容")
        )
