import asyncio

from app.agents import chatbot_agent as chatbot_module
from app.agents.chatbot_agent import ChatBotAgent
from app.agents.language_emotion_agent import LanguageEmotionAgent


def test_chatbot_flash_audio_skips_independent_voice_affect_model(monkeypatch) -> None:
    async def fake_asr(*args, **kwargs):
        return {"transcript": "今天有点累。", "raw": {"provider": "fake_asr"}}

    monkeypatch.setattr(chatbot_module, "transcribe_chatbox_audio", fake_asr)

    context = asyncio.run(
        ChatBotAgent().prepare_audio(
            b"audio",
            filename="recording.wav",
            content_type="audio/wav",
            run_id="parallel-test",
        )
    )

    assert context.transcript == "今天有点累。"
    assert context.voice_affect == {}
    assert context.audio_affect_raw["provider"] == "none"


def test_chatbot_fallback_is_independent_from_semantic_tables(monkeypatch) -> None:
    agent = ChatBotAgent()
    monkeypatch.setattr(agent, "_provider", lambda: None)

    result = asyncio.run(
        agent.generate_reply(
            "今天开了一天的会，好累呀。",
            voice_affect={"emotion": "疲惫"},
            user_id="mother-demo",
            relationship_id="family-demo",
            run_id="fallback-test",
        )
    )

    assert result.reply == "听起来今天真的很辛苦，先让自己缓一缓吧，我在这里。"
    assert "short_term_table" not in result.raw
    assert "long_term_table" not in result.raw


def test_chatbot_uses_its_own_fast_non_thinking_request(monkeypatch) -> None:
    captured: dict = {}

    class FakeProvider:
        async def chat(self, prompt: str, **kwargs) -> str:
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return '{"reply":"我听到了，先歇一会儿吧。"}'

    agent = ChatBotAgent()
    monkeypatch.setattr(agent, "_provider", lambda: FakeProvider())

    result = asyncio.run(
        agent.generate_reply(
            "今天有点累。",
            voice_affect={},
            run_id="provider-test",
        )
    )

    assert result.reply == "我听到了，先歇一会儿吧。"
    assert captured["kwargs"]["enable_thinking"] is False
    assert captured["kwargs"]["max_tokens"] == 160
    assert "不填写或推断短期表" in captured["prompt"]


def test_streaming_audio_emotion_is_written_to_short_term_affect_table() -> None:
    result = LanguageEmotionAgent()._rule_understanding(
        "我刚刚到家。",
        asr_raw=None,
        voice_affect={
            "providerEmotion": "angry",
            "emotion": "生气",
            "confidence": 0.65,
            "confidenceSource": "system_fusion_weight_not_provider_confidence",
        },
    )

    cell = result.short_term_table["B_affective_semantics"]["momentary_affect"]
    assert cell["value"] == "生气"
    assert cell["source"] == "inferred"
    assert "豆包流式情绪=angry" in cell["evidence"]


def test_explicit_text_emotion_keeps_priority_over_streaming_audio() -> None:
    result = LanguageEmotionAgent()._rule_understanding(
        "今天特别开心。",
        asr_raw=None,
        voice_affect={
            "providerEmotion": "sad",
            "emotion": "悲伤",
            "confidence": 0.65,
            "confidenceSource": "system_fusion_weight_not_provider_confidence",
        },
    )

    cell = result.short_term_table["B_affective_semantics"]["momentary_affect"]
    assert cell["value"] == "愉悦"
