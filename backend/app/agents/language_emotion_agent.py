from __future__ import annotations

import json
import re
from typing import Optional

import httpx

from app.providers.llm import get_llm_provider
from app.schemas.agent import LanguageEmotionResult
from app.agents.script_reflection_agent import ScriptReflectionAgent
from app.services.audio_understanding_tool import analyze_audio_affect
from app.services.chatbox_asr_service import transcribe_chatbox_audio
from app.services.user_context import DEFAULT_CHILD_USER_ID, DEFAULT_PARENT_USER_ID


class LanguageEmotionAgent:
    def __init__(self) -> None:
        self.reflection_agent = ScriptReflectionAgent()

    async def run(
        self,
        audio: bytes,
        content_type: str,
        filename: str,
        run_id: str | None = None,
        user_id: str | None = None,
        relationship_id: str | None = None,
    ) -> LanguageEmotionResult:
        self._log(run_id, "ChatBox ASR started")
        asr = await transcribe_chatbox_audio(audio, filename=filename, content_type=content_type, run_id=run_id)
        self._log(run_id, "ChatBox ASR done")
        transcript = asr["transcript"]

        voice_affect = None
        audio_affect_raw = None
        try:
            self._log(run_id, "Audio Understanding Tool started")
            audio_affect = await analyze_audio_affect(
                audio,
                filename=filename,
                content_type=content_type,
                transcript=transcript,
                run_id=run_id,
            )
            self._log(run_id, "Audio Understanding Tool done")
            voice_affect = audio_affect.get("voiceAffect")
            audio_affect_raw = audio_affect.get("raw")
        except Exception as exc:
            self._log(run_id, f"Audio Understanding Tool skipped: {type(exc).__name__}: {exc}")

        return await self.run_text(
            transcript,
            asr_raw={
                "asr": asr.get("raw"),
                "audio_understanding": audio_affect_raw,
            },
            voice_affect=voice_affect,
            run_id=run_id,
            user_id=user_id,
            relationship_id=relationship_id,
        )

    async def run_text(
        self,
        transcript: str,
        asr_raw: dict | None = None,
        voice_affect: dict | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        relationship_id: str | None = None,
    ) -> LanguageEmotionResult:
        speaker_context = self._speaker_context(user_id, relationship_id)
        voice_affect = voice_affect or self._voice_affect_from_raw(asr_raw)
        llm_result = await self._try_llm_understanding(transcript, asr_raw, voice_affect, speaker_context, run_id=run_id)
        if llm_result is not None:
            return llm_result
        self._log(run_id, "language understanding using rule fallback")
        return self._rule_understanding(transcript, asr_raw, speaker_context, voice_affect)

    async def _try_llm_understanding(
        self,
        transcript: str,
        asr_raw: dict | None,
        voice_affect: dict | None,
        speaker_context: dict,
        run_id: str | None = None,
    ) -> Optional[LanguageEmotionResult]:
        provider = get_llm_provider()
        if provider is None:
            self._log(run_id, "language understanding skipped LLM: provider not configured")
            return None

        prompt = f"""
请按短期感知智能体闭环分析一段家庭异步沟通语音转写，并只输出 JSON，不要输出解释文字。
Gist Layer 与 Semantic Extractor 是并行关系：二者都直接从原始文本和端侧上下文出发，不要让其中一个覆盖另一个。
Question-Based Table Filler 再综合整体大意、原文语义线索和端侧身份，填写短期 A/B/C 表。

转写文本：
{transcript}

音频理解结果：
{json.dumps({"voiceAffect": voice_affect or {}}, ensure_ascii=False)}

端侧上下文：
{json.dumps(speaker_context, ensure_ascii=False)}

输出字段必须是：
{{
  "gistLayer": {{
    "one_sentence_gist": "一句话说明这条消息整体在说什么",
    "speaker_state": "说话者当前状态",
    "life_rhythm": "清晨|午后|黄昏|深夜|周末|节日|季节|日常时刻等生活节奏状态；可由文本稳定推断",
    "communication_motive": "为什么留下这条信息",
    "confidence": 0.0
  }},
  "semanticExtractor": {{
    "events": [{{"value": "事件或活动短语", "evidence": "原文证据或推断说明", "source": "explicit|inferred|default", "confidence": 0.0}}],
    "scenes": [{{"value": "地点/场景", "evidence": "原文证据或推断说明", "source": "explicit|inferred|default", "confidence": 0.0}}],
    "times": [{{"value": "时间或生活节奏状态", "evidence": "原文证据或推断说明", "source": "explicit|inferred|default", "confidence": 0.0}}],
    "subjects": [{{"value": "人物/群体/端侧身份", "evidence": "原文证据或端侧规则", "source": "explicit|inferred|default", "confidence": 0.0}}],
    "objects": [{{"value": "物件/活动对象/组织/感官线索/生活痕迹", "evidence": "原文证据或推断说明", "source": "explicit|inferred|default", "confidence": 0.0}}],
    "affect_cues": [{{"value": "情绪、身体状态或语气线索", "evidence": "原文证据或推断说明", "source": "explicit|inferred|default", "confidence": 0.0}}],
    "communication_cues": [{{"value": "沟通意图线索", "evidence": "原文证据或推断说明", "source": "explicit|inferred|default", "confidence": 0.0}}]
  }},
  "questionAnswers": {{
    "event": {{"question": "最近发生了什么？", "answer": "回答", "value": "填表值", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "scene": {{"question": "发生在哪里？对方在哪里？", "answer": "回答", "value": "填表值", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "time": {{"question": "何时发生？对方当前生活节奏如何？", "answer": "回答", "value": "填表值", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "subject": {{"question": "与谁有关？谁在场？", "answer": "回答", "value": ["人物"], "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "object": {{"question": "哪些物件提示了这件事？", "answer": "回答", "value": ["物件/活动对象/生活线索"], "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "momentary_affect": {{"question": "此刻感受如何？", "answer": "回答", "value": "愉悦|平静|悲伤|焦虑|思念|期待|疲惫", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "affective_intensity": {{"question": "这种感受有多强？", "answer": "回答", "value": "轻微|明显|强烈|波动", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "affective_ambiguity": {{"question": "这个状态是否容易理解？", "answer": "回答", "value": "明确|含混|难以判断|需要上下文", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "intent_type": {{"question": "为什么留下这条信息？", "answer": "回答", "value": "分享生活|表达思念|寻求安慰|倾诉情绪|期待回应", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "desired_response": {{"question": "希望对方怎样回应？", "answer": "回答", "value": "看见即可|轻触回应|留言|回忆|进一步聊天|安慰或轻触回应", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}},
    "disclosure_depth": {{"question": "这条信息表达得多深？", "answer": "回答", "value": "日常分享|情绪透露|脆弱表达|求助", "evidence": "证据", "source": "explicit|inferred|default", "confidence": 0.0}}
  }},
  "shortTermTable": {{
    "A_situational_semantics": {{}},
    "B_affective_semantics": {{}},
    "C_communicative_semantics": {{}}
  }},
  "reply": "一句温柔、简短的中文回应"
}}

规则：
- 并行完成整体大意理解和原文语义线索抽取，再结合二者按 Communication Question 填表。
- Gist Layer 负责整体语义：这句话总体在说什么、生活节奏是什么、说话者为什么留下这条信息。
- Semantic Extractor 负责忠实抽取原文线索：事件、场景、时间、人物、物件/活动对象、情绪线索和沟通线索；不要被 Gist 的概括替代。
- Question-Based Table Filler 负责融合：如果 Gist 和 Extractor 有冲突，优先保留原文明确线索，再用 Gist 做合理推断。
- 音频理解结果只用于辅助 B 情绪状态语义：momentary_affect、affective_intensity、affective_ambiguity。不要让语气线索改写 A 情境事实或 C 沟通意图的原文证据。
- 当文本情绪线索和 voiceAffect 不一致时：文本明确表达优先；文本含糊时可采用 voiceAffect，但 evidence 必须写明“音频语气线索”。
- Time 字段不是纯时间戳，而是“何时发生 + 当前生活节奏状态”；可由文本稳定推断为清晨、午后、黄昏、深夜、周末、节日、季节或日常时刻。
- Scene 字段是事件发生地、主要活动空间或稳定推断场景，不要用默认家庭场景覆盖原文场景。
- Object 字段保留具体物件、活动对象、组织、感官线索或生活痕迹；不要自动填无证据的装饰物。
- 文本省略说话主体时，subject 必须包含端侧上下文的 speaker_label。
- 感知层只做事实、实体、事件、情绪和沟通意图理解；不要输出视觉隐喻，不要说应该怎么画。
- shortTermTable 每个字段必须是 {{"value": ..., "evidence": "...", "confidence": 0.0, "source": "explicit|inferred|default"}}。
""".strip()
        try:
            self._log(run_id, "language understanding LLM request started")
            text = await provider.chat(
                prompt,
                system="你是 CHI 家庭陪伴系统的语言与情感理解智能体，只输出可解析 JSON。",
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            data = self._loads_json(text)
            self._log(run_id, "language understanding LLM response parsed")
            short_term_table = self._normalize_short_term_table(
                data.get("shortTermTable") or data.get("short_term_table") or {},
                data.get("questionAnswers") or data.get("question_answers") or {},
                transcript,
                speaker_context,
            )
            initial_short_term_table = json.loads(json.dumps(short_term_table, ensure_ascii=False))
            analysis_context = {
                "gistLayer": data.get("gistLayer") or data.get("gist_layer") or {},
                "semanticExtractor": data.get("semanticExtractor") or data.get("semantic_extractor") or {},
                "questionAnswers": data.get("questionAnswers") or data.get("question_answers") or {},
            }
            short_term_table, reflection = await self.reflection_agent.run(
                transcript=transcript,
                initial_table=short_term_table,
                speaker_context=speaker_context,
                run_id=run_id,
            )
            situation, emotion, communication = self._payloads_from_short_loop(short_term_table, analysis_context, transcript, voice_affect)
            return LanguageEmotionResult(
                transcript=transcript,
                emotion=emotion,
                situation=situation,
                communication=communication,
                short_term_table=short_term_table,
                reply=data.get("reply") or "我听到了，这条近况会被整理成今天的时间壁纸。",
                raw={
                    "asr": asr_raw,
                    "audio_understanding": {"voiceAffect": voice_affect or {}},
                    "llm": {"provider": "openai_compatible", "parsed": data},
                    "speaker_context": speaker_context,
                    "gist_layer": analysis_context["gistLayer"],
                    "semantic_extractor": analysis_context["semanticExtractor"],
                    "question_answers": analysis_context["questionAnswers"],
                    "initial_short_term_table": initial_short_term_table,
                    "reflection": reflection,
                },
            )
        except (KeyError, TypeError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            self._log(run_id, f"language understanding LLM failed, fallback={type(exc).__name__}: {exc}")
            return None

    def _loads_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise
            return json.loads(match.group(0))

    def _normalize_short_term_table(
        self,
        table: dict,
        question_answers: dict,
        transcript: str,
        speaker_context: dict,
    ) -> dict:
        normalized = {
            "tableName": "short_term_semantic_table",
            "agentRole": "Script Analyzer / 短期语义填表智能体",
            "designBoundary": "只做文本事实、实体、事件、情绪和沟通意图填表；不生成视觉隐喻，不决定画面怎么画。",
            "loopStructure": "Parallel(Gist Layer, Semantic Extractor) -> Question-Based Table Filler -> Reflection Agent -> Final Short-Term Table",
            "A_situational_semantics": {},
            "B_affective_semantics": {},
            "C_communicative_semantics": {},
        }
        table = table or {}
        for key in ("tableName", "agentRole", "designBoundary"):
            if table.get(key):
                normalized[key] = table[key]

        field_map = {
            "A_situational_semantics": ["event", "scene", "time", "subject", "object"],
            "B_affective_semantics": ["momentary_affect", "affective_intensity", "affective_ambiguity"],
            "C_communicative_semantics": ["intent_type", "desired_response", "disclosure_depth"],
        }
        for section, fields in field_map.items():
            raw_section = table.get(section, {}) if isinstance(table.get(section, {}), dict) else {}
            for field in fields:
                raw_cell = raw_section.get(field)
                answer_cell = question_answers.get(field) if isinstance(question_answers, dict) else None
                normalized[section][field] = self._normalize_cell(
                    raw_cell if not self._is_empty_cell(raw_cell) else answer_cell,
                    fallback_value=self._fallback_question_value(field, transcript, speaker_context),
                    fallback_evidence=self._fallback_question_evidence(field, transcript, speaker_context),
                )

        return self._complete_short_term_table(normalized, transcript, speaker_context)

    def _normalize_cell(self, cell, fallback_value, fallback_evidence: str) -> dict:
        if isinstance(cell, dict):
            value = cell.get("value")
            evidence = cell.get("evidence") or cell.get("answer") or fallback_evidence
            confidence = cell.get("confidence", 0.6)
            source = cell.get("source") or "inferred"
            if not self._is_empty(value):
                return {
                    "value": value,
                    "evidence": evidence,
                    "confidence": round(float(confidence or 0.6), 2),
                    "source": source,
                }
        return {
            "value": fallback_value,
            "evidence": fallback_evidence,
            "confidence": 0.35,
            "source": "default",
        }

    def _is_empty_cell(self, cell) -> bool:
        if not isinstance(cell, dict):
            return True
        return self._is_empty(cell.get("value"))

    def _fallback_question_value(self, field: str, transcript: str, speaker_context: dict):
        if field == "event":
            return self._fallback_event_value(transcript, speaker_context)
        if field == "scene":
            return self._fallback_scene_value(transcript, speaker_context)
        if field == "time":
            return self._infer_time(transcript)
        if field == "subject":
            return [self._fallback_subject_value(transcript, speaker_context)]
        if field == "object":
            return [self._fallback_object_value(transcript, speaker_context)]
        if field == "momentary_affect":
            return "平静"
        if field == "affective_intensity":
            return "轻微"
        if field == "affective_ambiguity":
            return "需要上下文"
        if field == "intent_type":
            return "分享生活"
        if field == "desired_response":
            return "看见即可"
        if field == "disclosure_depth":
            return "日常分享"
        return ""

    def _fallback_question_evidence(self, field: str, transcript: str, speaker_context: dict) -> str:
        if field == "subject":
            return f"端侧硬规则/文本线索：人物对象类别={self._fallback_subject_value(transcript, speaker_context)}"
        return f"表格枚举兜底：Communication Question 未得到明确回答，只从 Values/States 中选取；原文片段={transcript[:60]}"

    def _payloads_from_short_loop(
        self,
        short_term_table: dict,
        analysis_context: dict,
        transcript: str,
        voice_affect: dict | None = None,
    ) -> tuple[dict, dict, dict]:
        raw_extractor = analysis_context.get("semanticExtractor") if isinstance(analysis_context, dict) else {}
        extractor = raw_extractor if isinstance(raw_extractor, dict) else {}
        situation = {
            "explicit_facts": [transcript],
            "event_activity_type": self._table_cell_value(short_term_table, "A_situational_semantics", "event"),
            "scene_place_type": self._table_cell_value(short_term_table, "A_situational_semantics", "scene"),
            "temporal_context": self._table_cell_value(short_term_table, "A_situational_semantics", "time"),
            "actor_companion": self._table_cell_value(short_term_table, "A_situational_semantics", "subject"),
            "object_trace": self._table_cell_value(short_term_table, "A_situational_semantics", "object"),
            "events": self._extractor_items(extractor, "events"),
            "entities": self._extractor_entities(extractor),
            "sensory_details": self._extractor_items(extractor, "sensory_details"),
        }
        emotion = {
            "type": self._table_cell_value(short_term_table, "B_affective_semantics", "momentary_affect"),
            "intensity": self._table_cell_value(short_term_table, "B_affective_semantics", "affective_intensity"),
            "ambiguity": self._table_cell_value(short_term_table, "B_affective_semantics", "affective_ambiguity"),
            "voice_affect": voice_affect or {},
        }
        communication = {
            "intent_type": self._table_cell_value(short_term_table, "C_communicative_semantics", "intent_type"),
            "desired_response": self._table_cell_value(short_term_table, "C_communicative_semantics", "desired_response"),
            "disclosure_depth": self._table_cell_value(short_term_table, "C_communicative_semantics", "disclosure_depth"),
        }
        return situation, emotion, communication

    def _table_cell_value(self, table: dict, section: str, field: str):
        section_value = table.get(section, {}) if isinstance(table, dict) else {}
        if not isinstance(section_value, dict):
            return None
        return self._cell_raw_value(section_value.get(field))

    def _extractor_items(self, extractor: dict, key: str) -> list:
        if not isinstance(extractor, dict):
            return []
        value = extractor.get(key)
        return value if isinstance(value, list) else []

    def _extractor_entities(self, extractor: dict) -> list[dict]:
        if not isinstance(extractor, dict):
            return []
        entities = []
        for key, entity_type in [
            ("subjects", "person"),
            ("scenes", "place"),
            ("times", "time"),
            ("objects", "object"),
        ]:
            for item in extractor.get(key, []) if isinstance(extractor.get(key), list) else []:
                if isinstance(item, dict):
                    entities.append(
                        {
                            "raw": item.get("value"),
                            "type": entity_type,
                            "evidence": item.get("evidence", ""),
                        }
                    )
        return entities

    def _rule_understanding(
        self,
        transcript: str,
        asr_raw: dict | None,
        speaker_context: dict | None = None,
        voice_affect: dict | None = None,
    ) -> LanguageEmotionResult:
        speaker_context = speaker_context or self._speaker_context(None, None)
        lowered = transcript.lower()

        emotion_type = self._conservative_emotion(transcript, voice_affect)
        question_answers = self._conservative_question_answers(transcript, speaker_context, emotion_type)
        question_answers = self._merge_voice_affect_question_answers(question_answers, voice_affect)
        initial_table = self._normalize_short_term_table({}, question_answers, transcript, speaker_context)
        initial_short_term_table = json.loads(json.dumps(initial_table, ensure_ascii=False))
        reflection = self.reflection_agent._rule_reflect(transcript, initial_table, speaker_context)
        short_term_table = self.reflection_agent._apply_revisions(initial_table, reflection)
        short_term_table["reflectionPolicy"] = "Reflection Agent 只检查并修正短期语义表；不生成视觉隐喻，不向 Designer 或 History Reasoner 提供额外输入。"
        situation, emotion, communication = self._payloads_from_short_loop(short_term_table, {}, transcript, voice_affect)
        reply = "我听到了，这条近况会被整理成今天的时间壁纸。"

        return LanguageEmotionResult(
            transcript=transcript,
            emotion=emotion,
            situation=situation,
            communication=communication,
            short_term_table=short_term_table,
            reply=reply,
            raw={
                "asr": asr_raw,
                "audio_understanding": {"voiceAffect": voice_affect or {}},
                "lowered": lowered,
                "llm": {"provider": "conservative_fallback"},
                "speaker_context": speaker_context,
                "gist_layer": {},
                "semantic_extractor": {},
                "question_answers": question_answers,
                "initial_short_term_table": initial_short_term_table,
                "reflection": reflection,
            },
        )

    def _voice_affect_from_raw(self, asr_raw: dict | None) -> dict:
        if not isinstance(asr_raw, dict):
            return {}
        parsed = asr_raw.get("parsed")
        if isinstance(parsed, dict) and isinstance(parsed.get("voiceAffect"), dict):
            return parsed["voiceAffect"]
        voice_affect = asr_raw.get("voiceAffect") or asr_raw.get("voice_affect")
        return voice_affect if isinstance(voice_affect, dict) else {}

    def _merge_voice_affect_question_answers(self, question_answers: dict, voice_affect: dict | None) -> dict:
        if not isinstance(voice_affect, dict) or not voice_affect:
            return question_answers
        try:
            confidence = float(voice_affect.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence <= 0:
            return question_answers
        result = dict(question_answers)
        evidence = self._voice_affect_evidence(voice_affect)
        for field, value_key in [
            ("momentary_affect", "emotion"),
            ("affective_intensity", "intensity"),
            ("affective_ambiguity", "ambiguity"),
        ]:
            value = voice_affect.get(value_key)
            if not value:
                continue
            current = result.get(field)
            if isinstance(current, dict) and current.get("source") == "explicit" and current.get("confidence", 0) >= confidence:
                continue
            result[field] = {
                "value": value,
                "answer": f"音频语气线索显示：{value}",
                "evidence": evidence,
                "source": "inferred",
                "confidence": max(0.35, min(0.85, confidence)),
            }
        return result

    def _voice_affect_evidence(self, voice_affect: dict) -> str:
        cues = voice_affect.get("vocalCues")
        cue_text = "、".join(str(item) for item in cues if str(item).strip()) if isinstance(cues, list) else ""
        tone = str(voice_affect.get("tone") or "").strip()
        parts = [part for part in [tone, cue_text] if part]
        return "音频语气线索：" + ("；".join(parts) if parts else "模型判断说话语气")

    def _conservative_emotion(self, transcript: str, voice_affect: dict | None = None) -> str:
        if any(word in transcript for word in ("开心", "高兴", "快乐", "顺利")):
            return "愉悦"
        if any(word in transcript for word in ("累", "疲惫", "困", "熬夜")):
            return "疲惫"
        if any(word in transcript for word in ("难过", "伤心", "不舒服")):
            return "悲伤"
        if any(word in transcript for word in ("想", "想念", "牵挂")):
            return "思念"
        voice_emotion = str((voice_affect or {}).get("emotion") or "")
        if voice_emotion in {"愉悦", "平静", "悲伤", "焦虑", "思念", "期待", "疲惫"}:
            return voice_emotion
        return "平静"

    def _conservative_question_answers(self, transcript: str, speaker_context: dict, emotion_type: str) -> dict:
        evidence = "表格枚举兜底：LLM 不可用或调用失败，只从短期表 Values/States 中选择"
        affect_source = "explicit" if emotion_type != "平静" else "default"
        affect_confidence = 0.55 if emotion_type != "平静" else 0.35
        intent_type = "倾诉情绪" if emotion_type in {"疲惫", "悲伤"} else "分享生活"
        disclosure_depth = "情绪透露" if emotion_type in {"疲惫", "悲伤", "思念"} else "日常分享"
        desired_response = "安慰或轻触回应" if emotion_type in {"疲惫", "悲伤"} else "看见即可"
        return {
            "event": {
                "value": self._fallback_event_value(transcript, speaker_context),
                "answer": "LLM 不可用时只从 Event 的 Values/States 中选择。",
                "evidence": evidence,
                "source": "default",
                "confidence": 0.3,
            },
            "scene": {
                "value": self._fallback_scene_value(transcript, speaker_context),
                "answer": "LLM 不可用时只从 Scene 的 Values/States 中选择。",
                "evidence": evidence,
                "source": "default",
                "confidence": 0.3,
            },
            "time": {
                "value": self._infer_time(transcript),
                "answer": "只根据明确时间词或最低限度生活节奏做保守判断。",
                "evidence": "原文时间线索或默认生活时刻",
                "source": "inferred",
                "confidence": 0.4,
            },
            "subject": {
                "value": [self._fallback_subject_value(transcript, speaker_context)],
                "answer": "文本省略主体时使用端侧身份对应的人物对象类别。",
                "evidence": f"端侧硬规则/文本线索：人物对象类别={self._fallback_subject_value(transcript, speaker_context)}",
                "source": "inferred",
                "confidence": 0.7,
            },
            "object": {
                "value": [self._fallback_object_value(transcript, speaker_context)],
                "answer": "LLM 不可用时只从 Object 的 Values/States 中选择。",
                "evidence": evidence,
                "source": "default",
                "confidence": 0.3,
            },
            "momentary_affect": {
                "value": emotion_type,
                "answer": "仅依据少量明确情绪词做保守情绪判断。",
                "evidence": transcript[:80] if affect_source == "explicit" else evidence,
                "source": affect_source,
                "confidence": affect_confidence,
            },
            "affective_intensity": {
                "value": "明显" if any(word in transcript for word in ("很", "太", "特别", "好久", "一直")) else "轻微",
                "answer": "只根据明确强度词保守判断。",
                "evidence": transcript[:80],
                "source": "inferred",
                "confidence": 0.45,
            },
            "affective_ambiguity": {
                "value": "明确" if emotion_type != "平静" else "需要上下文",
                "answer": "没有明确情绪词时需要上下文。",
                "evidence": transcript[:80] if emotion_type != "平静" else evidence,
                "source": "inferred",
                "confidence": 0.45,
            },
            "intent_type": {
                "value": intent_type,
                "answer": "根据情绪透露强弱做最低限度沟通意图判断。",
                "evidence": transcript[:80],
                "source": "inferred",
                "confidence": 0.4,
            },
            "desired_response": {
                "value": desired_response,
                "answer": "没有明确索要回复时默认低压力回应。",
                "evidence": evidence,
                "source": "default",
                "confidence": 0.35,
            },
            "disclosure_depth": {
                "value": disclosure_depth,
                "answer": "根据是否出现情绪状态做保守判断。",
                "evidence": transcript[:80],
                "source": "inferred",
                "confidence": 0.4,
            },
        }

    def _fallback_event_value(self, transcript: str, speaker_context: dict) -> str:
        if any(word in transcript for word in ("做饭", "吃饭", "饭", "菜", "粥")):
            return "做饭"
        if any(word in transcript for word in ("散步", "走路", "公园")):
            return "散步"
        if any(word in transcript for word in ("旅行", "出门", "车票", "机票", "行李")):
            return "旅行"
        if any(word in transcript for word in ("生病", "不舒服", "医院", "药")):
            return "生病"
        if any(word in transcript for word in ("聚会", "合唱", "唱歌", "排练", "活动")):
            return "聚会"
        if any(word in transcript for word in ("学习", "作业", "报告", "考试", "图书馆", "看书")):
            return "学习"
        if any(word in transcript for word in ("通勤", "上班路", "下班路", "地铁", "公交")):
            return "通勤"
        if any(word in transcript for word in ("工作", "加班", "项目", "开会", "下班")):
            return "工作"
        return "学习" if speaker_context.get("speaker_role") == "child" else "工作"

    def _fallback_scene_value(self, transcript: str, speaker_context: dict) -> str:
        if any(word in transcript for word in ("社区", "合唱", "活动")):
            return "社区"
        if any(word in transcript for word in ("公园", "散步")):
            return "公园"
        if any(word in transcript for word in ("办公室", "开会", "项目", "加班")):
            return "办公室"
        if any(word in transcript for word in ("医院", "药", "生病")):
            return "医院"
        if any(word in transcript for word in ("街道", "通勤", "地铁", "公交", "下班路")):
            return "街道"
        if any(word in transcript for word in ("阳台", "晒")):
            return "阳台"
        if any(word in transcript for word in ("厨房", "做饭", "饭菜")):
            return "厨房"
        return "家庭"

    def _fallback_subject_value(self, transcript: str, speaker_context: dict) -> str:
        if any(word in transcript for word in ("妈妈", "爸爸", "母亲", "父亲", "老人")):
            return "父母"
        if any(word in transcript for word in ("女儿", "儿子", "孩子", "子女")):
            return "子女"
        if speaker_context.get("speaker_role") == "child":
            return "子女"
        if speaker_context.get("speaker_role") == "parent":
            return "父母"
        return "朋友"

    def _fallback_object_value(self, transcript: str, speaker_context: dict) -> str:
        if any(word in transcript for word in ("饭", "菜", "粥")):
            return "饭菜"
        if any(word in transcript for word in ("药", "医院", "不舒服", "生病")):
            return "药盒"
        if any(word in transcript for word in ("旅行", "出门", "行李")):
            return "行李箱"
        if any(word in transcript for word in ("书", "学习", "报告", "图书馆", "作业", "考试")):
            return "书"
        if any(word in transcript for word in ("照片", "相册")):
            return "照片"
        if any(word in transcript for word in ("花", "桂花", "香")):
            return "花"
        if any(word in transcript for word in ("杯", "喝水", "茶", "咖啡")):
            return "杯子"
        if any(word in transcript for word in ("票", "门票", "车票", "机票")):
            return "门票"
        return "书" if speaker_context.get("speaker_role") == "child" else "照片"

    def _sync_structured_payloads_from_table(
        self,
        situation: dict,
        emotion: dict,
        communication: dict,
        short_term_table: dict,
    ) -> tuple[dict, dict, dict]:
        situation = dict(situation or {})
        emotion = dict(emotion or {})
        communication = dict(communication or {})
        a_section = short_term_table.get("A_situational_semantics", {})
        b_section = short_term_table.get("B_affective_semantics", {})
        c_section = short_term_table.get("C_communicative_semantics", {})

        situation["event_activity_type"] = self._cell_raw_value(a_section.get("event"), situation.get("event_activity_type"))
        situation["scene_place_type"] = self._cell_raw_value(a_section.get("scene"), situation.get("scene_place_type"))
        situation["temporal_context"] = self._cell_raw_value(a_section.get("time"), situation.get("temporal_context"))
        situation["actor_companion"] = self._cell_raw_value(a_section.get("subject"), situation.get("actor_companion"))
        situation["object_trace"] = self._cell_raw_value(a_section.get("object"), situation.get("object_trace"))

        emotion["type"] = self._cell_raw_value(b_section.get("momentary_affect"), emotion.get("type"))
        emotion["intensity"] = self._cell_raw_value(b_section.get("affective_intensity"), emotion.get("intensity"))
        emotion["ambiguity"] = self._cell_raw_value(b_section.get("affective_ambiguity"), emotion.get("ambiguity"))

        communication["intent_type"] = self._cell_raw_value(c_section.get("intent_type"), communication.get("intent_type"))
        communication["desired_response"] = self._cell_raw_value(c_section.get("desired_response"), communication.get("desired_response"))
        communication["disclosure_depth"] = self._cell_raw_value(c_section.get("disclosure_depth"), communication.get("disclosure_depth"))
        return situation, emotion, communication

    def _cell_raw_value(self, cell, fallback=None):
        if isinstance(cell, dict) and not self._is_empty(cell.get("value")):
            return cell.get("value")
        return fallback

    def _build_short_term_table(
        self,
        transcript: str,
        situation: dict,
        emotion: dict,
        communication: dict,
        speaker_context: dict | None = None,
    ) -> dict:
        evidence = self._primary_evidence(transcript, situation)
        event_evidence = self._event_evidence(situation) or evidence
        entity_evidence = self._entity_evidence(situation) or evidence
        confidence = self._average_confidence(situation)
        subject = self._resolve_subject(situation.get("actor_companion"), speaker_context, transcript)
        subject_evidence = entity_evidence
        if subject_evidence == evidence and speaker_context and speaker_context.get("speaker_label"):
            subject_evidence = f"端侧上下文：当前说话者={speaker_context.get('speaker_label')}"
        return {
            "tableName": "short_term_semantic_table",
            "agentRole": "Script Analyzer / 短期语义填表智能体",
            "designBoundary": "只做文本事实、实体、事件、情绪和沟通意图填表；不生成视觉隐喻，不决定画面怎么画。",
            "A_situational_semantics": {
                "event": self._cell(situation.get("event_activity_type"), event_evidence, confidence),
                "scene": self._cell(situation.get("scene_place_type"), evidence, confidence),
                "time": self._cell(situation.get("temporal_context"), evidence, confidence),
                "subject": self._cell(subject, subject_evidence, confidence),
                "object": self._cell(situation.get("object_trace"), entity_evidence, confidence),
            },
            "B_affective_semantics": {
                "momentary_affect": self._cell(emotion.get("type"), evidence, confidence),
                "affective_intensity": self._cell(emotion.get("intensity"), evidence, confidence),
                "affective_ambiguity": self._cell(emotion.get("ambiguity"), evidence, confidence),
            },
            "C_communicative_semantics": {
                "intent_type": self._cell(communication.get("intent_type"), evidence, confidence),
                "desired_response": self._cell(communication.get("desired_response"), evidence, confidence),
                "disclosure_depth": self._cell(communication.get("disclosure_depth"), evidence, confidence),
            },
        }

    def _complete_short_term_table(self, table: dict, transcript: str, speaker_context: dict | None) -> dict:
        table.setdefault("completionPolicy", "空字段由短期智能体基于事件常识、端侧身份和家庭异步沟通场景做保守补全；补全内容不生成视觉隐喻。")
        table.setdefault("A_situational_semantics", {})
        table.setdefault("B_affective_semantics", {})
        table.setdefault("C_communicative_semantics", {})

        event = self._cell_value(table, "A_situational_semantics", "event")
        subject = self._cell_value(table, "A_situational_semantics", "subject")
        scene = self._infer_scene(event, transcript, speaker_context)
        time = self._infer_time(transcript)
        objects = self._infer_objects(event, transcript)
        actors = subject or self._fallback_actors(transcript, speaker_context or self._speaker_context(None, None))

        self._fill_if_empty(
            table,
            "A_situational_semantics",
            "event",
            self._fallback_event_value(transcript, speaker_context or {}),
            "表格枚举兜底：event 未被明确填入，只从 Values/States 中选择",
            0.3,
        )
        self._fill_if_empty(
            table,
            "A_situational_semantics",
            "scene",
            scene,
            self._inference_evidence("scene", event, transcript),
            0.58,
        )
        self._fill_if_empty(
            table,
            "A_situational_semantics",
            "time",
            time,
            self._inference_evidence("time", event, transcript),
            0.55,
        )
        self._fill_if_empty(
            table,
            "A_situational_semantics",
            "subject",
            actors,
            f"端侧硬规则：当前说话者={self._speaker_label(speaker_context)}",
            0.7,
        )
        self._fill_if_empty(
            table,
            "A_situational_semantics",
            "object",
            objects,
            self._inference_evidence("object", event, transcript),
            0.55,
        )

        self._fill_if_empty(table, "B_affective_semantics", "momentary_affect", "平静", "合理推断：文本情绪不明确，采用保守情绪状态", 0.45)
        self._fill_if_empty(table, "B_affective_semantics", "affective_intensity", "轻微", "合理推断：文本没有明确强度词，采用低强度", 0.45)
        self._fill_if_empty(table, "B_affective_semantics", "affective_ambiguity", "需要上下文", "合理推断：文本情绪线索不足", 0.45)

        self._fill_if_empty(table, "C_communicative_semantics", "intent_type", "分享生活", "合理推断：家庭异步沟通中的普通近况默认按分享生活处理", 0.48)
        self._fill_if_empty(table, "C_communicative_semantics", "desired_response", "看见即可", "合理推断：未明确索要回应时默认低压力回应", 0.48)
        self._fill_if_empty(table, "C_communicative_semantics", "disclosure_depth", "日常分享", "合理推断：未出现求助或脆弱表达时默认日常分享", 0.48)
        return table

    def _fill_if_empty(
        self,
        table: dict,
        section: str,
        field: str,
        value,
        evidence: str,
        confidence: float,
    ) -> None:
        section_value = table.setdefault(section, {})
        cell = section_value.get(field)
        if not isinstance(cell, dict) or self._is_empty(cell.get("value")):
            section_value[field] = self._cell(value, evidence, confidence)

    def _cell_value(self, table: dict, section: str, field: str):
        section_value = table.get(section, {})
        if not isinstance(section_value, dict):
            return None
        cell = section_value.get(field, {})
        if not isinstance(cell, dict):
            return None
        value = cell.get("value")
        if self._is_empty(value):
            return None
        return value

    def _is_empty(self, value) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, list):
            return not [item for item in value if str(item).strip()]
        return False

    def _value_text(self, value) -> str:
        if isinstance(value, list):
            return "、".join(str(item) for item in value if str(item).strip())
        return str(value or "")

    def _infer_scene(self, event, transcript: str, speaker_context: dict | None) -> str:
        return self._fallback_scene_value(transcript, speaker_context or {})

    def _infer_time(self, transcript: str) -> str:
        if any(word in transcript for word in ("早上", "清晨")):
            return "清晨"
        if any(word in transcript for word in ("下午", "午后", "中午")):
            return "午后"
        if any(word in transcript for word in ("晚上", "夜", "熬夜", "加班", "半夜", "凌晨")):
            return "深夜"
        if any(word in transcript for word in ("周末", "星期六", "星期天")):
            return "周末"
        if any(word in transcript for word in ("节日", "过节", "春节", "中秋")):
            return "节日"
        if any(word in transcript for word in ("春天", "夏天", "秋天", "冬天", "桂花", "季节")):
            return "季节"
        return "黄昏"

    def _infer_objects(self, event, transcript: str) -> list[str]:
        return [self._fallback_object_value(transcript, {})]

    def _inference_evidence(self, field: str, event, transcript: str) -> str:
        event_text = self._value_text(event) or "表格枚举兜底"
        return f"表格枚举兜底：{field} 未被明确填入；事件={event_text}；原文片段={transcript[:40]}"

    def _speaker_label(self, speaker_context: dict | None) -> str:
        if not speaker_context:
            return "当前说话者"
        return str(speaker_context.get("speaker_label") or "当前说话者")

    def _speaker_context(self, user_id: str | None, relationship_id: str | None) -> dict:
        role = "unknown"
        speaker_label = "当前说话者"
        counterpart_label = "对方"
        if user_id == DEFAULT_PARENT_USER_ID:
            role = "parent"
            speaker_label = "妈妈"
            counterpart_label = "女儿"
        elif user_id == DEFAULT_CHILD_USER_ID:
            role = "child"
            speaker_label = "女儿"
            counterpart_label = "妈妈"
        return {
            "user_id": user_id or "",
            "relationship_id": relationship_id or "",
            "speaker_role": role,
            "speaker_label": speaker_label,
            "counterpart_label": counterpart_label,
        }

    def _fallback_actors(self, transcript: str, speaker_context: dict) -> list[str]:
        actors = [speaker_context.get("speaker_label") or "当前说话者"]
        counterpart = speaker_context.get("counterpart_label") or "对方"
        if any(word in transcript for word in ("你", "对方", "孩子", "妈妈", "爸爸", "女儿", "儿子")):
            if counterpart not in actors:
                actors.append(counterpart)
        return actors

    def _resolve_subject(self, raw_subject, speaker_context: dict | None, transcript: str) -> list[str]:
        subjects: list[str] = []
        if isinstance(raw_subject, list):
            subjects = [str(item) for item in raw_subject if str(item).strip()]
        elif isinstance(raw_subject, str) and raw_subject.strip():
            subjects = [raw_subject.strip()]
        if subjects:
            return subjects
        if not speaker_context:
            return []
        return self._fallback_actors(transcript, speaker_context)

    def _cell(self, value, evidence: str, confidence: float) -> dict:
        return {
            "value": value,
            "evidence": evidence,
            "confidence": round(confidence, 2),
        }

    def _primary_evidence(self, transcript: str, situation: dict) -> str:
        facts = situation.get("explicit_facts") or []
        if facts:
            return str(facts[0])
        return transcript[:120]

    def _event_evidence(self, situation: dict) -> str:
        events = situation.get("events") or []
        if events and isinstance(events[0], dict):
            return str(events[0].get("evidence") or events[0].get("raw") or "")
        return ""

    def _entity_evidence(self, situation: dict) -> str:
        entities = situation.get("entities") or []
        if entities:
            return "；".join(str(item.get("raw", "")) for item in entities if isinstance(item, dict) and item.get("raw"))
        sensory = situation.get("sensory_details") or []
        if sensory:
            return "；".join(str(item.get("raw", "")) for item in sensory if isinstance(item, dict) and item.get("raw"))
        return ""

    def _average_confidence(self, situation: dict) -> float:
        confidences = [
            float(item["confidence"])
            for item in situation.get("events", [])
            if isinstance(item, dict) and isinstance(item.get("confidence"), (int, float))
        ]
        if not confidences:
            return 0.65
        return sum(confidences) / len(confidences)

    def _log(self, run_id: str | None, message: str) -> None:
        prefix = f"[agent-run:{run_id}]" if run_id else "[agent-run]"
        print(f"{prefix} {message}", flush=True)
