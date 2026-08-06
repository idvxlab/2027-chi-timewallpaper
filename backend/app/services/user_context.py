from __future__ import annotations

import uuid
import secrets
from datetime import datetime

from sqlalchemy import inspect, text

from app.db.models import RelationshipProfile, UserProfile
from app.db.session import Base, SessionLocal, engine

DEFAULT_PARENT_USER_ID = "mother-demo"
DEFAULT_CHILD_USER_ID = "child-demo"
DEFAULT_RELATIONSHIP_ID = "family-demo"

VIEWER_ROLE_USER_IDS = {
    "elder": DEFAULT_PARENT_USER_ID,
    "child": DEFAULT_CHILD_USER_ID,
}


class RelationshipAccessError(Exception):
    pass


class RelationshipInvitationNotFoundError(Exception):
    pass


class RelationshipJoinConflictError(Exception):
    pass


def upsert_onboarding_profile(
    viewer_role: str,
    display_name: str,
    gender: str,
    user_id: str | None = None,
    relationship_id: str | None = None,
) -> dict[str, object]:
    _validate_profile(viewer_role, display_name, gender)

    counterpart_role = "child" if viewer_role == "elder" else "elder"
    family_role = _family_role(viewer_role, gender)
    display_name = display_name.strip()
    Base.metadata.create_all(bind=engine)
    _ensure_relationship_profile_columns()
    now = datetime.utcnow()

    with SessionLocal() as session:
        if relationship_id is None:
            _begin_serialized_write(session)
            resolved_user_id = user_id or _new_user_id()
            user = _upsert_user(
                session,
                resolved_user_id,
                viewer_role,
                display_name,
                gender,
                family_role,
                now,
            )
            relationship = RelationshipProfile(
                relationship_id=_new_relationship_id(),
                invite_code=_new_invite_code(session),
                display_name="",
                parent_user_id=resolved_user_id if viewer_role == "elder" else "",
                child_user_id=resolved_user_id if viewer_role == "child" else "",
                parent_role=family_role if viewer_role == "elder" else "mother",
                child_role=family_role if viewer_role == "child" else "daughter",
                relation_type="parent_child",
                profile={
                    "createdByRole": viewer_role,
                    "onboardingParticipants": {viewer_role: True},
                },
                created_at=now,
                updated_at=now,
            )
            session.add(relationship)
        else:
            if not user_id:
                raise RelationshipAccessError(
                    "userId is required when updating an existing relationship"
                )
            relationship = (
                session.query(RelationshipProfile)
                .filter(RelationshipProfile.relationship_id == relationship_id)
                .one_or_none()
            )
            if relationship is None:
                raise RelationshipAccessError("relationship not found")
            expected_user_id = (
                relationship.parent_user_id
                if viewer_role == "elder"
                else relationship.child_user_id
            )
            if expected_user_id != user_id:
                raise RelationshipAccessError("user does not belong to this relationship role")
            resolved_user_id = user_id
            user = _upsert_user(
                session,
                resolved_user_id,
                viewer_role,
                display_name,
                gender,
                family_role,
                now,
            )
            if viewer_role == "elder":
                relationship.parent_role = family_role
            else:
                relationship.child_role = family_role

        session.flush()
        parent, child = _load_relationship_users(session, relationship)
        relationship.display_name = _relationship_display_name(relationship, parent, child)
        relationship.updated_at = now
        result = _profile_result(
            user=user,
            relationship=relationship,
            viewer_role=viewer_role,
            counterpart_role=counterpart_role,
            family_role=family_role,
            display_name=display_name,
            gender=gender,
            invite_code=relationship.invite_code,
        )
        session.commit()
        return result


def get_relationship_invitation(invite_code: str) -> dict[str, str]:
    normalized_code = invite_code.strip()
    Base.metadata.create_all(bind=engine)
    _ensure_relationship_profile_columns()
    with SessionLocal() as session:
        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.invite_code == normalized_code)
            .one_or_none()
        )
        if relationship is None:
            raise RelationshipInvitationNotFoundError("invitation code is invalid or expired")

        participants = (relationship.profile or {}).get("onboardingParticipants", {})
        creator_role = (relationship.profile or {}).get("createdByRole")
        if creator_role not in VIEWER_ROLE_USER_IDS:
            creator_role = "elder" if participants.get("elder") else "child"
        required_role = "child" if creator_role == "elder" else "elder"
        return {
            "invite_code": normalized_code,
            "relationship_id": relationship.relationship_id,
            "relationship_display_name": relationship.display_name,
            "creator_role": creator_role,
            "required_role": required_role,
            "status": "waiting",
        }


