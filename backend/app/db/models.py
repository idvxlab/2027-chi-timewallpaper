from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, JSON
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
