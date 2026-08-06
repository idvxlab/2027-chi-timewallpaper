"""Typed, log-safe errors shared by image providers."""

from __future__ import annotations

import json
import re


class ImageProviderError(Exception):
    """Base class for image-provider failures."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "unknown",
        error_code: str | None = None,
        status_code: int | None = None,
        retryable: bool = True,
        stage: str = "image_edit",
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.error_code = error_code
        self.status_code = status_code
        self.retryable = retryable
        self.stage = stage
        self.request_id = request_id


class ImageAuthError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="authentication",
            retryable=False,
            **kwargs,
        )


class ImageInvalidRequestError(ImageProviderError):
    def __init__(
        self,
        message: str,
        *,
        param: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            error_type="invalid_request",
            retryable=False,
            **kwargs,
        )
        self.param = param


class ImageSafetyError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="safety",
            retryable=False,
            **kwargs,
        )


class ImageRateLimitError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="rate_limit_or_quota",
            retryable=True,
            **kwargs,
        )


class ImageUpstreamError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="upstream",
            retryable=True,
            **kwargs,
        )


class ImageBillingError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="billing",
            retryable=False,
            **kwargs,
        )


class ImageTimeoutError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="timeout",
            retryable=True,
            **kwargs,
        )


class ImageNetworkError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="network",
            retryable=True,
            **kwargs,
        )


class ImageInvalidResponseError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="invalid_response",
            retryable=False,
            **kwargs,
        )


class ImageCapabilityNotSupportedError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="capability_not_supported",
            retryable=False,
            **kwargs,
        )


class ImageDecodeError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="image_decode",
            retryable=False,
            **kwargs,
        )


class ImageDownloadError(ImageProviderError):
    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(
            message,
            error_type="download_failure",
            retryable=True,
            **kwargs,
        )


def classify_image_http_error(
    status_code: int,
    body_text: str,
    *,
    stage: str = "image_edit",
) -> ImageProviderError:
    """Convert an HTTP failure into a stable application error."""

    message = body_text[:300] or f"Image provider returned HTTP {status_code}"
    error_code: str | None = None
    param: str | None = None
    request_id: str | None = None

    try:
        body = json.loads(body_text)
        error = body.get("error", {})
        if isinstance(error, dict):
            message = str(error.get("message") or message)
            error_code = error.get("code") or error.get("type")
            param = error.get("param")
            request_id = error.get("request_id") or body.get("request_id")
    except (TypeError, ValueError):
        pass

    common = {
        "error_code": error_code,
        "status_code": status_code,
        "stage": stage,
        "request_id": request_id,
    }
    lower = body_text.lower()

    if any(
        word in lower
        for word in (
            "safety",
            "nsfw",
            "harmful",
            "content_policy",
            "inappropriate",
        )
    ):
        return ImageSafetyError(message, **common)
    if any(
        word in lower
        for word in ("billing", "quota", "limit exceeded", "insufficient")
    ):
        return ImageBillingError(message, **common)
    if status_code in (401, 403):
        return ImageAuthError(message, **common)
    if status_code == 400:
        return ImageInvalidRequestError(message, param=param, **common)
    if status_code == 429:
        return ImageRateLimitError(message, **common)
    if status_code >= 500:
        return ImageUpstreamError(message, **common)

    return ImageProviderError(
        message,
        error_code=error_code,
        status_code=status_code,
        stage=stage,
        request_id=request_id,
    )


def safe_error_message(error: Exception) -> str:
    """Remove credentials and encoded image bodies from log messages."""

    message = str(error)
    message = re.sub(
        r"data:[^,\s]+;base64,[^\s'\"]+",
        "[DATA_URI]",
        message,
        flags=re.IGNORECASE,
    )
    message = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}", "[REDACTED]", message)
    message = re.sub(
        r"(?i)\b(api[_-]?key|secret|token|bearer|authorization)\b"
        r"([\s:=]+)\S+",
        r"\1\2[REDACTED]",
        message,
    )
    message = re.sub(r"\b[A-Za-z0-9+/]{80,}={0,2}\b", "[BASE64]", message)
    return message