def join_relationship_by_invite(
    invite_code: str,
    viewer_role: str,
    display_name: str,
    gender: str,
) -> dict[str, object]:
    _validate_profile(viewer_role, display_name, gender)
    normalized_code = invite_code.strip()
    display_name = display_name.strip()
    Base.metadata.create_all(bind=engine)
    _ensure_relationship_profile_columns()
    now = datetime.utcnow()

    with SessionLocal() as session:
        _begin_serialized_write(session)
        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.invite_code == normalized_code)
            .with_for_update()
            .one_or_none()
        )
        if relationship is None:
            raise RelationshipInvitationNotFoundError("invitation code is invalid or expired")

        relationship_profile = relationship.profile or {}
        participants = relationship_profile.get("onboardingParticipants", {})
        creator_role = relationship_profile.get("createdByRole")
        if creator_role not in VIEWER_ROLE_USER_IDS:
            creator_role = "elder" if participants.get("elder") else "child"
        required_role = "child" if creator_role == "elder" else "elder"
        if viewer_role != required_role or participants.get(viewer_role):
            raise RelationshipJoinConflictError(
                f"this invitation must be joined as {required_role}"
            )

        resolved_user_id = _new_user_id()
        family_role = _family_role(viewer_role, gender)
        user = _upsert_user(
            session,
            resolved_user_id,
            viewer_role,
            display_name,
            gender,
            family_role,
            now,
        )
        if viewer_role == "elder":
            relationship.parent_user_id = resolved_user_id
            relationship.parent_role = family_role
        else:
            relationship.child_user_id = resolved_user_id
            relationship.child_role = family_role
        relationship.profile = {
            **relationship_profile,
            "onboardingParticipants": {**participants, viewer_role: True},
        }
        session.flush()
        parent, child = _load_relationship_users(session, relationship)
        relationship.display_name = _relationship_display_name(relationship, parent, child)
        relationship.updated_at = now
        relationship.invite_code = None
        result = _profile_result(
            user=user,
            relationship=relationship,
            viewer_role=viewer_role,
            counterpart_role=creator_role,
            family_role=family_role,
            display_name=display_name,
            gender=gender,
            invite_code=normalized_code,
        )
        session.commit()
        return result


def get_speaker_context(user_id: str | None, relationship_id: str | None) -> dict[str, str]:
    fallback = _fallback_speaker_context(user_id, relationship_id)
    if not user_id or not relationship_id:
        return fallback

    with SessionLocal() as session:
        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.relationship_id == relationship_id)
            .one_or_none()
        )
        if relationship is None:
            return fallback

        if user_id == relationship.parent_user_id:
            speaker_role = "parent"
            counterpart_user_id = relationship.child_user_id
        elif user_id == relationship.child_user_id:
            speaker_role = "child"
            counterpart_user_id = relationship.parent_user_id
        else:
            return fallback

        speaker = session.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
        counterpart = (
            session.query(UserProfile)
            .filter(UserProfile.user_id == counterpart_user_id)
            .one_or_none()
        )
        return {
            "user_id": user_id,
            "relationship_id": relationship_id,
            "speaker_role": speaker_role,
            "speaker_label": (speaker.display_name if speaker else "") or fallback["speaker_label"],
            "counterpart_label": (counterpart.display_name if counterpart else "") or fallback["counterpart_label"],
        }


def _validate_profile(viewer_role: str, display_name: str, gender: str) -> None:
    if viewer_role not in VIEWER_ROLE_USER_IDS:
        raise ValueError("viewer_role must be elder or child")
    if gender not in {"male", "female"}:
        raise ValueError("gender must be male or female")
    if not display_name.strip():
        raise ValueError("display_name must not be blank")


def _upsert_user(
    session,
    user_id: str,
    viewer_role: str,
    display_name: str,
    gender: str,
    family_role: str,
    now: datetime,
) -> UserProfile:
    user = session.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
    if user is None:
        user = UserProfile(
            user_id=user_id,
            display_name=display_name,
            role=family_role,
            profile={},
            created_at=now,
            updated_at=now,
        )
        session.add(user)
    user.display_name = display_name
    user.role = family_role
    user.profile = {
        **(user.profile or {}),
        "gender": gender,
        "viewerRole": viewer_role,
        "onboardingCompleted": True,
        "onboardingStep": (user.profile or {}).get("onboardingStep", "photo"),
    }
    user.updated_at = now
    return user


def _load_relationship_users(
    session,
    relationship: RelationshipProfile,
) -> tuple[UserProfile | None, UserProfile | None]:
    parent = None
    child = None
    if relationship.parent_user_id:
        parent = (
            session.query(UserProfile)
            .filter(UserProfile.user_id == relationship.parent_user_id)
            .one_or_none()
        )
    if relationship.child_user_id:
        child = (
            session.query(UserProfile)
            .filter(UserProfile.user_id == relationship.child_user_id)
            .one_or_none()
        )
    return parent, child


