from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, String, DateTime, JSON
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
    parent_user_id: Mapped[str] = mapped_column(String(64), index=True)
    child_user_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_role: Mapped[str] = mapped_column(String(32), default="mother")
    child_role: Mapped[str] = mapped_column(String(32), default="daughter")
    relation_type: Mapped[str] = mapped_column(String(64), default="parent_child")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
