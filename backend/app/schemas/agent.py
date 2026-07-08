from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class _CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=_to_camel)


class AgentStep(_CamelModel):
    name: str
    status: str = "pending"


class LanguageEmotionResult(_CamelModel):
    transcript: str
    emotion: dict[str, Any]
    situation: dict[str, Any]
    communication: dict[str, Any]
    short_term_table: dict[str, Any] = Field(default_factory=dict)
    reply: str
    raw: dict[str, Any] = Field(default_factory=dict)


class MemoryRelationResult(_CamelModel):
    longitudinal: dict[str, Any]
    relational: dict[str, Any]
    long_term_table: dict[str, Any] = Field(default_factory=dict)
    memory_card: dict[str, str]
    history_summary: dict[str, Any] = Field(default_factory=dict)


class SemanticMappingResult(_CamelModel):
    semantic_visual_instruction: str
    graph_version: str = ""
    retrieval_query: str = ""
    cognitive_scaffold: dict[str, Any] = Field(default_factory=dict)
    open_content_visualizations: list[dict[str, str]] = Field(default_factory=list)
    mapping_trace: list[dict[str, Any]] = Field(default_factory=list)


class ImageGenerationResult(_CamelModel):
    wallpaper_url: str = ""
    generation_mode: str = "mask_image2image_mvp"
    changed_regions: list[str] = Field(default_factory=list)
    asset_metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunResult(_CamelModel):
    run_id: str
    status: str
    user_id: Optional[str] = None
    relationship_id: Optional[str] = None
    steps: list[AgentStep]
    language_emotion: Optional[LanguageEmotionResult] = None
    memory_relation: Optional[MemoryRelationResult] = None
    semantic_mapping: Optional[SemanticMappingResult] = None
    image_generation: Optional[ImageGenerationResult] = None
    created_at: datetime
    updated_at: datetime


class AgentRunCreateOut(_CamelModel):
    run_id: str
    status: str
    result: AgentRunResult


class AgentRunTextIn(_CamelModel):
    transcript: str = Field(min_length=1)
    user_id: Optional[str] = None
    relationship_id: Optional[str] = None
    previous_image_url: Optional[str] = None
    elder_reference_url: Optional[str] = None
    child_reference_url: Optional[str] = None
