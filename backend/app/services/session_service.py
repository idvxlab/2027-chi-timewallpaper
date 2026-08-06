from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from app.db.models import DeviceSession, RelationshipProfile, UserProfile
from app.db.session import Base, SessionLocal, engine


SESSION_COOKIE_NAME = "timewallpaper_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 90
STEP_ORDER = {"photo": 0, "prelude": 1, "wallpaper": 2}


class SessionAuthenticationError(Exception):
    pass


def issue_device_session(
    user_id: str,
    relationship_id: str,
    device_id: str | None = None,
) -> str:
    Base.metadata.create_all(bind=engine)
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    with SessionLocal() as session:
        session.add(
            DeviceSession(
                session_id=uuid.uuid4().hex,
                token_hash=_hash_token(token),
                device_id=device_id or uuid.uuid4().hex,
                user_id=user_id,
                relationship_id=relationship_id,
                expires_at=now + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
                revoked_at=None,
                created_at=now,
                last_seen_at=now,
            )
        )
        session.commit()
    return token


def set_device_session_cookie(response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )


def get_current_session(token: str | None) -> dict[str, object]:
    with SessionLocal() as session:
        device_session = _load_active_session(session, token)
        result = _session_result(session, device_session)
        device_session.last_seen_at = datetime.utcnow()
        session.commit()
        return result


def update_session_progress(
    token: str | None,
    onboarding_step: str,
    wallpaper_url: str | None = None,
) -> dict[str, object]:
    if onboarding_step not in STEP_ORDER:
        raise ValueError("invalid onboarding step")

    with SessionLocal() as session:
        device_session = _load_active_session(session, token)
        user = (
            session.query(UserProfile)
            .filter(UserProfile.user_id == device_session.user_id)
            .one_or_none()
        )
        if user is None:
            raise SessionAuthenticationError("session user no longer exists")

        profile = user.profile or {}
        current_step = profile.get("onboardingStep", "photo")
        if STEP_ORDER.get(onboarding_step, 0) >= STEP_ORDER.get(current_step, 0):
            profile = {**profile, "onboardingStep": onboarding_step}
        if wallpaper_url:
            profile = {**profile, "latestWallpaperUrl": wallpaper_url}
        user.profile = profile
        user.updated_at = datetime.utcnow()
        device_session.last_seen_at = datetime.utcnow()
        session.flush()
        result = _session_result(session, device_session)
        session.commit()
        return result


def revoke_device_session(token: str | None) -> None:
    if not token:
        return
    with SessionLocal() as session:
        device_session = (
            session.query(DeviceSession)
            .filter(DeviceSession.token_hash == _hash_token(token))
            .one_or_none()
        )
        if device_session is not None and device_session.revoked_at is None:
            device_session.revoked_at = datetime.utcnow()
            session.commit()


def _load_active_session(session, token: str | None) -> DeviceSession:
    if not token:
        raise SessionAuthenticationError("device session is missing")
    device_session = (
        session.query(DeviceSession)
        .filter(DeviceSession.token_hash == _hash_token(token))
        .one_or_none()
    )
    now = datetime.utcnow()
    if (
        device_session is None
        or device_session.revoked_at is not None
        or device_session.expires_at <= now
    ):
        raise SessionAuthenticationError("device session is invalid or expired")
    return device_session


def _session_result(session, device_session: DeviceSession) -> dict[str, object]:
    user = (
        session.query(UserProfile)
        .filter(UserProfile.user_id == device_session.user_id)
        .one_or_none()
    )
    relationship = (
        session.query(RelationshipProfile)
        .filter(RelationshipProfile.relationship_id == device_session.relationship_id)
        .one_or_none()
    )
    if user is None or relationship is None:
        raise SessionAuthenticationError("session account no longer exists")

    if user.user_id == relationship.parent_user_id:
        viewer_role = "elder"
        counterpart_role = "child"
        counterpart_user_id = relationship.child_user_id
    elif user.user_id == relationship.child_user_id:
        viewer_role = "child"
        counterpart_role = "elder"
        counterpart_user_id = relationship.parent_user_id
    else:
        raise SessionAuthenticationError("session user is not a relationship member")

    participants = (relationship.profile or {}).get("onboardingParticipants", {})
    relationship_status = (
        "connected"
        if participants.get("elder") and participants.get("child")
        else "waiting"
    )
    profile = user.profile or {}
    gender = profile.get("gender")
    if gender not in {"male", "female"}:
        gender = "female"
    onboarding_step = profile.get("onboardingStep", "photo")
    if onboarding_step not in STEP_ORDER:
        onboarding_step = "photo"
    if relationship_status == "waiting":
        onboarding_step = "pairing"

    return {
        "session_id": device_session.session_id,
        "device_id": device_session.device_id,
        "user_id": user.user_id,
        "counterpart_user_id": counterpart_user_id or "",
        "relationship_id": relationship.relationship_id,
        "relationship_display_name": relationship.display_name,
        "invite_code": relationship.invite_code,
        "relationship_status": relationship_status,
        "viewer_role": viewer_role,
        "counterpart_role": counterpart_role,
        "family_role": user.role,
        "display_name": user.display_name,
        "gender": gender,
        "onboarding_step": onboarding_step,
        "wallpaper_url": profile.get("latestWallpaperUrl", ""),
    }


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
