from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from app.db.models import InteractionLog, MessageLog
from app.db.session import SessionLocal
from app.providers.llm import get_llm_provider
from app.schemas.agent import LanguageEmotionResult, MemoryRelationResult


class MemoryRelationAgent:
    async def run(
        self,
        language: LanguageEmotionResult,
        relationship_id: str = "family-demo",
        run_id: str | None = None,
    ) -> MemoryRelationResult:
        with SessionLocal() as session:
            recent_messages = (
                session.query(MessageLog)
                .filter(MessageLog.relationship_id == relationship_id)
                .order_by(MessageLog.id.desc())
                .limit(10)
                .all()
            )
            recent_touches = (
                session.query(InteractionLog)
                .filter(InteractionLog.relationship_id == relationship_id)
                .order_by(InteractionLog.id.desc())
                .limit(20)
                .all()
            )

        evidence_pack = self._build_evidence_pack(language, recent_messages, recent_touches, relationship_id)
        history_summary = evidence_pack["history_summary"]
        long_term_table = await self._try_llm_long_term_table(evidence_pack, run_id=run_id)
        if long_term_table is None:
            self._log(run_id, "history reasoner using conservative fallback table")
            long_term_table = self._build_fallback_long_term_table(evidence_pack)

        longitudinal, relational = self._flatten_long_term_table(long_term_table)
        event = self._cell_value(language.short_term_table, "A_situational_semantics", "event") or "当前事件"
        emotion = self._cell_value(language.short_term_table, "B_affective_semantics", "momentary_affect") or "当前情绪"

        return MemoryRelationResult(
            longitudinal=longitudinal,
            relational=relational,
            long_term_table=long_term_table,
            memory_card={
                "frontHint": "今天的联系被轻轻记录下来",
                "backSummary": f"本次留言主要与{event}有关，情绪倾向为{emotion}；长期表判断关系趋势为{relational.get('relationship_trend', '未知')}。",
            },
            history_summary=history_summary,
        )

    def _build_evidence_pack(
        self,
        language: LanguageEmotionResult,
        recent_messages: list[MessageLog],
        recent_touches: list[InteractionLog],
        relationship_id: str,
    ) -> dict[str, Any]:
        messages = [
            {
                "message_id": row.message_id,
                "created_at": row.created_at.isoformat(),
                "input_type": row.input_type,
                "transcript_excerpt": row.transcript[:160],
                "short_term_table": row.short_term_table,
            }
            for row in recent_messages
        ]
        interactions = [
            {
                "interaction_id": row.interaction_id,
                "created_at": row.created_at.isoformat(),
                "interaction_type": row.interaction_type,
                "target_id": row.target_id,
                "payload": row.payload,
            }
            for row in recent_touches
        ]
        return {
            "relationship_id": relationship_id,
            "current_short_term_table": language.short_term_table,
            "historical_short_term_tables": messages,
            "interaction_records": interactions,
            "history_summary": {
                "recent_message_count": len(recent_messages),
                "recent_interaction_count": len(recent_touches),
                "message_window": "latest_10_by_relationship",
                "interaction_window": "latest_20_by_relationship",
            },
            "table_questions": self._long_term_questions(),
        }

    async def _try_llm_long_term_table(self, evidence_pack: dict[str, Any], run_id: str | None = None) -> Optional[dict]:
        provider = get_llm_provider()
        if provider is None:
            self._log(run_id, "history reasoner skipped LLM: provider not configured")
            return None

        prompt = f"""
你是 TimeWallpaper 的 History Reasoner / 长期关系填表智能体。
你不能自由写关系总结，也不能生成画面描述。你只根据“当前短期表、历史短期表、交互记录”和表格问题，填写 D/E 长期表。

判断原则：
1. D/E 是长期心理状态和关系趋势，不是细粒度情绪分类。
2. 不要只看当前一条消息，要综合历史窗口和当前数据。
3. 证据不足时选择较保守的状态，并降低 confidence。
4. 每个字段的 value 必须优先从 candidate_values 中选择；如果确实需要组合表达，可以使用 candidate_values 中的短语组合。
5. evidence 必须引用结构化证据中的事件、情绪、意图、期待回应或交互记录，不要编造。
6. 只输出 JSON，不要输出解释。

Evidence Pack:
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
        current = evidence_pack.get("current_short_term_table", {})
        history_summary = evidence_pack.get("history_summary", {})
        message_count = history_summary.get("recent_message_count", 0)
        interaction_count = history_summary.get("recent_interaction_count", 0)
        emotion = self._cell_value(current, "B_affective_semantics", "momentary_affect") or "未知"
        event = self._cell_value(current, "A_situational_semantics", "event") or "未知"
        interaction_frequency = "长时间未联系" if message_count <= 1 else "经常联系" if message_count >= 5 else "偶尔联系"
        social_connection = "联系需求增加" if emotion in ("思念", "悲伤", "焦虑", "期待") else "陪伴稳定"
        fatigue = "活动减少" if event in ("工作", "生病") else "状态稳定"
        trend = "暂时疏远" if interaction_frequency == "长时间未联系" else "稳定"
        warmth = "温暖" if emotion in ("思念", "平静", "愉悦", "期待") else "需要安慰"
        relation_evidence = f"fallback: 最近{message_count}条消息，{interaction_count}次交互"
        current_evidence = f"fallback: 当前事件={event}，当前情绪={emotion}"
        return {
            "tableName": "long_term_relation_table",
            "agentRole": "History Reasoner / 长期关系填表智能体",
            "designBoundary": "只根据历史短期表、交互记录和当前短期表填写长期表；不生成视觉隐喻，不决定画面怎么画。",
            "D_longitudinal_state_semantics": {
                "social_connection_cue": self._cell(social_connection, current_evidence, 0.55),
                "fatigue_vitality_cue": self._cell(fatigue, current_evidence, 0.5),
                "stability_fluctuation": self._cell("状态稳定", relation_evidence, 0.48),
                "recovery_decline_trend": self._cell("恢复稳定" if fatigue == "状态稳定" else "轻微下降", relation_evidence, 0.48),
            },
            "E_relational_semantics": {
                "interaction_frequency": self._cell(interaction_frequency, relation_evidence, 0.6),
                "intimacy_distance": self._cell("稍远" if trend == "暂时疏远" else "近", relation_evidence, 0.5),
                "reciprocity": self._cell("回应失衡" if interaction_count == 0 else "双向回应", relation_evidence, 0.52),
                "emotional_warmth": self._cell(warmth, current_evidence, 0.55),
                "relationship_trend": self._cell(trend, relation_evidence, 0.5),
            },
        }

    def _normalize_long_term_table(self, data: dict) -> dict:
        table = {
            "tableName": "long_term_relation_table",
            "agentRole": "History Reasoner / 长期关系填表智能体",
            "designBoundary": "只根据历史短期表、交互记录和当前短期表填写长期表；不生成视觉隐喻，不决定画面怎么画。",
            "D_longitudinal_state_semantics": {},
            "E_relational_semantics": {},
        }
        defaults = self._build_fallback_long_term_table({"current_short_term_table": {}, "history_summary": {}})
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
