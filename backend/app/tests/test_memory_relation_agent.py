from __future__ import annotations

import asyncio

from app.agents.memory_relation_agent import MemoryRelationAgent
from app.agents.orchestrator import MultiAgentOrchestrator
from app.schemas.agent import (
    ChatBotResult,
    ImageGenerationResult,
    LanguageEmotionResult,
    MemoryRelationResult,
    SemanticMappingResult,
)


def _short_table(emotion: str = "平静", event: str = "日常交流") -> dict:
    return {
        "A_situational_semantics": {
            "event": {"value": event},
            "scene": {"value": "家庭"},
            "time": {"value": "今天"},
            "subject": {"value": ["父母"]},
        },
        "B_affective_semantics": {
            "momentary_affect": {"value": emotion},
            "affective_intensity": {"value": "轻微"},
            "affective_ambiguity": {"value": "清晰"},
        },
        "C_communicative_semantics": {
            "intent_type": {"value": "分享"},
            "desired_response": {"value": "回应"},
            "disclosure_depth": {"value": "日常"},
        },
    }


def _language(emotion: str = "平静", event: str = "日常交流") -> LanguageEmotionResult:
    return LanguageEmotionResult(
        transcript="这是本次消息，不能进入并行运行中的长期模型输入。",
        emotion={},
        situation={},
        communication={},
        short_term_table=_short_table(emotion, event),
        reply="",
        raw={"reflection": {"supervised": True}},
    )


def _context() -> dict:
    return {
        "relationshipId": "family-1",
        "previousLongTermTable": {},
        "previousHistoryDigest": {},
        "recentMessages": [
            {
                "messageId": "child-message",
                "userId": "child-1",
                "createdAt": "2026-08-03T09:00:00",
                "shortTermValues": {
                    "event": "上班",
                    "emotion": "疲惫",
                    "intensity": "明显",
                    "intent": "分享",
                    "desiredResponse": "安慰",
                    "date": "2026-08-03",
                },
            },
            {
                "messageId": "parent-message",
                "userId": "parent-1",
                "createdAt": "2026-08-02T09:00:00",
                "shortTermValues": {
                    "event": "散步",
                    "emotion": "平静",
                    "intensity": "轻微",
                    "intent": "分享",
                    "desiredResponse": "回应",
                    "date": "2026-08-02",
                },
            },
        ],
        "recentInteractionCount": 3,
        "lastContactAt": "2026-08-03T09:00:00",
        "parentUserId": "parent-1",
        "childUserId": "child-1",
    }


def test_long_term_llm_runs_on_every_request_and_uses_history_only(monkeypatch) -> None:
    agent = MemoryRelationAgent()
    calls: list[dict] = []

    async def fake_long_term(evidence_pack: dict, run_id: str | None = None) -> dict:
        del run_id
        calls.append(evidence_pack)
        return agent._build_fallback_long_term_table(evidence_pack)

    monkeypatch.setattr(agent, "_try_llm_long_term_table", fake_long_term)

    asyncio.run(agent.run_history("family-1", prefetched_context=_context()))
    asyncio.run(agent.run_history("family-1", prefetched_context=_context()))

    assert len(calls) == 2
    assert "currentShortTermValues" not in calls[0]
    assert "triggerReasons" not in calls[0]
    stats = calls[0]["historyDigest"]["rollingStats"]
    assert stats["windowDays"] == 14
    assert stats["messageCount"] == 2
    assert stats["parentMessageCount"] == 1
    assert stats["childMessageCount"] == 1
    assert stats["interactionCount"] == 3
    serialized = str(calls[0])
    assert "这是本次消息" not in serialized
    assert "reflection" not in serialized


