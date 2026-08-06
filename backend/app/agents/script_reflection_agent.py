from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

import httpx

from app.providers.llm import get_llm_provider


class ScriptReflectionAgent:
    async def run(
        self,
        transcript: str,
        initial_table: dict[str, Any],
        speaker_context: dict[str, Any],
        run_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        llm_result = await self._try_llm_reflect(transcript, initial_table, speaker_context, run_id=run_id)
        if llm_result is None:
            llm_result = self._rule_reflect(transcript, initial_table, speaker_context)

        revised_table = self._apply_revisions(initial_table, llm_result)
        revised_table["reflectionPolicy"] = "Reflection Agent 只检查并修正短期语义表；不生成视觉隐喻，不向 Designer 或 History Reasoner 提供额外输入。"
        return revised_table, llm_result

    async def _try_llm_reflect(
        self,
        transcript: str,
        initial_table: dict[str, Any],
        speaker_context: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        provider = get_llm_provider()
        if provider is None:
            self._log(run_id, "script reflection skipped LLM: provider not configured")
            return None

        prompt = f"""
你是 TimeWallpaper 的 Reflection Agent，只负责监督 Script Analyzer 的短期语义表。
你不能生成视觉隐喻，不能决定画面怎么画，也不能改长期记忆表。

原始文本：
{transcript}

端侧上下文：
{json.dumps(speaker_context, ensure_ascii=False)}

Script Analyzer 初始短期表：
{json.dumps(initial_table, ensure_ascii=False)}

短期表的 Communication Question 是唯一校验入口：
- event: 最近发生了什么？
- scene: 发生在哪里？对方在哪里？
- time: 何时发生？对方当前生活节奏如何？
- subject: 与谁有关？谁在场？
- object: 哪些物件提示了这件事？
- momentary_affect: 此刻感受如何？
- affective_intensity: 这种感受有多强？
- affective_ambiguity: 这个状态是否容易理解？
- intent_type: 为什么留下这条信息？
- desired_response: 希望对方怎样回应？
- disclosure_depth: 这条信息表达得多深？

你要做的是：
1. 只基于原始文本、端侧上下文和短期表定义，独立回答每个 Communication Question。
2. 将你的回答和初始短期表逐项比较。
3. 如果初始表和问题答案不一致，或字段归属错误，输出 revisions。
4. 如果原文没有直接说，但问题需要填写，可以做保守推断；推断必须标注为 inferred/default，置信度低于直接证据。
5. 不要生成视觉隐喻，不要描述画面怎么画，不要改长期表。
6. 你看不到、也不应该依赖 Script Analyzer 的 Gist Layer / Semantic Extractor / Question-Based Filler；这些中间层只属于 Script Analyzer 内部调试。你只校验 A/B/C 表是否回答了这些问题。

只输出 JSON：
{{
  "agentRole": "Reflection Agent / 短期表监督校验智能体",
  "utterance_gist": "一句话概括整体语义，不用于下游画图，只用于校验",
  "evidence_map": {{
    "events": ["原文或稳定推断的主要事件"],
    "scenes": ["地点/场景证据"],
    "times": ["时间证据"],
    "subjects": ["人物/端侧身份证据"],
    "objects": ["物件/活动对象/生活线索"],
    "affects": ["情绪、身体状态、语气线索"],
    "communication_intents": ["沟通意图线索"]
  }},
  "question_check": {{
    "event": "你对 Communication Question 的独立回答",
    "scene": "你对 Communication Question 的独立回答",
    "time": "你对 Communication Question 的独立回答",
    "subject": "你对 Communication Question 的独立回答",
    "object": "你对 Communication Question 的独立回答",
    "momentary_affect": "你对 Communication Question 的独立回答",
    "affective_intensity": "你对 Communication Question 的独立回答",
    "affective_ambiguity": "你对 Communication Question 的独立回答",
    "intent_type": "你对 Communication Question 的独立回答",
    "desired_response": "你对 Communication Question 的独立回答",
    "disclosure_depth": "你对 Communication Question 的独立回答"
  }},
  "issues": [
    {{
      "field_path": "A_situational_semantics.scene",
      "old": "旧值",
      "problem": "为什么不准确",
      "evidence": "原文证据",
      "fix": "新值"
    }}
  ],
  "revisions": {{
    "A_situational_semantics": {{
      "event": {{"value": "修正值", "evidence": "原文证据或合理推断说明", "confidence": 0.0}},
      "scene": {{"value": "修正值", "evidence": "原文证据或合理推断说明", "confidence": 0.0}},
      "time": {{"value": "修正值", "evidence": "原文证据或合理推断说明", "confidence": 0.0}},
      "subject": {{"value": ["人物"], "evidence": "原文证据或端侧硬规则", "confidence": 0.0}},
      "object": {{"value": ["物件或活动线索"], "evidence": "原文证据或合理推断说明", "confidence": 0.0}}
    }},
    "B_affective_semantics": {{
      "momentary_affect": {{"value": "愉悦|平静|悲伤|焦虑|思念|期待|疲惫|生气|惊讶", "evidence": "原文证据", "confidence": 0.0}},
      "affective_intensity": {{"value": "轻微|明显|强烈|波动", "evidence": "原文证据", "confidence": 0.0}},
      "affective_ambiguity": {{"value": "明确|含混|难以判断|需要上下文", "evidence": "原文证据", "confidence": 0.0}}
    }},
    "C_communicative_semantics": {{
      "intent_type": {{"value": "分享生活|表达思念|寻求安慰|倾诉情绪|期待回应", "evidence": "原文证据或合理推断说明", "confidence": 0.0}},
      "desired_response": {{"value": "看见即可|轻触回应|留言|回忆|进一步聊天|安慰或轻触回应", "evidence": "原文证据或合理推断说明", "confidence": 0.0}},
      "disclosure_depth": {{"value": "日常分享|情绪透露|脆弱表达|求助", "evidence": "原文证据或合理推断说明", "confidence": 0.0}}
    }}
  }}
}}

如果字段没有问题，可以不放进 revisions。
""".strip()
        try:
            self._log(run_id, "script reflection LLM request started")
            text = await provider.chat(
                prompt,
                system="你只输出可解析 JSON。你是短期语义表监督校验智能体。",
                response_format={"type": "json_object"},
                temperature=0.05,
                timeout=90,
            )
            data = self._loads_json(text)
            self._log(run_id, "script reflection LLM response parsed")
            return data
        except (KeyError, TypeError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            self._log(run_id, f"script reflection LLM failed, fallback={type(exc).__name__}: {exc}")
            return None

    def _rule_reflect(
        self,
        transcript: str,
        initial_table: dict[str, Any],
        speaker_context: dict[str, Any],
    ) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        revisions: dict[str, dict[str, dict[str, Any]]] = {}

        def revise(section: str, field: str, value, evidence: str, confidence: float, problem: str = "") -> None:
            old_value = self._cell_value(initial_table, section, field)
            if old_value == value:
                return
            revisions.setdefault(section, {})[field] = {
                "value": value,
                "evidence": evidence,
                "confidence": round(confidence, 2),
            }
            issues.append(
                {
                    "field_path": f"{section}.{field}",
                    "old": old_value,
                    "problem": problem or "规则校验发现该字段需要更准确地贴合原文。",
                    "evidence": evidence,
                    "fix": value,
                }
            )

        work_late = self._is_work_late_return(transcript)
        if work_late:
            explicit_time = self._extract_explicit_time(transcript) or "深夜"
            time_value = f"今天深夜，{explicit_time}" if "今天" in transcript else f"深夜，{explicit_time}"
            revise(
                "A_situational_semantics",
                "event",
                "深夜加班后回家",
                self._evidence(transcript, ["加班", "12点", "回家"]),
                0.9,
                "应把加班很久和回家终点合并为连续事件，而不是泛化为工作或日常分享。",
            )
            revise(
                "A_situational_semantics",
                "scene",
                "工作场所到回家路上",
                self._evidence(transcript, ["加班", "回家"]),
                0.86,
                "回家是事件终点，不应直接把主要场景判为家庭。",
            )
            revise(
                "A_situational_semantics",
                "time",
                time_value,
                self._evidence(transcript, ["今天", explicit_time]),
                0.92,
                "文本有明确时间，不能只保留今天。",
            )
            revise(
                "A_situational_semantics",
                "object",
                ["工作任务", "加班", "回家路途"],
                self._evidence(transcript, ["加班", "回家"]),
                0.82,
                "班不是物件，应转成工作事件线索和回家路途。",
            )
            revise(
                "B_affective_semantics",
                "momentary_affect",
                "疲惫",
                self._evidence(transcript, ["好累", "加班"]),
                0.92,
                "好累和加班很久优先表达疲惫/压力；回家不等于想家。",
            )
            revise(
                "B_affective_semantics",
                "affective_intensity",
                "明显",
                self._evidence(transcript, ["好累", "好久"]),
                0.88,
                "好累、好久表示强度明显。",
            )
            revise(
                "B_affective_semantics",
                "affective_ambiguity",
                "明确",
                self._evidence(transcript, ["好累"]),
                0.86,
                "疲惫线索明确。",
            )
            revise(
                "C_communicative_semantics",
                "intent_type",
                "倾诉情绪",
                self._evidence(transcript, ["好累", "加班"]),
                0.82,
                "重点是表达疲惫和压力，不是单纯分享生活或表达思念。",
            )
            revise(
                "C_communicative_semantics",
                "desired_response",
                "安慰或轻触回应",
                "合理推断：疲惫倾诉通常期待低压力安慰或被看见。",
                0.72,
                "疲惫倾诉更适合低压力安慰，而不是强制进一步聊天。",
            )
            revise(
                "C_communicative_semantics",
                "disclosure_depth",
                "情绪透露",
                self._evidence(transcript, ["好累"]),
                0.78,
                "文本直接透露疲惫状态。",
            )

        current_subject = self._cell_value(initial_table, "A_situational_semantics", "subject")
        if self._is_empty(current_subject) and speaker_context.get("speaker_label"):
            revise(
                "A_situational_semantics",
                "subject",
                [speaker_context["speaker_label"]],
                f"端侧硬规则：当前说话者={speaker_context['speaker_label']}",
                0.8,
                "文本省略主体时必须由端侧身份补齐。",
            )

        return {
            "agentRole": "Reflection Agent / 短期表监督校验智能体",
            "utterance_gist": self._gist(transcript, work_late),
            "issues": issues,
            "revisions": revisions,
            "provider": "rule_reflection",
        }

    def _apply_revisions(self, initial_table: dict[str, Any], reflection: dict[str, Any]) -> dict[str, Any]:
        table = deepcopy(initial_table)
        revisions = reflection.get("revisions") or {}
        if not isinstance(revisions, dict):
            revisions = {}
        allowed_sections = {
            "A_situational_semantics",
            "B_affective_semantics",
            "C_communicative_semantics",
        }
        for section, fields in revisions.items():
            if section not in allowed_sections or not isinstance(fields, dict):
                continue
            section_value = table.setdefault(section, {})
            for field, cell in fields.items():
                if not isinstance(cell, dict) or "value" not in cell:
                    continue
                section_value[field] = {
                    "value": cell.get("value"),
                    "evidence": cell.get("evidence") or "Reflection Agent 修正",
                    "confidence": round(float(cell.get("confidence", 0.7) or 0.7), 2),
                    "source": "reflection",
                }
        return table

    def _is_work_late_return(self, transcript: str) -> bool:
        has_work = any(word in transcript for word in ("加班", "下班", "工作", "班"))
        has_late = bool(self._extract_explicit_time(transcript)) or any(word in transcript for word in ("很晚", "半夜", "深夜", "凌晨", "熬夜", "好久"))
        has_return = any(word in transcript for word in ("回家", "到家", "才回"))
        has_tired = any(word in transcript for word in ("累", "疲惫", "困", "撑不住"))
        return has_work and has_return and (has_late or has_tired)

    def _extract_explicit_time(self, transcript: str) -> str:
        match = re.search(r"([0-2]?\d)\s*[点:：](半|\d{1,2}分?)?", transcript)
        if not match:
            return ""
        hour = int(match.group(1))
        suffix = match.group(2) or ""
        if suffix == "半":
            return f"{hour}点半"
        if suffix:
            return f"{hour}点{suffix}"
        return f"{hour}点"

    def _evidence(self, transcript: str, keywords: list[str]) -> str:
        hits = [word for word in keywords if word and word in transcript]
        if hits:
            return f"原文包含：{'、'.join(hits)}；{transcript[:80]}"
        return transcript[:80]

    def _gist(self, transcript: str, work_late: bool) -> str:
        if work_late:
            return "说话者今天深夜加班很久后才回家，主要表达疲惫和压力。"
        return f"说话者表达了一条当前生活近况：{transcript[:60]}"

    def _cell_value(self, table: dict[str, Any], section: str, field: str):
        section_value = table.get(section, {})
        if not isinstance(section_value, dict):
            return None
        cell = section_value.get(field, {})
        if not isinstance(cell, dict):
            return None
        return cell.get("value")

    def _is_empty(self, value) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, list):
            return not [item for item in value if str(item).strip()]
        return False

    def _loads_json(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise
            return json.loads(match.group(0))

    def _log(self, run_id: str | None, message: str) -> None:
        prefix = f"[agent-run:{run_id}]" if run_id else "[agent-run]"
        print(f"{prefix} {message}", flush=True)
