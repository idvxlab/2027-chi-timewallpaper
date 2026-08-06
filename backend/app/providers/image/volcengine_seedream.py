"""Volcengine Seedream image provider.

Seedream generation and reference-image editing share the JSON
``/images/generations`` endpoint. Image inputs are sent as Data URIs in stable
order: base image first, followed by identity references. The endpoint has no
independent mask parameter.
"""

from __future__ import annotations

import base64
import binascii
import io
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import settings
from app.providers.image.base import ImageProvider
from app.providers.image.errors import (
    ImageAuthError,
    ImageCapabilityNotSupportedError,
    ImageDecodeError,
    ImageDownloadError,
    ImageInvalidRequestError,
    ImageInvalidResponseError,
    ImageNetworkError,
    ImageTimeoutError,
    classify_image_http_error,
)

if TYPE_CHECKING:
    from PIL import Image


SEEDREAM_LOGICAL_TO_REQUEST_SIZE: dict[str, str] = {
    # Character assets are stored at a smaller application size. Seedream is
    # asked for a larger same-aspect-ratio source and the response is then
    # downsampled without cropping or stretching.
    "768x1024": "1728x2304",
    "1024x1536": "1664x2496",
    "1536x1536": "2048x2048",
}
SEEDREAM_NATIVE_EXACT_SIZES: set[str] = {
    "1664x2496",
    "1728x2304",
    "2048x2048",
}


def resolve_seedream_request_size(logical_size: str) -> str:
    """Map the application canvas size to a Seedream-supported request size."""

    if logical_size in SEEDREAM_NATIVE_EXACT_SIZES:
        return logical_size
    request_size = SEEDREAM_LOGICAL_TO_REQUEST_SIZE.get(logical_size)
    if request_size is None:
        supported = ", ".join(sorted(SEEDREAM_LOGICAL_TO_REQUEST_SIZE))
        raise ImageInvalidRequestError(
            f"Unsupported Seedream logical size '{logical_size}'. "
            f"Supported logical sizes: {supported}",
            param="size",
            stage="image_request",
        )
    return request_size


def normalize_seedream_output(
    raw_image: "Image.Image",
    logical_size: str,
) -> "Image.Image":
    """Normalize the provider output to the application's logical canvas."""

    from PIL import Image as PILImage

    try:
        width_text, height_text = logical_size.lower().split("x", maxsplit=1)
        target_size = (int(width_text), int(height_text))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ImageInvalidRequestError(
            f"Invalid logical image size '{logical_size}'",
            param="size",
            stage="image_response",
        ) from exc

    rgb_image = raw_image.convert("RGB")
    if rgb_image.size == target_size:
        return rgb_image
    return rgb_image.resize(target_size, PILImage.Resampling.LANCZOS)