def _profile_result(
    user: UserProfile,
    relationship: RelationshipProfile,
    viewer_role: str,
    counterpart_role: str,
    family_role: str,
    display_name: str,
    gender: str,
    invite_code: str | None,
) -> dict[str, object]:
    counterpart_user_id = (
        relationship.child_user_id
        if viewer_role == "elder"
        else relationship.parent_user_id
    )
    participants = (relationship.profile or {}).get("onboardingParticipants", {})
    status = (
        "connected"
        if participants.get("elder") and participants.get("child")
        else "waiting"
    )
    return {
        "user_id": user.user_id,
        "counterpart_user_id": counterpart_user_id or "",
        "relationship_id": relationship.relationship_id,
        "relationship_display_name": relationship.display_name,
        "invite_code": invite_code,
        "relationship_status": status,
        "viewer_role": viewer_role,
        "counterpart_role": counterpart_role,
        "family_role": family_role,
        "display_name": display_name,
        "gender": gender,
    }


def _family_role(viewer_role: str, gender: str) -> str:
    if viewer_role == "elder":
        return "father" if gender == "male" else "mother"
    return "son" if gender == "male" else "daughter"


def _new_user_id() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    return f"user-{date_part}-{uuid.uuid4().hex[:12]}"


def _new_relationship_id() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    return f"relationship-{date_part}-{uuid.uuid4().hex[:12]}"


def _new_invite_code(session) -> str:
    start = secrets.randbelow(10000)
    for offset in range(10000):
        code = f"{(start + offset) % 10000:04d}"
        exists = (
            session.query(RelationshipProfile.id)
            .filter(RelationshipProfile.invite_code == code)
            .first()
        )
        if exists is None:
            return code
    raise RuntimeError("all four-digit invitation codes are currently in use")


def _relationship_display_name(
    relationship: RelationshipProfile,
    parent: UserProfile | None,
    child: UserProfile | None,
) -> str:
    date_part = (relationship.created_at or datetime.utcnow()).strftime("%Y-%m-%d")
    participants = (relationship.profile or {}).get("onboardingParticipants", {})
    parent_ready = bool(participants.get("elder"))
    child_ready = bool(participants.get("child"))
    parent_name = parent.display_name if parent_ready and parent else "父母待加入"
    child_name = child.display_name if child_ready and child else "子女待加入"
    return f"{date_part}-{parent_name}-{child_name}"


def _fallback_speaker_context(user_id: str | None, relationship_id: str | None) -> dict[str, str]:
    role = "unknown"
    speaker_label = "当前说话者"
    counterpart_label = "对方"
    if user_id == DEFAULT_PARENT_USER_ID:
        role = "parent"
        speaker_label = "妈妈"
        counterpart_label = "女儿"
    elif user_id == DEFAULT_CHILD_USER_ID:
        role = "child"
        speaker_label = "女儿"
        counterpart_label = "妈妈"
    return {
        "user_id": user_id or "",
        "relationship_id": relationship_id or "",
        "speaker_role": role,
        "speaker_label": speaker_label,
        "counterpart_label": counterpart_label,
    }


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
                    invite_code=None,
                    display_name="",
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
        inspector = inspect(connection)
        if not inspector.has_table("relationship_profiles"):
            return
        existing = {
            column["name"]
            for column in inspector.get_columns("relationship_profiles")
        }
        if not existing:
            return
        if "parent_user_id" not in existing:
            connection.exec_driver_sql("ALTER TABLE relationship_profiles ADD COLUMN parent_user_id VARCHAR(64)")
            if "elder_user_id" in existing:
                connection.exec_driver_sql(
                    "UPDATE relationship_profiles SET parent_user_id = elder_user_id WHERE parent_user_id IS NULL"
                )
        if "display_name" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE relationship_profiles ADD COLUMN display_name VARCHAR(192) DEFAULT ''"
            )
        if "invite_code" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE relationship_profiles ADD COLUMN invite_code VARCHAR(4)"
            )
        index_names = {
            item["name"] for item in inspector.get_indexes("relationship_profiles")
        }
        constraint_names = {
            item["name"]
            for item in inspector.get_unique_constraints("relationship_profiles")
            if item.get("name")
        }
        if "ix_relationship_profiles_invite_code" not in index_names | constraint_names:
            # Unique indexes in SQLite/MySQL both allow multiple NULL values,
            # so a partial-index predicate is unnecessary.
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX ix_relationship_profiles_invite_code "
                "ON relationship_profiles (invite_code)"
            )
        if "parent_role" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE relationship_profiles ADD COLUMN parent_role VARCHAR(32) DEFAULT 'mother'"
            )
        if "child_role" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE relationship_profiles ADD COLUMN child_role VARCHAR(32) DEFAULT 'daughter'"
            )


def _begin_serialized_write(session) -> None:
    """Use SQLite's immediate lock; MySQL is serialized with row locks."""
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
