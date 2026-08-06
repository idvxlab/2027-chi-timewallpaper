from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.providers.llm.openai_compatible import OpenAICompatibleLLM
from app.schemas.agent import ChatBotResult
from app.services.chatbox_asr_service import transcribe_chatbox_audio
from app.services.doubao_streaming_asr_service import DoubaoStreamingASRSession
from app.services.user_context import get_speaker_context


@dataclass
class ChatBotAudioContext:
    transcript: str
    voice_affect: dict[str, Any]
    asr_raw: dict[str, Any]
    audio_affect_raw: dict[str, Any]


class ChatBotAgent:
    """Own the real-time voice interaction lane.

    ASR and acoustic-affect analysis are tools, not semantic-memory agents.
    This agent coordinates those tools and creates the user-facing reply without
    reading short-term tables, long-term tables, visual mappings, or image prompts.
    """

    async def prepare_audio(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
        run_id: str | None = None,
    ) -> ChatBotAudioContext:
        self._log(run_id, "ASR request started; independent audio-affect model disabled")
        asr = await transcribe_chatbox_audio(
            audio,
            filename=filename,
            content_type=content_type,
            run_id=run_id,
        )
        self._log(run_id, "ASR request completed")
        return ChatBotAudioContext(
            transcript=str(asr.get("transcript") or "").strip(),
            voice_affect={},
            asr_raw=self._dict(asr.get("raw")),
            audio_affect_raw={
                "provider": "none",
                "reason": "independent audio-affect model removed; streaming ASR owns voice affect",
            },
        )

    def create_streaming_asr_session(
        self,
        *,
        uid: str,
        end_window_size_ms: int,
        run_id: str | None = None,
    ) -> DoubaoStreamingASRSession:
        """Create the acoustic stream owned by this ChatBot lane."""
        return DoubaoStreamingASRSession(
            uid=uid,
            end_window_size_ms=end_window_size_ms,
            run_id=run_id,
        )

    def context_from_streaming_result(
        self,
        streamed: dict[str, Any],
    ) -> ChatBotAudioContext:
        transcript = str(streamed.get("transcript") or "").strip()
        voice_affect = self._dict(streamed.get("voiceAffect"))
        raw = self._dict(streamed.get("raw"))
        return ChatBotAudioContext(
            transcript=transcript,
            voice_affect=voice_affect,
            asr_raw=raw,
            audio_affect_raw={
                "provider": "doubao_seed_asr_2_0_streaming_input",
                "voiceAffect": voice_affect,
            },
        )

    async def generate_reply(
        self,
        transcript: str,
        *,
        voice_affect: dict[str, Any] | None = None,
        recent_dialogue: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
        relationship_id: str | None = None,
        run_id: str | None = None,
        audio_context: ChatBotAudioContext | None = None,
    ) -> ChatBotResult:
        speaker_context = get_speaker_context(user_id, relationship_id)
        voice_affect = self._dict(voice_affect)
        recent_dialogue = (recent_dialogue or [])[-8:]
        provider = self._provider()

        raw: dict[str, Any] = {
            "asr": audio_context.asr_raw if audio_context else {},
            "audio_understanding": audio_context.audio_affect_raw if audio_context else {},
            "speaker_context": speaker_context,
        }
        if provider is None:
            reply = self._fallback_reply(transcript, voice_affect)
            raw["llm"] = {"provider": "chatbot_fallback", "reason": "provider not configured"}
            return ChatBotResult(
                transcript=transcript,
                voice_affect=voice_affect,
                reply=reply,
                raw=raw,
            )

        prompt = f"""
你是 TimeWallpaper 的 ChatBot Agent，只负责对用户当前这句话生成即时聊天回复。

当前转录：
{transcript}

当前说话者上下文：
{json.dumps(speaker_context, ensure_ascii=False)}

可选的声音情绪线索：
{json.dumps(voice_affect, ensure_ascii=False)}

最近聊天记录：
{json.dumps(recent_dialogue, ensure_ascii=False)}

边界规则：
- 只回复用户，不填写或推断短期表、长期表、关系表、视觉映射或图片提示词。
- 不假装已经读取任何语义表或记忆智能体输出。
- 回复使用自然中文，温柔、简短、低压力，通常一到两句话，不超过 60 个汉字。
- 用户只是分享近况时先表示看见；用户疲惫或难过时先共情，不急着说教。
- 声音情绪为空时只依据当前转录，不要虚构语气。

只输出 JSON：
{{"reply": "给用户的即时回复"}}
""".strip()

        try:
            self._log(run_id, "reply LLM request started")
            text = await provider.chat(
                prompt,
                system="你是家庭异步陪伴场景的独立 ChatBot Agent，只输出可解析 JSON。",
                response_format={"type": "json_object"},
                temperature=settings.chatbot_llm_temperature,
                enable_thinking=settings.chatbot_enable_thinking,
                max_tokens=160,
                timeout=settings.chatbot_timeout_seconds,
            )
            data = self._loads_json(text)
            reply = str(data.get("reply") or "").strip()
            if not reply:
                raise ValueError("ChatBot response did not contain reply")
            raw["llm"] = {
                "provider": "openai_compatible",
                "model": settings.effective_chatbot_llm_model,
                "parsed": data,
            }
            self._log(run_id, "reply LLM response parsed")
        except (KeyError, TypeError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            self._log(run_id, f"reply LLM failed, fallback={type(exc).__name__}: {exc}")
            reply = self._fallback_reply(transcript, voice_affect)
            raw["llm"] = {
                "provider": "chatbot_fallback",
                "error": f"{type(exc).__name__}: {exc}",
            }

        return ChatBotResult(
            transcript=transcript,
            voice_affect=voice_affect,
            reply=reply,
            raw=raw,
        )

    def _provider(self) -> OpenAICompatibleLLM | None:
        provider = OpenAICompatibleLLM(
            api_key=settings.effective_chatbot_llm_api_key,
            api_base_url=settings.effective_chatbot_llm_api_base_url,
            model=settings.effective_chatbot_llm_model,
            chat_endpoint=settings.effective_chatbot_llm_chat_endpoint,
        )
        return provider if provider.configured else None

    def _loads_json(self, text: str) -> dict[str, Any]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise
            data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise TypeError("ChatBot response must be a JSON object")
        return data

    def _fallback_reply(self, transcript: str, voice_affect: dict[str, Any]) -> str:
        text = f"{transcript} {voice_affect.get('emotion', '')}"
        if any(word in text for word in ("累", "疲惫", "辛苦", "困", "加班")):
            return "听起来今天真的很辛苦，先让自己缓一缓吧，我在这里。"
        if any(word in text for word in ("难过", "伤心", "焦虑", "不舒服")):
            return "我听见了，你可以慢慢说，不用一个人撑着。"
        if any(word in text for word in ("开心", "高兴", "顺利", "快乐")):
            return "听起来是个很好的小瞬间，谢谢你告诉我。"
        if any(word in text for word in ("想家", "想你", "想念", "牵挂")):
            return "我也把这份想念收到了，我们慢慢聊。"
        return "我听到了，谢谢你把这一刻告诉我。"

    def _dict(self, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _log(self, run_id: str | None, message: str) -> None:
        print(f"[agent-run:{run_id or '-'}] chatbot {message}")
