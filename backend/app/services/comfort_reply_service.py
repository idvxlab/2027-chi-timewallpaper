from __future__ import annotations

from typing import Any

from app.schemas.agent import ComfortReplyResult, LanguageEmotionResult, MemoryRelationResult


class ComfortReplyService:
    """Build a lightweight suggested reply from the semantic tables.

    This is intentionally not an agent. It does not create visual metaphors and
    does not call the image pipeline; it only turns the filled tables into a
    low-pressure comfort reply that can be tested independently.
    """

    def build(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        user_id: str,
    ) -> ComfortReplyResult:
        short_table = language.short_term_table or {}
        long_table = memory.long_term_table or {}

        event = self._value(short_table, "A_situational_semantics", "event") or "这件事"
        time = self._value(short_table, "A_situational_semantics", "time")
        affect = self._value(short_table, "B_affective_semantics", "momentary_affect") or "平静"
        intensity = self._value(short_table, "B_affective_semantics", "affective_intensity") or "轻微"
        intent = self._value(short_table, "C_communicative_semantics", "intent_type") or "分享生活"
        desired = self._value(short_table, "C_communicative_semantics", "desired_response") or "轻触回应"
        warmth = self._value(long_table, "E_relational_semantics", "emotional_warmth") or "温暖"
        distance = self._value(long_table, "E_relational_semantics", "intimacy_distance") or "稳定"

        tone = self._tone(affect, desired, warmth, distance)
        strategy = self._strategy(affect, intent, desired)
        text = self._reply_text(event, time, affect, intensity, intent, desired, warmth, distance)

        return ComfortReplyResult(
            text=text,
            tone=tone,
            strategy=strategy,
            source={
                "userId": user_id,
                "event": event,
                "time": time,
                "affect": affect,
                "affectiveIntensity": intensity,
                "intentType": intent,
                "desiredResponse": desired,
                "relationshipWarmth": warmth,
                "intimacyDistance": distance,
            },
            short_term_table=short_table,
            long_term_table=long_table,
        )

    def _reply_text(
        self,
        event: str,
        time: str,
        affect: str,
        intensity: str,
        intent: str,
        desired: str,
        warmth: str,
        distance: str,
    ) -> str:
        event_text = self._event_text(event, time)
        affect_text = str(affect)
        is_tired = self._has_any(affect_text + event_text, ("焦虑", "悲伤", "累", "疲", "压力", "工作", "加班", "生病", "不舒服"))
        is_missing = self._has_any(affect_text + str(intent), ("思念", "期待", "想念"))
        is_happy = self._has_any(affect_text, ("愉悦", "平静"))
        wants_light = self._has_any(str(desired), ("轻触回应", "看见即可", "留言"))
        is_close = not self._has_any(str(distance), ("远", "疏远"))

        if is_tired:
            if wants_light:
                return f"听起来{event_text}真的很不容易。先慢慢缓一下，不用急着再撑着，我在这里陪你。"
            return f"听起来{event_text}让你消耗了很多。先让自己歇一会儿，喝点水，今天已经做得很不容易了。"
        if is_missing:
            return "我也很想你。能听到你这样说，我心里很暖，我们慢慢聊，不用急。"
        if is_happy:
            return f"听起来{event_text}是个很好的小瞬间。谢谢你告诉我，我也跟着觉得心里亮了一点。"
        if self._has_any(str(intent), ("寻求安慰", "倾诉情绪")):
            return f"我听见了，{event_text}对你来说不只是小事。你可以慢慢说，我会认真听。"
        if is_close or "温暖" in str(warmth):
            return f"收到啦，{event_text}我记在心里了。你慢慢来，我一直都在。"
        return f"收到你的分享了，{event_text}我看见了。希望你接下来能轻松一点。"

    def _event_text(self, event: str, time: str) -> str:
        event = str(event or "这件事").strip()
        time = str(time or "").strip()
        if time and time not in event:
            if self._has_any(event, ("深夜", "12点", "半夜")) or self._has_any(time, ("深夜", "12点", "半夜")):
                time_brief = time.replace("今天", "").replace("深夜", "").strip(" ，,")
                return f"{event}（{time_brief}）" if time_brief else event
            return f"{time}的{event}"
        return event

    def _tone(self, affect: str, desired: str, warmth: str, distance: str) -> str:
        tone_parts = ["温柔", "低压力"]
        if self._has_any(str(desired), ("轻触回应", "看见即可")):
            tone_parts.append("短句回应")
        if "温暖" in str(warmth):
            tone_parts.append("亲近")
        if self._has_any(str(distance), ("远", "疏远")):
            tone_parts.append("克制")
        if self._has_any(str(affect), ("焦虑", "悲伤", "思念")):
            tone_parts.append("情绪确认")
        return "、".join(dict.fromkeys(tone_parts))

    def _strategy(self, affect: str, intent: str, desired: str) -> str:
        steps = ["看见当前状态"]
        if self._has_any(str(affect), ("焦虑", "悲伤", "思念")):
            steps.append("确认情绪")
        if self._has_any(str(intent), ("倾诉情绪", "寻求安慰")):
            steps.append("不急着建议")
        if self._has_any(str(desired), ("轻触回应", "看见即可")):
            steps.append("轻量陪伴")
        else:
            steps.append("适度安慰")
        return " + ".join(dict.fromkeys(steps))

    def _value(self, table: dict[str, Any], section: str, field: str) -> str:
        raw = table.get(section, {}).get(field, {}) if isinstance(table, dict) else {}
        if isinstance(raw, dict):
            value = raw.get("value")
        else:
            value = raw
        if isinstance(value, list):
            return "、".join(str(item) for item in value if str(item).strip())
        if value is None:
            return ""
        return str(value).strip()

    def _has_any(self, text: str, needles: tuple[str, ...]) -> bool:
        return any(needle in text for needle in needles)