def test_reflected_short_table_is_attached_only_after_long_term_reasoning(monkeypatch) -> None:
    agent = MemoryRelationAgent()

    async def fake_long_term(evidence_pack: dict, run_id: str | None = None) -> dict:
        del run_id
        return agent._build_fallback_long_term_table(evidence_pack)

    monkeypatch.setattr(agent, "_try_llm_long_term_table", fake_long_term)
    memory = asyncio.run(agent.run_history("family-1", prefetched_context=_context()))
    updated = agent.attach_current_short(memory, _language("悲伤", "想念家人"), user_id="parent-1")

    stats = updated.history_summary["rollingStats"]
    assert stats["messageCount"] == 3
    assert stats["parentMessageCount"] == 2
    assert stats["childMessageCount"] == 1
    assert updated.history_summary["lastShortTermValues"]["event"] == "想念家人"
    assert updated.history_summary["emotionTrend"]["recent"][-1] == "悲伤"


def test_mapping_waits_for_long_term_and_reflected_short_chain(monkeypatch) -> None:
    orchestrator = MultiAgentOrchestrator()
    state = {
        "long_started": False,
        "short_started": False,
        "long_done": False,
        "reflected_short_done": False,
        "mapping_started": False,
    }

    base_memory = MemoryRelationResult(
        longitudinal={},
        relational={"relationship_trend": "稳定"},
        long_term_table={},
        memory_card={"frontHint": "", "backSummary": ""},
        history_summary={
            "rollingStats": {},
            "emotionTrend": {},
            "participants": {"parentUserId": "parent-1", "childUserId": "child-1"},
        },
    )

    class FakeChatBot:
        async def generate_reply(self, *args, **kwargs):
            await asyncio.sleep(0)
            return ChatBotResult(transcript="测试", reply="收到", raw={})

    class FakeLanguage:
        async def run_text(self, *args, **kwargs):
            state["short_started"] = True
            while not state["long_started"]:
                await asyncio.sleep(0)
            await asyncio.sleep(0.01)
            state["reflected_short_done"] = True
            return _language()

    class FakeMemory:
        async def run_history(self, *args, **kwargs):
            state["long_started"] = True
            while not state["short_started"]:
                await asyncio.sleep(0)
            await asyncio.sleep(0.01)
            state["long_done"] = True
            return base_memory

        def attach_current_short(self, memory, language, *, user_id=None):
            assert state["reflected_short_done"]
            return memory

    class FakeMapping:
        async def run(self, language, memory, run_id=None, **kwargs):
            del language, memory, run_id, kwargs
            assert state["long_done"]
            assert state["reflected_short_done"]
            state["mapping_started"] = True
            return SemanticMappingResult(
                semantic_visual_instruction="test",
                cognitive_scaffold={},
                mapping_trace=[],
            )

    class FakeImage:
        async def run(self, *args, **kwargs):
            return ImageGenerationResult()

    async def no_op(*args, **kwargs):
        return None

    async def message_log(*args, **kwargs):
        return "message-1"

    orchestrator.chatbot_agent = FakeChatBot()
    orchestrator.language_emotion_agent = FakeLanguage()
    orchestrator.memory_relation_agent = FakeMemory()
    orchestrator.semantic_mapping_agent = FakeMapping()
    orchestrator.image_generation_agent = FakeImage()
    for name in (
        "_save_asr_log",
        "_save_relationship_state",
        "_save_wallpaper_log",
        "_save_run",
    ):
        monkeypatch.setattr(orchestrator, name, no_op)
    monkeypatch.setattr(orchestrator, "_save_message_log", message_log)
    monkeypatch.setattr(orchestrator, "_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_log_script_analyzer_internal", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_log_initial_short_term_table", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_log_script_reflection", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_log_short_term_table", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_log_long_term_table", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "_log_designer_output", lambda *args, **kwargs: None)

    result = asyncio.run(
        orchestrator.run_text(
            "测试",
            user_id="parent-1",
            relationship_id="family-1",
        )
    )

    assert state["mapping_started"]
    assert result.status == "done"
    assert [step.status for step in result.steps] == ["done"] * 5
