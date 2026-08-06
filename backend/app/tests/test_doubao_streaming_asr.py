from __future__ import annotations

import json

import pytest

from app.services.doubao_streaming_asr_service import (
    FULL_SERVER_RESPONSE,
    GZIP_COMPRESSION,
    JSON_SERIALIZATION,
    _decode_server_frame,
    _encode_client_frame,
    _normalize_streaming_result,
    streaming_profile,
)


def test_streaming_profiles_keep_packets_equal_and_give_elder_more_time() -> None:
    child = streaming_profile("child")
    elder = streaming_profile("elder")

    assert child["chunk_ms"] == elder["chunk_ms"] == 200
    assert child["end_silence_ms"] == 750
    assert elder["end_silence_ms"] == 1200
    assert child["max_recording_ms"] == 20000
    assert elder["max_recording_ms"] == 30000


def test_binary_protocol_round_trip_for_json_frame() -> None:
    payload = {"result": {"text": "今天有点累。"}}
    frame = _encode_client_frame(
        FULL_SERVER_RESPONSE,
        payload,
        serialization=JSON_SERIALIZATION,
        compression=GZIP_COMPRESSION,
    )

    decoded = _decode_server_frame(frame)

    assert decoded["message_type"] == FULL_SERVER_RESPONSE
    assert decoded["payload"] == payload


def test_streaming_result_exposes_utterance_emotion_volume_and_rate() -> None:
    data = {
        "result": {
            "text": "今天有点累。",
            "utterances": [
                {
                    "text": "今天有点累。",
                    "start_time": 0,
                    "end_time": 1500,
                    "additions": json.dumps(
                        {
                            "emotion": "sad",
                            "volume": "0.38",
                            "speech_rate": "0.72",
                        }
                    ),
                }
            ],
        }
    }

    result = _normalize_streaming_result(data, "request-1", 48000)

    assert result["transcript"] == "今天有点累。"
    assert result["voiceAffect"]["emotion"] == "悲伤"
    assert result["voiceAffect"]["providerEmotion"] == "sad"
    assert result["voiceAffect"]["confidence"] == 0.65
    assert result["voiceAffect"]["confidenceSource"] == "system_fusion_weight_not_provider_confidence"
    assert result["voiceAffect"]["volume"] == "0.38"
    assert result["voiceAffect"]["speechRate"] == "0.72"
    assert result["raw"]["provider"] == "doubao_seed_asr_2_0_streaming_input"


@pytest.mark.parametrize(
    ("provider_emotion", "short_term_emotion", "confidence"),
    [
        ("happy", "愉悦", 0.65),
        ("sad", "悲伤", 0.65),
        ("neutral", "平静", 0.55),
        ("angry", "生气", 0.65),
        ("surprise", "惊讶", 0.65),
    ],
)
def test_all_doubao_emotions_map_deterministically(
    provider_emotion: str,
    short_term_emotion: str,
    confidence: float,
) -> None:
    data = {
        "result": {
            "text": "测试。",
            "utterances": [
                {
                    "text": "测试。",
                    "additions": json.dumps({"emotion": provider_emotion}),
                }
            ],
        }
    }

    result = _normalize_streaming_result(data, "request-map", 3200)

    assert result["voiceAffect"]["providerEmotion"] == provider_emotion
    assert result["voiceAffect"]["emotion"] == short_term_emotion
    assert result["voiceAffect"]["confidence"] == confidence
