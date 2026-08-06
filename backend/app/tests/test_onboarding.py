from fastapi.testclient import TestClient

from app.agents.language_emotion_agent import LanguageEmotionAgent
from app.db.models import RelationshipProfile, UserProfile
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def _create_child_profile(name: str = "小林") -> dict:
    response = client.post(
        "/onboarding/profile",
        json={"viewerRole": "child", "displayName": name, "gender": "female"},
    )
    assert response.status_code == 200
    return response.json()


def _unused_invite_code() -> str:
    with SessionLocal() as session:
        used = {
            code
            for (code,) in session.query(RelationshipProfile.invite_code).all()
            if code is not None
        }
    return next(f"{value:04d}" for value in range(10000) if f"{value:04d}" not in used)


def test_first_profile_creates_user_relationship_and_four_digit_invite() -> None:
    payload = _create_child_profile()

    assert payload["userId"].startswith("user-")
    assert payload["counterpartUserId"] == ""
    assert payload["relationshipId"].startswith("relationship-")
    assert len(payload["relationshipId"].split("-")) == 3
    assert payload["inviteCode"].isdigit()
    assert len(payload["inviteCode"]) == 4
    assert payload["relationshipStatus"] == "waiting"
    assert "父母待加入" in payload["relationshipDisplayName"]
    assert payload["relationshipDisplayName"].endswith("-小林")
    assert payload["viewerRole"] == "child"
    assert payload["counterpartRole"] == "elder"
    assert payload["familyRole"] == "daughter"

    with SessionLocal() as session:
        child = (
            session.query(UserProfile)
            .filter(UserProfile.user_id == payload["userId"])
            .one()
        )
        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.relationship_id == payload["relationshipId"])
            .one()
        )
        assert child.display_name == "小林"
        assert child.role == "daughter"
        assert relationship.parent_user_id == ""
        assert relationship.child_user_id == payload["userId"]
        assert relationship.invite_code == payload["inviteCode"]
        assert relationship.profile["onboardingParticipants"] == {"child": True}

    context = LanguageEmotionAgent()._speaker_context(
        payload["userId"], payload["relationshipId"]
    )
    assert context["speaker_role"] == "child"
    assert context["speaker_label"] == "小林"


def test_invitation_lookup_reports_required_role() -> None:
    child = _create_child_profile("小雨")

    response = client.get(f"/relationships/invitations/{child['inviteCode']}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["inviteCode"] == child["inviteCode"]
    assert payload["relationshipId"] == child["relationshipId"]
    assert payload["creatorRole"] == "child"
    assert payload["requiredRole"] == "elder"
    assert payload["status"] == "waiting"


def test_invitation_rejects_same_role_then_connects_counterpart() -> None:
    child = _create_child_profile("小雨")
    invite_code = child["inviteCode"]

    same_role_response = client.post(
        "/relationships/join",
        json={
            "inviteCode": invite_code,
            "viewerRole": "child",
            "displayName": "另一位子女",
            "gender": "male",
        },
    )
    assert same_role_response.status_code == 409

    parent_response = client.post(
        "/relationships/join",
        json={
            "inviteCode": invite_code,
            "viewerRole": "elder",
            "displayName": "王阿姨",
            "gender": "female",
        },
    )

    assert parent_response.status_code == 200
    parent = parent_response.json()
    assert parent["userId"].startswith("user-")
    assert parent["userId"] != child["userId"]
    assert parent["counterpartUserId"] == child["userId"]
    assert parent["relationshipId"] == child["relationshipId"]
    assert parent["relationshipStatus"] == "connected"
    assert parent["relationshipDisplayName"].endswith("-王阿姨-小雨")

    with SessionLocal() as session:
        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.relationship_id == child["relationshipId"])
            .one()
        )
        assert relationship.parent_user_id == parent["userId"]
        assert relationship.child_user_id == child["userId"]
        assert relationship.invite_code is None
        assert relationship.profile["onboardingParticipants"] == {
            "child": True,
            "elder": True,
        }

    expired_response = client.get(f"/relationships/invitations/{invite_code}")
    assert expired_response.status_code == 404


def test_profile_update_requires_matching_user_and_relationship() -> None:
    child = _create_child_profile("小雨")

    response = client.post(
        "/onboarding/profile",
        json={
            "viewerRole": "child",
            "displayName": "小雨新名字",
            "gender": "female",
            "userId": child["userId"],
            "relationshipId": child["relationshipId"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["userId"] == child["userId"]
    assert payload["relationshipId"] == child["relationshipId"]
    assert payload["inviteCode"] == child["inviteCode"]
    assert payload["relationshipDisplayName"].endswith("-小雨新名字")


def test_invalid_or_expired_invitation_returns_not_found() -> None:
    missing_code = _unused_invite_code()
    response = client.post(
        "/relationships/join",
        json={
            "inviteCode": missing_code,
            "viewerRole": "elder",
            "displayName": "测试用户",
            "gender": "male",
        },
    )

    assert response.status_code == 404


def test_profile_rejects_unknown_viewer_role() -> None:
    response = client.post(
        "/onboarding/profile",
        json={"viewerRole": "friend", "displayName": "测试", "gender": "female"},
    )

    assert response.status_code == 422
