"""Add the first concurrency-control foundation.

Revision ID: 20260804_01
Revises:
Create Date: 2026-08-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260804_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    application_tables = tables - {"alembic_version"}

    # A brand-new deployment receives the complete current schema. Existing
    # SQLite/MySQL installations are upgraded column by column below.
    if not application_tables:
        from app.db.session import Base
        import app.db.models  # noqa: F401

        Base.metadata.create_all(bind=bind)
        return

    if "device_sessions" in tables:
        columns = _columns(inspector, "device_sessions")
        if "device_id" not in columns:
            op.add_column(
                "device_sessions",
                sa.Column("device_id", sa.String(length=64), nullable=False, server_default=""),
            )
            op.create_index("ix_device_sessions_device_id", "device_sessions", ["device_id"])

    if "relationship_states" in tables:
        columns = _columns(inspector, "relationship_states")
        if "version" not in columns:
            op.add_column(
                "relationship_states",
                sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
            )
        if "last_applied_event_seq" not in columns:
            op.add_column(
                "relationship_states",
                sa.Column("last_applied_event_seq", sa.Integer(), nullable=False, server_default="0"),
            )

    if "relationship_wallpaper_states" in tables:
        columns = _columns(inspector, "relationship_wallpaper_states")
        if "next_event_seq" not in columns:
            op.add_column(
                "relationship_wallpaper_states",
                sa.Column("next_event_seq", sa.Integer(), nullable=False, server_default="0"),
            )
            op.execute(
                "UPDATE relationship_wallpaper_states "
                "SET next_event_seq = version WHERE next_event_seq < version"
            )

    if "voice_events" not in tables:
        op.create_table(
            "voice_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("relationship_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("device_session_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("speaker_role", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("input_type", sa.String(length=32), nullable=False, server_default="audio"),
            sa.Column("event_seq", sa.Integer(), nullable=False),
            sa.Column("base_state_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="accepted"),
            sa.Column("transcript", sa.String(length=4096), nullable=False, server_default=""),
            sa.Column("short_term_table", sa.JSON(), nullable=False),
            sa.Column("long_term_table", sa.JSON(), nullable=False),
            sa.Column("semantic_mapping", sa.JSON(), nullable=False),
            sa.Column("result_payload", sa.JSON(), nullable=False),
            sa.Column("error", sa.String(length=1000), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("analyzed_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id"),
            sa.UniqueConstraint("run_id"),
            sa.UniqueConstraint("user_id", "request_id", name="uq_voice_event_user_request"),
            sa.UniqueConstraint("relationship_id", "event_seq", name="uq_voice_event_sequence"),
        )
        for column in (
            "event_id",
            "request_id",
            "run_id",
            "relationship_id",
            "user_id",
            "device_session_id",
            "event_seq",
            "status",
        ):
            op.create_index(f"ix_voice_events_{column}", "voice_events", [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "voice_events" in tables:
        op.drop_table("voice_events")
    if "relationship_wallpaper_states" in tables and "next_event_seq" in _columns(inspector, "relationship_wallpaper_states"):
        op.drop_column("relationship_wallpaper_states", "next_event_seq")
    if "relationship_states" in tables:
        columns = _columns(inspector, "relationship_states")
        if "last_applied_event_seq" in columns:
            op.drop_column("relationship_states", "last_applied_event_seq")
        if "version" in columns:
            op.drop_column("relationship_states", "version")
    if "device_sessions" in tables and "device_id" in _columns(inspector, "device_sessions"):
        op.drop_index("ix_device_sessions_device_id", table_name="device_sessions")
        op.drop_column("device_sessions", "device_id")
