from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.db.models import InteractionLog, MessageLog, RelationshipProfile, RelationshipState
from app.db.session import SessionLocal
from app.providers.llm import get_llm_provider
from app.schemas.agent import LanguageEmotionResult, MemoryRelationResult


class MemoryRelationAgent:
    DIGEST_VERSION = 2
    HISTORY_WINDOW_DAYS = 14
    MAX_RECENT_EMOTIONS = 5
    MAX_SIGNIFICANT_EVENTS = 5
    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
    NEGATIVE_EMOTIONS = {"悲伤", "生气", "焦虑", "疲惫"}

    async def run(
        self,
        language: LanguageEmotionResult,
        relationship_id: str = "family-demo",
        run_id: str | None = None,
        prefetched_context: dict[str, Any] | None = None,
        user_id: str | None = None,
        is_voice: bool = True,
    ) -> MemoryRelationResult:
        """Compatibility wrapper: reason from history, then attach this message locally."""
        del is_voice
        memory = await self.run_history(
            relationship_id=relationship_id,
            run_id=run_id,
            prefetched_context=prefetched_context,
        )
        return self.attach_current_short(memory, language, user_id=user_id)

    async def run_history(
        self,
        relationship_id: str = "family-demo",
        run_id: str | None = None,
        prefetched_context: dict[str, Any] | None = None,
    ) -> MemoryRelationResult:
        """Always run long-term reasoning from persisted relationship history only."""
        context = prefetched_context or await self.load_context(relationship_id)
        now = datetime.now(self.SHANGHAI_TZ)
        history_digest = self._build_history_digest(context, now=now)
        evidence_pack = self._build_history_evidence_pack(
            relationship_id=relationship_id,
            history_digest=history_digest,
            previous_long_term_table=context.get("previousLongTermTable") or {},
        )
        self._log(run_id, "history reasoner started from persisted relationship history")
        long_term_table = await self._try_llm_long_term_table(evidence_pack, run_id=run_id)
        reasoning_mode = "llm"
        if long_term_table is None:
            self._log(run_id, "history reasoner using conservative fallback table")
            long_term_table = self._build_fallback_long_term_table(evidence_pack)
            reasoning_mode = "fallback"

        history_digest.update(
            {
                "lastFullReasonedDate": now.date().isoformat(),
                "lastFullReasonedAt": now.isoformat(),
                "lastReasoningMode": reasoning_mode,
            }
        )
        history_digest["previousLongTermValues"] = self._compact_long_term_table(long_term_table)

        longitudinal, relational = self._flatten_long_term_table(long_term_table)
        historical_short = history_digest.get("lastShortTermValues") or {}
        event = historical_short.get("event") or "历史互动"
        emotion = historical_short.get("emotion") or "未知"

        return MemoryRelationResult(
            longitudinal=longitudinal,
            relational=relational,
            long_term_table=long_term_table,
            memory_card={
                "frontHint": "今天的联系被轻轻记录下来",
                "backSummary": f"本次留言主要与{event}有关，情绪倾向为{emotion}；长期表判断关系趋势为{relational.get('relationship_trend', '未知')}。",
            },
            history_summary=history_digest,
        )

    def attach_current_short(
        self,
        memory: MemoryRelationResult,
        language: LanguageEmotionResult,
        *,
        user_id: str | None = None,
    ) -> MemoryRelationResult:
        """Merge the reflected short table into the digest without another LLM call."""
        now = datetime.now(self.SHANGHAI_TZ)
        current_short = self._compact_short_table(language.short_term_table)
        current_short["date"] = now.date().isoformat()
        digest = deepcopy(memory.history_summary)

        emotions = list((digest.get("emotionTrend") or {}).get("recent") or [])
        if current_short.get("emotion"):
            emotions.append(str(current_short["emotion"]))
        emotions = emotions[-self.MAX_RECENT_EMOTIONS :]

        events = list(digest.get("significantEvents") or [])
        emotion = str(current_short.get("emotion") or "")
        intensity = str(current_short.get("intensity") or "")
        if emotion in self.NEGATIVE_EMOTIONS or intensity in {"明显", "强烈"}:
            events.append(
                {
                    "date": current_short["date"],
                    "event": current_short.get("event") or "当前事件",
                    "emotion": emotion or "未知",
                    "intensity": intensity or "未知",
                    "intent": current_short.get("intent") or "未知",
                    "desiredResponse": current_short.get("desiredResponse") or "未知",
                }
            )

        stats = deepcopy(digest.get("rollingStats") or {})
        stats["messageCount"] = int(stats.get("messageCount") or 0) + 1
        participants = digest.get("participants") or {}
        if user_id and user_id == participants.get("parentUserId"):
            stats["parentMessageCount"] = int(stats.get("parentMessageCount") or 0) + 1
        elif user_id and user_id == participants.get("childUserId"):
            stats["childMessageCount"] = int(stats.get("childMessageCount") or 0) + 1
        stats["daysSinceLastContact"] = 0

        digest.update(
            {
                "lastObservedEmotion": current_short.get("emotion") or "未知",
                "lastContactAt": now.isoformat(),
                "lastShortTermValues": current_short,
                "rollingStats": stats,
                "emotionTrend": {
                    "recent": emotions,
                    "dominant": self._dominant_emotion(emotions),
                    "consecutiveNegativeCount": self._consecutive_negative_count(emotions),
                },
                "significantEvents": events[-self.MAX_SIGNIFICANT_EVENTS :],
            }
        )

        event = current_short.get("event") or "当前事件"
        emotion = current_short.get("emotion") or "当前情绪"
        return memory.model_copy(
            update={
                "history_summary": digest,
                "memory_card": {
                    "frontHint": "今天的联系被轻轻记录下来",
                    "backSummary": f"本次留言主要与{event}有关，情绪倾向为{emotion}；长期表判断关系趋势为{memory.relational.get('relationship_trend', '未知')}。",
                },
            }
        )

    async def load_context(self, relationship_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._load_context_sync, relationship_id)

    def _load_context_sync(self, relationship_id: str) -> dict[str, Any]:
        cutoff = datetime.utcnow() - timedelta(days=self.HISTORY_WINDOW_DAYS)
        with SessionLocal() as session:
            state = (
                session.query(RelationshipState)
                .filter(RelationshipState.relationship_id == relationship_id)
                .one_or_none()
            )
            relationship = (
                session.query(RelationshipProfile)
                .filter(RelationshipProfile.relationship_id == relationship_id)
                .one_or_none()
            )
            recent_messages = (
                session.query(MessageLog)
                .filter(
                    MessageLog.relationship_id == relationship_id,
                    MessageLog.created_at >= cutoff,
                )
                .order_by(MessageLog.id.desc())
                .limit(100)
                .all()
            )
            recent_touches = (
                session.query(InteractionLog)
                .filter(
                    InteractionLog.relationship_id == relationship_id,
                    InteractionLog.created_at >= cutoff,
                )
                .order_by(InteractionLog.id.desc())
                .limit(200)
                .all()
            )

            messages = [
                {
                    "messageId": row.message_id,
                    "userId": row.user_id,
                    "createdAt": row.created_at.isoformat(),
                    "shortTermValues": {
                        **self._compact_short_table(row.short_term_table),
                        "date": row.created_at.date().isoformat(),
                    },
                }
                for row in recent_messages
            ]
            return {
                "relationshipId": relationship_id,
                "previousLongTermTable": deepcopy(state.long_term_table) if state else {},
                "previousHistoryDigest": deepcopy(state.history_summary) if state else {},
                "previousLongitudinal": deepcopy(state.longitudinal) if state else {},
                "previousRelational": deepcopy(state.relational) if state else {},
                "stateUpdatedAt": state.updated_at.isoformat() if state else "",
                "recentMessages": messages,
                "recentInteractionCount": len(recent_touches),
                "lastContactAt": messages[0]["createdAt"] if messages else "",
                "parentUserId": relationship.parent_user_id if relationship else "",
                "childUserId": relationship.child_user_id if relationship else "",
            }

    def _build_history_digest(
        self,
        context: dict[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        previous = context.get("previousHistoryDigest") or {}
        historical = list(reversed(context.get("recentMessages") or []))
        historical_values = [item.get("shortTermValues") or {} for item in historical]
        recent_emotions = [
            str(item.get("emotion"))
            for item in historical_values
            if item.get("emotion")
        ][-self.MAX_RECENT_EMOTIONS :]
        if not recent_emotions:
            recent_emotions = list((previous.get("emotionTrend") or {}).get("recent") or [])[
                -self.MAX_RECENT_EMOTIONS :
            ]

        significant_events = []
        for values in historical_values:
            emotion = str(values.get("emotion") or "")
            intensity = str(values.get("intensity") or "")
            if emotion not in self.NEGATIVE_EMOTIONS and intensity not in {"明显", "强烈"}:
                continue
            significant_events.append(
                {
                    "date": str(values.get("date") or now.date().isoformat()),
                    "event": values.get("event") or "当前事件",
                    "emotion": emotion or "未知",
                    "intensity": intensity or "未知",
                    "intent": values.get("intent") or "未知",
                    "desiredResponse": values.get("desiredResponse") or "未知",
                }
            )
        if not significant_events:
            significant_events = list(previous.get("significantEvents") or [])
        significant_events = significant_events[-self.MAX_SIGNIFICANT_EVENTS :]

        parent_user_id = str(context.get("parentUserId") or "")
        child_user_id = str(context.get("childUserId") or "")
        parent_count = sum(1 for item in historical if item.get("userId") == parent_user_id)
        child_count = sum(1 for item in historical if item.get("userId") == child_user_id)
        latest = historical_values[-1] if historical_values else (previous.get("lastShortTermValues") or {})
        return {
            "version": self.DIGEST_VERSION,
            "scope": "relationship",
            "relationshipId": context.get("relationshipId") or "",
            "participants": {
                "parentUserId": parent_user_id,
                "childUserId": child_user_id,
            },
            "lastObservedEmotion": latest.get("emotion") or previous.get("lastObservedEmotion") or "未知",
            "lastContactAt": context.get("lastContactAt") or previous.get("lastContactAt") or "",
            "lastShortTermValues": latest,
            "rollingStats": {
                "windowDays": self.HISTORY_WINDOW_DAYS,
                "messageCount": len(historical),
                "parentMessageCount": parent_count,
                "childMessageCount": child_count,
                "interactionCount": int(context.get("recentInteractionCount") or 0),
                "daysSinceLastContact": self._days_since(context.get("lastContactAt"), now),
            },
            "emotionTrend": {
                "recent": recent_emotions,
                "dominant": self._dominant_emotion(recent_emotions),
                "consecutiveNegativeCount": self._consecutive_negative_count(recent_emotions),
            },
            "significantEvents": significant_events,
        }

    def _build_history_evidence_pack(
        self,
        *,
        relationship_id: str,
        history_digest: dict[str, Any],
        previous_long_term_table: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "relationshipId": relationship_id,
            "historyDigest": {
                "rollingStats": history_digest.get("rollingStats") or {},
                "emotionTrend": history_digest.get("emotionTrend") or {},
                "significantEvents": history_digest.get("significantEvents") or [],
                "lastShortTermValues": history_digest.get("lastShortTermValues") or {},
            },
            "previousLongTermValues": self._compact_long_term_table(previous_long_term_table),
            "tableQuestions": self._long_term_questions(),
        }

    async def _try_llm_long_term_table(self, evidence_pack: dict[str, Any], run_id: str | None = None) -> Optional[dict]:
        provider = get_llm_provider()
        if provider is None:
            self._log(run_id, "history reasoner skipped LLM: provider not configured")
            return None

        prompt = f"""
你是 TimeWallpaper 的 History Reasoner / 长期关系填表智能体。
你不能自由写关系总结，也不能生成画面描述。你只根据“数据库中的压缩历史摘要、上一版长期表值”和表格问题，填写 D/E 长期表。

判断原则：
1. D/E 是长期心理状态和关系趋势，不是细粒度情绪分类。
2. 当前消息的短期表尚未生成，不能推测或引用当前消息；只综合 HistoryDigest 和上一版长期状态。
3. 证据不足时选择较保守的状态，并降低 confidence。
4. 每个字段的 value 必须优先从 candidate_values 中选择；如果确实需要组合表达，可以使用 candidate_values 中的短语组合。
5. evidence 必须引用结构化证据中的事件、情绪、意图、期待回应或交互记录，不要编造。
6. 只输出 JSON，不要输出解释。

Compressed Evidence Pack:
{json.dumps(evidence_pack, ensure_ascii=False)}

输出 JSON 格式必须是：
{{
  "D_longitudinal_state_semantics": {{
    "social_connection_cue": {{"value": "...", "evidence": "...", "confidence": 0.0}},
    "fatigue_vitality_cue": {{"value": "...", "evidence": "...", "confidence": 0.0}},
    "stability_fluctuation": {{"value": "...", "evidence": "...", "confidence": 0.0}},
    "recovery_decline_trend": {{"value": "...", "evidence": "...", "confidence": 0.0}}
  }},
  "E_relational_semantics": {{
    "interaction_frequency": {{"value": "...", "evidence": "...", "confidence": 0.0}},
    "intimacy_distance": {{"value": "...", "evidence": "...", "confidence": 0.0}},
    "reciprocity": {{"value": "...", "evidence": "...", "confidence": 0.0}},
    "emotional_warmth": {{"value": "...", "evidence": "...", "confidence": 0.0}},
    "relationship_trend": {{"value": "...", "evidence": "...", "confidence": 0.0}}
  }}
}}
""".strip()
        try:
            self._log(run_id, "history reasoner LLM request started")
            text = await provider.chat(
                prompt,
                system="你只输出可解析 JSON。你根据结构化历史证据填写长期关系表。",
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=90,
            )
            data = self._loads_json(text)
            table = self._normalize_long_term_table(data)
            self._log(run_id, "history reasoner LLM response parsed")
            return table
        except (KeyError, TypeError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            self._log(run_id, f"history reasoner LLM failed, fallback={type(exc).__name__}: {exc}")
            return None

    def _build_fallback_long_term_table(self, evidence_pack: dict[str, Any]) -> dict:
        history_digest = evidence_pack.get("historyDigest", {})
        rolling_stats = history_digest.get("rollingStats", {})
        latest = history_digest.get("lastShortTermValues", {})
        message_count = int(rolling_stats.get("messageCount") or 0)
        interaction_count = int(rolling_stats.get("interactionCount") or 0)
        emotion = latest.get("emotion") or "未知"
        event = latest.get("event") or "未知"
        interaction_frequency = "长时间未联系" if message_count <= 1 else "经常联系" if message_count >= 5 else "偶尔联系"
        social_connection = "联系需求增加" if emotion in ("思念", "悲伤", "焦虑", "期待") else "陪伴稳定"
        fatigue = "活动减少" if event in ("工作", "生病") else "状态稳定"
        trend = "暂时疏远" if interaction_frequency == "长时间未联系" else "稳定"
        warmth = "温暖" if emotion in ("思念", "平静", "愉悦", "期待") else "需要安慰"
        relation_evidence = f"fallback: 最近{message_count}条消息，{interaction_count}次交互"
        history_evidence = f"fallback: 最近历史事件={event}，最近历史情绪={emotion}"
        return {
            "tableName": "long_term_relation_table",
            "agentRole": "History Reasoner / 长期关系填表智能体",
            "designBoundary": "只根据数据库中的历史短期表、交互记录和上一版长期状态填写长期表；不读取当前短期表，不生成视觉隐喻。",
            "D_longitudinal_state_semantics": {
                "social_connection_cue": self._cell(social_connection, history_evidence, 0.55),
                "fatigue_vitality_cue": self._cell(fatigue, history_evidence, 0.5),
                "stability_fluctuation": self._cell("状态稳定", relation_evidence, 0.48),
                "recovery_decline_trend": self._cell("恢复稳定" if fatigue == "状态稳定" else "轻微下降", relation_evidence, 0.48),
            },
            "E_relational_semantics": {
                "interaction_frequency": self._cell(interaction_frequency, relation_evidence, 0.6),
                "intimacy_distance": self._cell("稍远" if trend == "暂时疏远" else "近", relation_evidence, 0.5),
                "reciprocity": self._cell("回应失衡" if interaction_count == 0 else "双向回应", relation_evidence, 0.52),
                "emotional_warmth": self._cell(warmth, history_evidence, 0.55),
                "relationship_trend": self._cell(trend, relation_evidence, 0.5),
            },
        }

    def _normalize_long_term_table(self, data: dict) -> dict:
        table = {
            "tableName": "long_term_relation_table",
            "agentRole": "History Reasoner / 长期关系填表智能体",
            "designBoundary": "只根据数据库中的历史短期表、交互记录和上一版长期状态填写长期表；不读取当前短期表，不生成视觉隐喻。",
            "D_longitudinal_state_semantics": {},
            "E_relational_semantics": {},
        }
        defaults = self._build_fallback_long_term_table(
            {
                "historyDigest": {"rollingStats": {}, "lastShortTermValues": {}},
            }
        )
        for section_key, fields in {
            "D_longitudinal_state_semantics": [
                "social_connection_cue",
                "fatigue_vitality_cue",
                "stability_fluctuation",
                "recovery_decline_trend",
            ],
            "E_relational_semantics": [
                "interaction_frequency",
                "intimacy_distance",
                "reciprocity",
                "emotional_warmth",
                "relationship_trend",
            ],
        }.items():
            section = data.get(section_key, {})
            for field in fields:
                cell = section.get(field, {}) if isinstance(section, dict) else {}
                fallback_cell = defaults[section_key][field]
                table[section_key][field] = self._normalize_cell(cell, fallback_cell)
        return table

    def _normalize_cell(self, cell: Any, fallback_cell: dict) -> dict:
        if not isinstance(cell, dict):
            return fallback_cell
        value = cell.get("value") or fallback_cell["value"]
        evidence = cell.get("evidence") or fallback_cell["evidence"]
        confidence = cell.get("confidence", fallback_cell["confidence"])
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = fallback_cell["confidence"]
        confidence = max(0.0, min(1.0, confidence))
        return self._cell(value, str(evidence), confidence)

    def _compact_short_table(self, table: dict[str, Any]) -> dict[str, Any]:
        return {
            "event": self._cell_value(table, "A_situational_semantics", "event") or "未知",
            "scene": self._cell_value(table, "A_situational_semantics", "scene") or "未知",
            "time": self._cell_value(table, "A_situational_semantics", "time") or "未知",
            "subject": self._cell_value(table, "A_situational_semantics", "subject") or [],
            "emotion": self._cell_value(table, "B_affective_semantics", "momentary_affect") or "未知",
            "intensity": self._cell_value(table, "B_affective_semantics", "affective_intensity") or "未知",
            "ambiguity": self._cell_value(table, "B_affective_semantics", "affective_ambiguity") or "未知",
            "intent": self._cell_value(table, "C_communicative_semantics", "intent_type") or "未知",
            "desiredResponse": self._cell_value(table, "C_communicative_semantics", "desired_response") or "未知",
            "disclosureDepth": self._cell_value(table, "C_communicative_semantics", "disclosure_depth") or "未知",
        }

    def _compact_long_term_table(self, table: dict[str, Any]) -> dict[str, Any]:
        if not table:
            return {}
        return {
            "socialConnectionCue": self._cell_value(table, "D_longitudinal_state_semantics", "social_connection_cue"),
            "fatigueVitalityCue": self._cell_value(table, "D_longitudinal_state_semantics", "fatigue_vitality_cue"),
            "stabilityFluctuation": self._cell_value(table, "D_longitudinal_state_semantics", "stability_fluctuation"),
            "recoveryDeclineTrend": self._cell_value(table, "D_longitudinal_state_semantics", "recovery_decline_trend"),
            "interactionFrequency": self._cell_value(table, "E_relational_semantics", "interaction_frequency"),
            "intimacyDistance": self._cell_value(table, "E_relational_semantics", "intimacy_distance"),
            "reciprocity": self._cell_value(table, "E_relational_semantics", "reciprocity"),
            "emotionalWarmth": self._cell_value(table, "E_relational_semantics", "emotional_warmth"),
            "relationshipTrend": self._cell_value(table, "E_relational_semantics", "relationship_trend"),
        }

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(self.SHANGHAI_TZ)

    def _days_since(self, value: Any, now: datetime) -> int:
        parsed = self._parse_datetime(value)
        if parsed is None:
            return 0
        return max(0, (now.date() - parsed.date()).days)

    def _dominant_emotion(self, emotions: list[str]) -> str:
        if not emotions:
            return "未知"
        return max(
            set(emotions),
            key=lambda emotion: (emotions.count(emotion), max(i for i, item in enumerate(emotions) if item == emotion)),
        )

    def _consecutive_negative_count(self, emotions: list[str]) -> int:
        count = 0
        for emotion in reversed(emotions):
            if emotion not in self.NEGATIVE_EMOTIONS:
                break
            count += 1
        return count

    def _flatten_long_term_table(self, table: dict) -> tuple[dict, dict]:
        longitudinal = {
            field: self._cell_value(table, "D_longitudinal_state_semantics", field)
            for field in [
                "social_connection_cue",
                "fatigue_vitality_cue",
                "stability_fluctuation",
                "recovery_decline_trend",
            ]
        }
        relational = {
            field: self._cell_value(table, "E_relational_semantics", field)
            for field in [
                "interaction_frequency",
                "intimacy_distance",
                "reciprocity",
                "emotional_warmth",
                "relationship_trend",
            ]
        }
        return longitudinal, relational

    def _long_term_questions(self) -> list[dict[str, Any]]:
        return [
            {
                "section": "D_longitudinal_state_semantics",
                "field": "social_connection_cue",
                "question": "双方联系如何？",
                "candidate_values": ["陪伴减少", "联系需求增加", "独处时间变长", "群体活动变少", "陪伴稳定"],
            },
            {
                "section": "D_longitudinal_state_semantics",
                "field": "fatigue_vitality_cue",
                "question": "双方距离如何？",
                "candidate_values": ["活动减少", "动作变慢", "休息增多", "活力恢复", "状态稳定"],
            },
            {
                "section": "D_longitudinal_state_semantics",
                "field": "stability_fluctuation",
                "question": "关系是否有来有回？",
                "candidate_values": ["状态稳定", "情绪波动", "生活节奏紊乱", "恢复稳定"],
            },
            {
                "section": "D_longitudinal_state_semantics",
                "field": "recovery_decline_trend",
                "question": "长期心理状态正在恢复、下降还是停滞？",
                "candidate_values": ["轻微下降", "修复中", "恢复稳定", "停滞"],
            },
            {
                "section": "E_relational_semantics",
                "field": "interaction_frequency",
                "question": "双方联系如何？",
                "candidate_values": ["经常联系", "偶尔联系", "长时间未联系", "突然密集"],
            },
            {
                "section": "E_relational_semantics",
                "field": "intimacy_distance",
                "question": "双方距离如何？",
                "candidate_values": ["近", "稍远", "远", "共享空间变大", "边界感增强"],
            },
            {
                "section": "E_relational_semantics",
                "field": "reciprocity",
                "question": "关系是否有来有回？",
                "candidate_values": ["双向回应", "回应失衡", "单向表达较多", "互动恢复"],
            },
            {
                "section": "E_relational_semantics",
                "field": "emotional_warmth",
                "question": "关系是否温暖？",
                "candidate_values": ["温暖", "疏离", "稳定", "冷淡", "需要安慰", "温暖但克制"],
            },
            {
                "section": "E_relational_semantics",
                "field": "relationship_trend",
                "question": "关系正在如何变化？",
                "candidate_values": ["靠近", "疏远", "修复", "停滞", "稳定"],
            },
        ]

    def _loads_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise
            return json.loads(match.group(0))

    def _cell_value(self, table: dict, section: str, field: str) -> Any:
        section_value = table.get(section, {})
        if not isinstance(section_value, dict):
            return None
        cell = section_value.get(field, {})
        if isinstance(cell, dict):
            return cell.get("value")
        return None

    def _cell(self, value, evidence: str, confidence: float) -> dict:
        return {
            "value": value,
            "evidence": evidence,
            "confidence": round(confidence, 2),
        }

    def _log(self, run_id: str | None, message: str) -> None:
        prefix = f"[agent-run:{run_id}]" if run_id else "[agent-run]"
        print(f"{prefix} {message}", flush=True)
