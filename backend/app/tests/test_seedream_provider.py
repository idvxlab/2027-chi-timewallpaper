from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from app.providers.image.errors import (
    ImageAuthError,
    ImageCapabilityNotSupportedError,
    ImageRateLimitError,
    ImageTimeoutError,
)
from app.providers.image.volcengine_seedream import (
    VolcengineSeedreamProvider,
    resolve_seedream_request_size,
)


def _image(
    color: tuple[int, int, int] = (10, 20, 30),
    size: tuple[int, int] = (64, 96),
) -> Image.Image:
    return Image.new("RGB", size, color)


def _encoded_png(size: tuple[int, int] = (2048, 2048)) -> str:
    output = io.BytesIO()
    _image(size=size).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_generate_uses_seedream_json_contract() -> None:
    provider = VolcengineSeedreamProvider(api_key="test-key")
    captured: dict = {}

    async def fake_post(payload: dict, *, stage: str) -> dict:
        captured.update(payload)
        assert stage == "image_generate"
        return {"data": [{"b64_json": _encoded_png()}]}

    provider._post_json = fake_post  # type: ignore[method-assign]
    result = await provider.generate("a watercolor garden")

    assert "image" not in captured
    assert "mask" not in captured
    assert captured["size"] == "2048x2048"
    assert captured["sequential_image_generation"] == "disabled"
    assert captured["response_format"] == "b64_json"
    assert captured["watermark"] is False
    assert result.mode == "RGB"
    assert result.size == (1536, 1536)


@pytest.mark.asyncio
async def test_edit_sends_base_then_identity_images_as_data_uris() -> None:
    provider = VolcengineSeedreamProvider(api_key="test-key")
    captured: dict = {}

    async def fake_post(payload: dict, *, stage: str) -> dict:
        captured.update(payload)
        assert stage == "image_edit"
        return {"data": [{"b64_json": _encoded_png()}]}

    provider._post_json = fake_post  # type: ignore[method-assign]
    result = await provider.edit(
        _image((1, 1, 1)),
        "change only the lower-left character",
        identity_images=[_image((2, 2, 2)), _image((3, 3, 3))],
    )

    assert len(captured["image"]) == 3
    assert all(
        item.startswith("data:image/png;base64,")
        for item in captured["image"]
    )
    assert "mask" not in captured
    assert result.size == (1536, 1536)


@pytest.mark.asyncio
async def test_character_size_uses_larger_same_ratio_seedream_source() -> None:
    provider = VolcengineSeedreamProvider(
        api_key="test-key",
        logical_size="768x1024",
    )
    captured: dict = {}

    async def fake_post(payload: dict, *, stage: str) -> dict:
        captured.update(payload)
        assert stage == "image_edit"
        return {"data": [{"b64_json": _encoded_png((1728, 2304))}]}

    provider._post_json = fake_post  # type: ignore[method-assign]
    result = await provider.edit(_image(), "create one reusable character")

    assert captured["size"] == "1728x2304"
    assert result.size == (768, 1024)


@pytest.mark.asyncio
async def test_edit_rejects_mask_before_http_call() -> None:
    provider = VolcengineSeedreamProvider(api_key="test-key")
    provider._post_json = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ImageCapabilityNotSupportedError):
        await provider.edit(_image(), "edit", mask=_image())

    provider._post_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_url_response_downloads_without_changing_output_contract() -> None:
    provider = VolcengineSeedreamProvider(
        api_key="test-key",
        response_format="url",
    )
    image_bytes = base64.b64decode(_encoded_png())
    provider._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value={"data": [{"url": "https://example.test/result.png"}]}
    )
    provider._download_bytes = AsyncMock(  # type: ignore[method-assign]
        return_value=image_bytes
    )

    result = await provider.generate("a garden")

    assert result.size == (1536, 1536)
    provider._download_bytes.assert_awaited_once_with(
        "https://example.test/result.png",
        stage="image_generate",
    )


@pytest.mark.asyncio
async def test_http_endpoint_and_error_classification() -> None:
    response = MagicMock()
    response.status_code = 429
    response.text = '{"error":{"message":"rate limit","code":"rate_limit"}}'

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=response)

    provider = VolcengineSeedreamProvider(
        api_key="test-key",
        base_url="https://ark.example/api/v3/",
    )

    with patch(
        "app.providers.image.volcengine_seedream.httpx.AsyncClient",
        return_value=client,
    ):
        with pytest.raises(ImageRateLimitError) as caught:
            await provider._post_json({"prompt": "test"}, stage="image_generate")

    assert caught.value.retryable is True
    request = client.post.await_args
    assert request.args[0] == "https://ark.example/api/v3/images/generations"
    assert request.kwargs["json"] == {"prompt": "test"}
    assert request.kwargs["headers"]["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_typed() -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(
        side_effect=httpx.ReadTimeout(
            "timeout",
            request=httpx.Request("POST", "https://ark.example"),
        )
    )
    provider = VolcengineSeedreamProvider(api_key="test-key")

    with patch(
        "app.providers.image.volcengine_seedream.httpx.AsyncClient",
        return_value=client,
    ):
        with pytest.raises(ImageTimeoutError) as caught:
            await provider._post_json({"prompt": "test"}, stage="image_generate")

    assert caught.value.retryable is True


def test_configuration_and_size_validation() -> None:
    with pytest.raises(ImageAuthError):
        VolcengineSeedreamProvider(api_key="")

    assert resolve_seedream_request_size("768x1024") == "1728x2304"
    assert resolve_seedream_request_size("1728x2304") == "1728x2304"
    assert resolve_seedream_request_size("1024x1536") == "1664x2496"
    assert resolve_seedream_request_size("1664x2496") == "1664x2496"
    assert resolve_seedream_request_size("1536x1536") == "2048x2048"
    assert resolve_seedream_request_size("2048x2048") == "2048x2048"