def pil_image_to_data_uri(
    image: "Image.Image",
    *,
    preferred_format: str = "PNG",
) -> str:
    """Encode a PIL image without creating a temporary file."""

    from PIL import Image as PILImage

    image_format = preferred_format.upper()
    if image_format not in {"PNG", "JPEG", "JPG"}:
        image_format = "PNG"

    normalized_format = "JPEG" if image_format in {"JPEG", "JPG"} else "PNG"
    mime_type = "image/jpeg" if normalized_format == "JPEG" else "image/png"
    output = io.BytesIO()

    try:
        encoded_image = image
        if normalized_format == "JPEG" and encoded_image.mode in {"RGBA", "LA", "P"}:
            rgba_image = encoded_image.convert("RGBA")
            rgb_image = PILImage.new("RGB", rgba_image.size, (255, 255, 255))
            rgb_image.paste(rgba_image, mask=rgba_image.getchannel("A"))
            encoded_image = rgb_image
        encoded_image.save(output, format=normalized_format)
    except Exception as exc:
        raise ImageInvalidResponseError(
            f"Failed to encode input image as {normalized_format}",
            stage="image_encode",
        ) from exc

    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class VolcengineSeedreamProvider(ImageProvider):
    """Seedream text-to-image and reference-image editing client."""

    provider_name = "volcengine_seedream"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        logical_size: str | None = None,
        response_format: str | None = None,
        timeout_seconds: float | None = None,
        max_download_bytes: int | None = None,
    ) -> None:
        self._api_key = (
            api_key
            if api_key is not None
            else settings.volcengine_image_api_key
        ).strip()
        self._base_url = (
            base_url
            or settings.volcengine_image_base_url
            or "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/")
        self._model = (
            model or settings.volcengine_image_model or "doubao-seedream-5-0-260128"
        ).strip()
        self._logical_size = (
            logical_size or settings.volcengine_image_size or "1536x1536"
        ).strip()
        self._response_format = (
            response_format or settings.volcengine_image_response_format or "b64_json"
        ).strip()
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else settings.volcengine_image_timeout_seconds
        )
        self._max_download_bytes = int(
            max_download_bytes
            if max_download_bytes is not None
            else settings.volcengine_image_max_download_bytes
        )

        if not self._api_key:
            raise ImageAuthError(
                "VOLCENGINE_IMAGE_API_KEY is not configured",
                stage="image_configuration",
            )
        if self._response_format not in {"b64_json", "url"}:
            raise ImageInvalidRequestError(
                "Seedream response format must be 'b64_json' or 'url'",
                param="response_format",
                stage="image_configuration",
            )
        resolve_seedream_request_size(self._logical_size)

    @property
    def model_name(self) -> str:
        return self._model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self._timeout_seconds,
            connect=min(30.0, self._timeout_seconds),
            read=self._timeout_seconds,
            write=self._timeout_seconds,
            pool=min(30.0, self._timeout_seconds),
        )

    async def _post_json(self, payload: dict[str, Any], *, stage: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response = await client.post(
                    f"{self._base_url}/images/generations",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ImageTimeoutError(
                f"Seedream request timed out after {self._timeout_seconds:g}s",
                stage=stage,
            ) from exc
        except httpx.RequestError as exc:
            raise ImageNetworkError(
                f"Seedream network request failed: {exc.__class__.__name__}",
                stage=stage,
            ) from exc

        if response.status_code != 200:
            raise classify_image_http_error(
                response.status_code,
                response.text,
                stage=stage,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ImageInvalidResponseError(
                "Seedream returned invalid JSON",
                stage=stage,
            ) from exc
        if not isinstance(body, dict):
            raise ImageInvalidResponseError(
                "Seedream returned a non-object JSON response",
                stage=stage,
            )
        return body

    async def _download_bytes(self, url: str, *, stage: str) -> bytes:
        if not url.startswith(("https://", "http://")):
            raise ImageDownloadError(
                "Seedream returned an unsupported image URL",
                stage=stage,
            )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ImageDownloadError(
                "Seedream image download timed out",
                stage=stage,
            ) from exc
        except httpx.RequestError as exc:
            raise ImageDownloadError(
                f"Seedream image download failed: {exc.__class__.__name__}",
                stage=stage,
            ) from exc

        if response.status_code != 200:
            raise ImageDownloadError(
                f"Seedream image download returned HTTP {response.status_code}",
                status_code=response.status_code,
                stage=stage,
            )
        if len(response.content) > self._max_download_bytes:
            raise ImageDownloadError(
                "Seedream image download exceeded the configured size limit",
                stage=stage,
            )
        return response.content

    async def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
    ) -> "Image.Image":
        logical_size = size or self._logical_size
        payload = self._build_payload(prompt, logical_size=logical_size)
        body = await self._post_json(payload, stage="image_generate")
        return await self._parse_response(
            body,
            stage="image_generate",
            logical_size=logical_size,
        )

    async def edit(
        self,
        base_image: "Image.Image",
        prompt: str,
        *,
        identity_images: list["Image.Image"] | None = None,
        mask: "Image.Image | None" = None,
        size: str | None = None,
    ) -> "Image.Image":
        if mask is not None:
            raise ImageCapabilityNotSupportedError(
                "Seedream /images/generations has no independent mask parameter",
                stage="image_edit",
            )

        logical_size = size or self._logical_size
        images = [pil_image_to_data_uri(base_image)]
        images.extend(
            pil_image_to_data_uri(identity_image)
            for identity_image in identity_images or []
        )
        payload = self._build_payload(
            prompt,
            logical_size=logical_size,
            images=images,
        )
        body = await self._post_json(payload, stage="image_edit")
        return await self._parse_response(
            body,
            stage="image_edit",
            logical_size=logical_size,
        )

    def _build_payload(
        self,
        prompt: str,
        *,
        logical_size: str,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise ImageInvalidRequestError(
                "Seedream prompt must not be empty",
                param="prompt",
                stage="image_request",
            )

        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "size": resolve_seedream_request_size(logical_size),
            "sequential_image_generation": "disabled",
            "response_format": self._response_format,
            "watermark": False,
        }
        if images is not None:
            payload["image"] = images
        return payload

    async def _parse_response(
        self,
        body: dict[str, Any],
        *,
        stage: str,
        logical_size: str,
    ) -> "Image.Image":
        from PIL import Image as PILImage

        items = body.get("data")
        if not isinstance(items, list) or not items:
            raise ImageInvalidResponseError(
                "Seedream response has no image data",
                stage=stage,
            )
        if len(items) != 1 or not isinstance(items[0], dict):
            raise ImageInvalidResponseError(
                f"Seedream expected one image output, received {len(items)}",
                stage=stage,
            )

        item = items[0]
        encoded_image = item.get("b64_json")
        if isinstance(encoded_image, str) and encoded_image:
            try:
                raw_bytes = base64.b64decode(encoded_image, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ImageDecodeError(
                    "Seedream returned invalid base64 image data",
                    stage=stage,
                ) from exc
        else:
            image_url = item.get("url")
            if not isinstance(image_url, str) or not image_url:
                raise ImageInvalidResponseError(
                    "Seedream image response has neither b64_json nor url",
                    stage=stage,
                )
            raw_bytes = await self._download_bytes(image_url, stage=stage)

        try:
            image = PILImage.open(io.BytesIO(raw_bytes))
            image.load()
        except Exception as exc:
            raise ImageDecodeError(
                "Seedream returned undecodable image bytes",
                stage=stage,
            ) from exc
        return normalize_seedream_output(image, logical_size)
