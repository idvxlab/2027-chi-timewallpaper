from fastapi import APIRouter, Depends, HTTPException

from app.core.scene_store import SceneStore, get_store
from app.schemas.scene import Scene
from app.services.scene_mapper import apply_demo

router = APIRouter()


@router.post("/{demo_id}", response_model=Scene)
async def switch_demo(
    demo_id: str,
    store: SceneStore = Depends(get_store),
) -> Scene:
    try:
        scene = apply_demo(demo_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown demo: {demo_id}")
    return await store.set(scene)
