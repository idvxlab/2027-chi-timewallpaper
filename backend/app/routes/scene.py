from fastapi import APIRouter, Depends

from app.core.scene_store import SceneStore, get_store
from app.schemas.scene import Scene

router = APIRouter()


@router.get("", response_model=Scene)
async def get_scene(store: SceneStore = Depends(get_store)) -> Scene:
    return await store.get()
