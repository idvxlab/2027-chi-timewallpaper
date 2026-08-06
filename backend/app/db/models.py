from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TouchLog(Base):
    __tablename__ = "touch_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    hotspot_id: Mapped[str] = mapped_column(String(64))
    demo_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AsrLog(Base):
    __tablename__ = "asr_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transcript: Mapped[str] = mapped_column(String(1024))
    raw: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentRunLog(Base):
    __tablename__ = "agent_run_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(32), default="")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RelationshipProfile(Base):
    __tablename__ = "relationship_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    relationship_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invite_code: Mapped[Optional[str]] = mapped_column(String(4), nullable=True, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(192), default="")
    parent_user_id: Mapped[str] = mapped_column(String(64), index=True)
    child_user_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_role: Mapped[str] = mapped_column(String(32), default="mother")
    child_role: Mapped[str] = mapped_column(String(32), default="daughter")
    relation_type: Mapped[str] = mapped_column(String(64), default="parent_child")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DeviceSession(Base):
    __tablename__ = "device_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    input_type: Mapped[str] = mapped_column(String(32), default="text")
    transcript: Mapped[str] = mapped_column(String(4096))
    short_term_table: Mapped[dict] = mapped_column(JSON, default=dict)
    emotion: Mapped[dict] = mapped_column(JSON, default=dict)
    situation: Mapped[dict] = mapped_column(JSON, default=dict)
    communication: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RelationshipState(Base):
    __tablename__ = "relationship_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    relationship_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    long_term_table: Mapped[dict] = mapped_column(JSON, default=dict)
    longitudinal: Mapped[dict] = mapped_column(JSON, default=dict)
    relational: Mapped[dict] = mapped_column(JSON, default=dict)
    history_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=0)
    last_applied_event_seq: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RelationshipWallpaperState(Base):
    """Current shared scene and role-specific wallpaper views for one family."""

    __tablename__ = "relationship_wallpaper_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    relationship_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    stage: Mapped[str] = mapped_column(String(32), default="characters_ready")
    status: Mapped[str] = mapped_column(String(32), default="idle")
    base_scene_url: Mapped[str] = mapped_column(String(512), default="")
    child_view_url: Mapped[str] = mapped_column(String(512), default="")
    elder_view_url: Mapped[str] = mapped_column(String(512), default="")
    version: Mapped[int] = mapped_column(default=0)
    next_event_seq: Mapped[int] = mapped_column(default=0)
    latest_run_id: Mapped[str] = mapped_column(String(64), default="")
    last_error: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VoiceEvent(Base):
    """Idempotent, ordered record for one accepted voice interaction."""

    __tablename__ = "voice_events"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_voice_event_user_request"),
        UniqueConstraint("relationship_id", "event_seq", name="uq_voice_event_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    device_session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    speaker_role: Mapped[str] = mapped_column(String(32), default="")
    input_type: Mapped[str] = mapped_column(String(32), default="audio")
    event_seq: Mapped[int] = mapped_column(index=True)
    base_state_version: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32), default="accepted", index=True)
    transcript: Mapped[str] = mapped_column(String(4096), default="")
    short_term_table: Mapped[dict] = mapped_column(JSON, default=dict)
    long_term_table: Mapped[dict] = mapped_column(JSON, default=dict)
    semantic_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VoiceGenerationPlan(Base):
    """Immutable Designer output shared by both view-render tasks for one voice."""

    __tablename__ = "voice_generation_plans"
    __table_args__ = (
        UniqueConstraint("relationship_id", "event_seq", name="uq_voice_plan_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    event_seq: Mapped[int] = mapped_column(index=True)
    speaker_role: Mapped[str] = mapped_column(String(32))
    generation_stage: Mapped[str] = mapped_column(String(32))
    designer_five_layer_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    semantic_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(32), default="five-layer-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WallpaperRenderTask(Base):
    """One ordered render operation for one relationship view and voice event."""

    __tablename__ = "wallpaper_render_tasks"
    __table_args__ = (
        UniqueConstraint("plan_id", "view_role", name="uq_render_task_plan_view"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    event_seq: Mapped[int] = mapped_column(index=True)
    speaker_role: Mapped[str] = mapped_column(String(32))
    view_role: Mapped[str] = mapped_column(String(32), index=True)
    render_mode: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(default=0)
    speaker_region: Mapped[str] = mapped_column(String(32))
    preserved_region: Mapped[str] = mapped_column(String(32))
    role_reference_images: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    retry_count: Mapped[int] = mapped_column(default=0)
    parent_image_url: Mapped[str] = mapped_column(String(512), default="")
    output_image_url: Mapped[str] = mapped_column(String(512), default="")
    final_prompt: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class WallpaperRevision(Base):
    """Append-only per-role history pointing to shared wallpaper revisions."""

    __tablename__ = "wallpaper_revisions"
    __table_args__ = (
        UniqueConstraint("relationship_id", "event_seq", "view_role", name="uq_revision_event_view"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    revision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    event_seq: Mapped[int] = mapped_column(index=True)
    view_role: Mapped[str] = mapped_column(String(32), index=True)
    parent_revision_id: Mapped[str] = mapped_column(String(64), default="")
    parent_image_url: Mapped[str] = mapped_column(String(512), default="")
    image_url: Mapped[str] = mapped_column(String(512))
    designer_five_layer_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    final_prompt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WallpaperViewHead(Base):
    """Current revision pointer for one relationship's child or elder view."""

    __tablename__ = "wallpaper_view_heads"
    __table_args__ = (
        UniqueConstraint("relationship_id", "view_role", name="uq_view_head_relationship_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    view_role: Mapped[str] = mapped_column(String(32), index=True)
    current_event_seq: Mapped[int] = mapped_column(default=0)
    current_revision_id: Mapped[str] = mapped_column(String(64), default="")
    current_image_url: Mapped[str] = mapped_column(String(512), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WallpaperLog(Base):
    __tablename__ = "wallpaper_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    wallpaper_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    prompt: Mapped[str] = mapped_column(String(4096), default="")
    image_url: Mapped[str] = mapped_column(String(512), default="")
    five_layer_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    asset_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReferenceImageLog(Base):
    __tablename__ = "reference_image_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reference_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    image_url: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CharacterAsset(Base):
    """Reusable picture-book character identity created during onboarding."""

    __tablename__ = "character_assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    source_image_url: Mapped[str] = mapped_column(String(512))
    style_reference_url: Mapped[str] = mapped_column(String(512), default="")
    master_image_url: Mapped[str] = mapped_column(String(512), default="")
    portrait_image_url: Mapped[str] = mapped_column(String(512), default="")
    half_body_image_url: Mapped[str] = mapped_column(String(512), default="")
    full_body_image_url: Mapped[str] = mapped_column(String(512), default="")
    style_version: Mapped[str] = mapped_column(String(64), default="picturebook-character-v1")
    status: Mapped[str] = mapped_column(String(32), default="processing")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    prompt: Mapped[str] = mapped_column(String(4096), default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InteractionLog(Base):
    __tablename__ = "interaction_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    interaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    interaction_type: Mapped[str] = mapped_column(String(32), default="touch")
    target_id: Mapped[str] = mapped_column(String(128), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MemoryObjectAsset(Base):
    __tablename__ = "memory_object_assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    relationship_id: Mapped[str] = mapped_column(String(64), index=True)
    object_name: Mapped[str] = mapped_column(String(128), index=True)
    mention_count: Mapped[int] = mapped_column(default=0)
    threshold: Mapped[int] = mapped_column(default=3)
    image_url: Mapped[str] = mapped_column(String(512), default="")
    prompt: Mapped[str] = mapped_column(String(2048), default="")
    status: Mapped[str] = mapped_column(String(32), default="ready")
    examples: Mapped[list] = mapped_column(JSON, default=list)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
