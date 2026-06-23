from fastapi import APIRouter, Depends

from app.core.scene_store import SceneStore, get_store
from app.schemas.api import TouchIn, TouchOut
from app.services.touch_service import handle_touch

router = APIRouter()


@router.post("", response_model=TouchOut)
async def post_touch(
    body: TouchIn,
    store: SceneStore = Depends(get_store),
) -> TouchOut:
    return await handle_touch(body, store)
