"""Canonical visual styles for shared-wallpaper image prompts."""

from __future__ import annotations


VINTAGE_PAPER_CRAFT_V1_ID = "vintage_paper_craft_v1"

VINTAGE_PAPER_CRAFT_V1_STYLE = (
    "The image must look like a full-frame handcrafted layered paper-cut world: "
    "cut-paper characters, layered paper scenery, rounded paper edges, "
    "visible fibrous paper texture, carefully arranged paper shapes and soft "
    "dimensional shadows between layers.\n\n"
    "Use a gentle low-saturation palette of cream paper tones, sage green, "
    "dusty blue, muted floral colors and restrained warm accents.\n\n"
    "Every person, animal, object, plant, architectural element and newly "
    "generated environmental detail must be constructed from the same "
    "layered paper vocabulary.\n\n"
    "The result should feel like an immersive warm handmade storybook environment "
    "that continues beyond all four image edges, not a standard painted landscape "
    "and not a separate craft object photographed on a backing sheet.\n\n"
    "Do not use photorealistic rendering.\n"
    "Do not use realistic skin shading.\n"
    "Do not use watercolor texture as the main style.\n"
    "Do not use clay, plastic, glossy 3D or photographic texture.\n"
    "Do not flatten new characters or objects into stickers."
)


def get_style_prompt(style_id: str = VINTAGE_PAPER_CRAFT_V1_ID) -> str:
    if style_id != VINTAGE_PAPER_CRAFT_V1_ID:
        raise KeyError(f"Unknown image style: {style_id}")
    return VINTAGE_PAPER_CRAFT_V1_STYLE


__all__ = [
    "VINTAGE_PAPER_CRAFT_V1_ID",
    "VINTAGE_PAPER_CRAFT_V1_STYLE",
    "get_style_prompt",
]
