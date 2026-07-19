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


class ComfortReplyResult(_CamelModel):
    text: str
    tone: str = ""
    strategy: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    short_term_table: dict[str, Any] = Field(default_factory=dict)
    long_term_table: dict[str, Any] = Field(default_factory=dict)


class MemoryObjectItem(_CamelModel):
    asset_id: str = ""
    name: str
    mention_count: int = 0
    threshold: int = 3
    ready: bool = False
    image_url: str = ""
    prompt: str = ""
    examples: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    last_seen_at: Optional[datetime] = None


class MemoryObjectsIn(_CamelModel):
    user_id: Optional[str] = None
    relationship_id: Optional[str] = None
    threshold: int = 3
    limit: int = 12
    generate_missing: bool = True


class MemoryObjectsOut(_CamelModel):
    run_id: str
    status: str
    user_id: str
    relationship_id: str
    threshold: int = 3
    items: list[MemoryObjectItem] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


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


class ComfortReplyIn(_CamelModel):
    transcript: str = Field(min_length=1)
    user_id: Optional[str] = None
    relationship_id: Optional[str] = None
    persist: bool = False


class ComfortReplyOut(_CamelModel):
    run_id: str
    status: str
    user_id: str
    relationship_id: str
    comfort_reply: ComfortReplyResult


class BaseSceneIn(_CamelModel):
    note: str = ""


class BaseSceneOut(_CamelModel):
    status: str
    image_url: str = ""
    generation_mode: str = "layered_painter_tool:base_scene_only"
    raw: dict[str, Any] = Field(default_factory=dict)


class CharacterAssetOut(_CamelModel):
    asset_id: str
    user_id: str
    relationship_id: Optional[str] = None
    role: str
    status: str
    style_version: str
    source_image_url: str
    style_reference_url: str = ""
    master_image_url: str = ""
    portrait_image_url: str = ""
    half_body_image_url: str = ""
    full_body_image_url: str = ""
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class CharacterAssetListOut(_CamelModel):
    items: list[CharacterAssetOut] = Field(default_factory=list)
