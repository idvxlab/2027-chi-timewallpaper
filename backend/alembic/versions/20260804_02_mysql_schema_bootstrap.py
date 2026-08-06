"""Ensure the complete application schema exists on a fresh MySQL database.

Revision ID: 20260804_02
Revises: 20260804_01
Create Date: 2026-08-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260804_02"
down_revision: Union[str, None] = "20260804_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Revision 01 was initially written before accounting for Alembic's own
    # version table. create_all is idempotent and fills only missing tables.
    from app.db.session import Base
    import app.db.models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # This repair revision may coexist with databases whose tables predated
    # Alembic, so it must not guess which application tables it owns.
    pass
