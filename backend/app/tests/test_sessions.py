import hashlib

from fastapi.testclient import TestClient

from app.db.models import DeviceSession
from app.db.session import SessionLocal
from app.main import app
from app.services.session_service import SESSION_COOKIE_NAME


def test_profile_issues_cookie_and_restores_progress() -> None:
    with TestClient(app) as browser:
        profile_response = browser.post(
            "/onboarding/profile",
            json={
                "viewerRole": "child",
                "displayName": "小雨",
                "gender": "female",
            },
        )
        assert profile_response.status_code == 200
        profile = profile_response.json()
        token = browser.cookies.get(SESSION_COOKIE_NAME)
        assert token
        assert "HttpOnly" in profile_response.headers["set-cookie"]

        with SessionLocal() as session:
            stored = (
                session.query(DeviceSession)
                .filter(DeviceSession.user_id == profile["userId"])
                .order_by(DeviceSession.created_at.desc())
                .first()
            )
            assert stored is not None
            assert stored.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
            assert stored.token_hash != token

        initial_session = browser.get("/sessions/me")
        assert initial_session.status_code == 200
        assert initial_session.json()["onboardingStep"] == "pairing"
        assert initial_session.json()["relationshipStatus"] == "waiting"

        with TestClient(app) as joiner:
            join_response = joiner.post(
                "/relationships/join",
                json={
                    "inviteCode": profile["inviteCode"],
                    "viewerRole": "elder",
                    "displayName": "雨妈妈",
                    "gender": "female",
                },
            )
            assert join_response.status_code == 200

        connected_session = browser.get("/sessions/me")
        assert connected_session.status_code == 200
        assert connected_session.json()["onboardingStep"] == "photo"
        assert connected_session.json()["relationshipStatus"] == "connected"

        prelude_response = browser.patch(
            "/sessions/me/progress",
            json={
                "onboardingStep": "prelude",
                "wallpaperUrl": "/generated/base-scene.png",
            },
        )
        assert prelude_response.status_code == 200
        assert prelude_response.json()["onboardingStep"] == "prelude"

        wallpaper_response = browser.patch(
            "/sessions/me/progress",
            json={
                "onboardingStep": "wallpaper",
                "wallpaperUrl": "/generated/final-wallpaper.png",
            },
        )
        assert wallpaper_response.status_code == 200
        assert wallpaper_response.json()["onboardingStep"] == "wallpaper"
        assert wallpaper_response.json()["wallpaperUrl"] == "/generated/final-wallpaper.png"

        restored = browser.get("/sessions/me")
        assert restored.status_code == 200
        assert restored.json()["onboardingStep"] == "wallpaper"
        assert restored.json()["wallpaperUrl"] == "/generated/final-wallpaper.png"


def test_creator_session_observes_later_join_from_another_device() -> None:
    with TestClient(app) as creator, TestClient(app) as joiner:
        child_response = creator.post(
            "/onboarding/profile",
            json={
                "viewerRole": "child",
                "displayName": "小林",
                "gender": "female",
            },
        )
        child = child_response.json()

        join_response = joiner.post(
            "/relationships/join",
            json={
                "inviteCode": child["inviteCode"],
                "viewerRole": "elder",
                "displayName": "林阿姨",
                "gender": "female",
            },
        )
        assert join_response.status_code == 200

        creator_session = creator.get("/sessions/me")
        assert creator_session.status_code == 200
        restored = creator_session.json()
        assert restored["relationshipStatus"] == "connected"
        assert restored["counterpartUserId"] == join_response.json()["userId"]
        assert restored["inviteCode"] is None


def test_logout_revokes_session() -> None:
    with TestClient(app) as browser:
        response = browser.post(
            "/onboarding/profile",
            json={
                "viewerRole": "elder",
                "displayName": "王叔叔",
                "gender": "male",
            },
        )
        assert response.status_code == 200
        assert browser.get("/sessions/me").status_code == 200

        logout_response = browser.post("/sessions/logout")
        assert logout_response.status_code == 204
        assert browser.get("/sessions/me").status_code == 401


def test_session_endpoint_requires_cookie() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/sessions/me").status_code == 401
