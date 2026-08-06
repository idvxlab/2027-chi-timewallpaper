"""Unified interface for image generation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


class ImageProvider(ABC):
    """Contract implemented by image generation and editing providers."""

    provider_name: str = "image_provider"

    @property
    def model_name(self) -> str:
        return "unknown"

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
    ) -> "Image.Image":
        """Generate one RGB image from text."""

    @abstractmethod
    async def edit(
        self,
        base_image: "Image.Image",
        prompt: str,
        *,
        identity_images: list["Image.Image"] | None = None,
        mask: "Image.Image | None" = None,
        size: str | None = None,
    ) -> "Image.Image":
        """Generate one edited RGB image from a base image and references."""
