from __future__ import annotations

from datetime import datetime

from app.db.models import RelationshipProfile, UserProfile
from app.db.session import Base, SessionLocal, engine

DEFAULT_PARENT_USER_ID = "mother-demo"
DEFAULT_CHILD_USER_ID = "child-demo"
DEFAULT_RELATIONSHIP_ID = "family-demo"


def normalize_user_context(user_id: str | None, relationship_id: str | None) -> tuple[str, str]:
    resolved_relationship_id = relationship_id or DEFAULT_RELATIONSHIP_ID
    resolved_user_id = user_id or DEFAULT_PARENT_USER_ID
    ensure_user_context(resolved_user_id, resolved_relationship_id)
    return resolved_user_id, resolved_relationship_id


def ensure_user_context(user_id: str, relationship_id: str) -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_relationship_profile_columns()
    now = datetime.utcnow()
    with SessionLocal() as session:
        parent = session.query(UserProfile).filter(UserProfile.user_id == DEFAULT_PARENT_USER_ID).one_or_none()
        if parent is None:
            session.add(
                UserProfile(
                    user_id=DEFAULT_PARENT_USER_ID,
                    display_name="母亲 Demo",
                    role="mother",
                    profile={},
                    created_at=now,
                    updated_at=now,
                )
            )
        child = session.query(UserProfile).filter(UserProfile.user_id == DEFAULT_CHILD_USER_ID).one_or_none()
        if child is None:
            session.add(
                UserProfile(
                    user_id=DEFAULT_CHILD_USER_ID,
                    display_name="女儿 Demo",
                    role="daughter",
                    profile={},
                    created_at=now,
                    updated_at=now,
                )
            )
        current_user = session.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
        if current_user is None and user_id not in {DEFAULT_PARENT_USER_ID, DEFAULT_CHILD_USER_ID}:
            session.add(
                UserProfile(
                    user_id=user_id,
                    display_name=user_id,
                    role="unknown",
                    profile={},
                    created_at=now,
                    updated_at=now,
                )
            )

        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.relationship_id == relationship_id)
            .one_or_none()
        )
        if relationship is None:
            session.add(
                RelationshipProfile(
                    relationship_id=relationship_id,
                    parent_user_id=DEFAULT_PARENT_USER_ID,
                    child_user_id=DEFAULT_CHILD_USER_ID,
                    parent_role="mother",
                    child_role="daughter",
                    relation_type="parent_child",
                    profile={},
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()


def _ensure_relationship_profile_columns() -> None:
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(relationship_profiles)").fetchall()
        }
        if not existing:
            return
        if "parent_user_id" not in existing:
            connection.exec_driver_sql("ALTER TABLE relationship_profiles ADD COLUMN parent_user_id VARCHAR(64)")
            if "elder_user_id" in existing:
                connection.exec_driver_sql(
                    "UPDATE relationship_profiles SET parent_user_id = elder_user_id WHERE parent_user_id IS NULL"
                )
        if "parent_role" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE relationship_profiles ADD COLUMN parent_role VARCHAR(32) DEFAULT 'mother'"
            )
        if "child_role" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE relationship_profiles ADD COLUMN child_role VARCHAR(32) DEFAULT 'daughter'"
            )
