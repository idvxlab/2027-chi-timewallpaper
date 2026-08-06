from fastapi import APIRouter, HTTPException, Response

from app.schemas.onboarding import OnboardingProfileIn, OnboardingProfileOut
from app.services.session_service import issue_device_session, set_device_session_cookie
from app.services.user_context import RelationshipAccessError, upsert_onboarding_profile


router = APIRouter()


@router.post("/profile", response_model=OnboardingProfileOut)
async def create_or_update_profile(
    body: OnboardingProfileIn,
    response: Response,
) -> OnboardingProfileOut:
    try:
        result = upsert_onboarding_profile(
            viewer_role=body.viewer_role,
            display_name=body.display_name,
            gender=body.gender,
            user_id=body.user_id,
            relationship_id=body.relationship_id,
        )
    except RelationshipAccessError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token = issue_device_session(
        user_id=str(result["user_id"]),
        relationship_id=str(result["relationship_id"]),
    )
    set_device_session_cookie(response, token)
    return OnboardingProfileOut(**result)
