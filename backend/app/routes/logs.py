from fastapi import APIRouter, Query

from app.services.log_service import list_logs

router = APIRouter()


@router.get("")
async def get_logs(
    kind: str = Query("touch", pattern="^(touch|asr|all)$"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    return await list_logs(kind=kind, limit=limit)
