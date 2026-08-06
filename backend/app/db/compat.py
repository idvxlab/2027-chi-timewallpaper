from __future__ import annotations

from sqlalchemy import inspect, text

from app.db.session import Base, engine


def ensure_concurrency_schema() -> None:
    """Keep existing development DBs usable before their first Alembic run.

    Production deployments should run ``alembic upgrade head``. This small
    compatibility bridge exists because the repository already contains an
    SQLite development database that predates Alembic.
    """
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    statements: list[str] = []

    if "device_sessions" in tables:
        columns = {item["name"] for item in inspector.get_columns("device_sessions")}
        if "device_id" not in columns:
            statements.append(
                "ALTER TABLE device_sessions ADD COLUMN device_id VARCHAR(64) NOT NULL DEFAULT ''"
            )

    if "relationship_states" in tables:
        columns = {item["name"] for item in inspector.get_columns("relationship_states")}
        if "version" not in columns:
            statements.append(
                "ALTER TABLE relationship_states ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
            )
        if "last_applied_event_seq" not in columns:
            statements.append(
                "ALTER TABLE relationship_states ADD COLUMN last_applied_event_seq INTEGER NOT NULL DEFAULT 0"
            )

    if "relationship_wallpaper_states" in tables:
        columns = {
            item["name"]
            for item in inspector.get_columns("relationship_wallpaper_states")
        }
        if "next_event_seq" not in columns:
            statements.append(
                "ALTER TABLE relationship_wallpaper_states ADD COLUMN next_event_seq INTEGER NOT NULL DEFAULT 0"
            )

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
            if "relationship_wallpaper_states" in tables:
                connection.execute(
                    text(
                        "UPDATE relationship_wallpaper_states "
                        "SET next_event_seq = version WHERE next_event_seq < version"
                    )
                )

    # MySQL has no portable CREATE INDEX IF NOT EXISTS form. Inspect first so
    # this compatibility bridge works for both MySQL and SQLite.
    inspector = inspect(engine)
    device_indexes = {
        item["name"] for item in inspector.get_indexes("device_sessions")
    }
    if "ix_device_sessions_device_id" not in device_indexes:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX ix_device_sessions_device_id "
                    "ON device_sessions (device_id)"
                )
            )
