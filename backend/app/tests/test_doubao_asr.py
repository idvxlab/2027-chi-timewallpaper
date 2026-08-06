from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.chatbox_asr_service import _transcribe_with_doubao_seed_asr


@pytest.mark.asyncio
async def test_doubao_seed_asr_uses_flash_base64_contract() -> None:
    response = MagicMock()
    response.headers = {
        "X-Api-Status-Code": "20000000",
        "X-Api-Message": "OK",
        "X-Tt-Logid": "test-log-id",
    }
    response.json.return_value = {
        "audio_info": {"duration": 1800},
        "result": {"text": "今天有点累。", "utterances": []},
    }

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=response)

    with patch(
        "app.services.chatbox_asr_service.httpx.AsyncClient",
        return_value=client,
    ):
        result = await _transcribe_with_doubao_seed_asr(
            audio=b"fake-wav-bytes",
            endpoint="https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
            api_key="speech-api-key",
            resource_id="volc.bigasr.auc_turbo",
            run_id="test-run",
        )

    assert result["transcript"] == "今天有点累。"
    assert result["raw"]["provider"] == "doubao_seed_asr_2_0"

    request = client.post.await_args
    assert request.args[0].endswith("/api/v3/auc/bigmodel/recognize/flash")
    assert request.kwargs["headers"]["X-Api-Key"] == "speech-api-key"
    assert request.kwargs["headers"]["X-Api-Resource-Id"] == "volc.bigasr.auc_turbo"
    assert request.kwargs["json"]["request"]["model_name"] == "bigmodel"
    assert request.kwargs["json"]["audio"]["data"] == base64.b64encode(
        b"fake-wav-bytes"
    ).decode("ascii")

