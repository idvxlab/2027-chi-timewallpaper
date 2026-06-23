from app.db.session import SessionLocal
from app.db.models import TouchLog, AsrLog


async def list_logs(kind: str, limit: int) -> list[dict]:
    with SessionLocal() as s:
        if kind == "touch":
            rows = s.query(TouchLog).order_by(TouchLog.id.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "kind": "touch",
                    "hotspotId": r.hotspot_id,
                    "demoId": r.demo_id,
                    "payload": r.payload,
                    "createdAt": r.created_at.isoformat(),
                }
                for r in rows
            ]
        if kind == "asr":
            rows = s.query(AsrLog).order_by(AsrLog.id.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "kind": "asr",
                    "transcript": r.transcript,
                    "raw": r.raw,
                    "createdAt": r.created_at.isoformat(),
                }
                for r in rows
            ]

        touch_rows = s.query(TouchLog).order_by(TouchLog.id.desc()).limit(limit).all()
        asr_rows = s.query(AsrLog).order_by(AsrLog.id.desc()).limit(limit).all()
        out: list[dict] = []
        for r in touch_rows:
            out.append(
                {
                    "id": r.id,
                    "kind": "touch",
                    "hotspotId": r.hotspot_id,
                    "demoId": r.demo_id,
                    "payload": r.payload,
                    "createdAt": r.created_at.isoformat(),
                }
            )
        for r in asr_rows:
            out.append(
                {
                    "id": r.id,
                    "kind": "asr",
                    "transcript": r.transcript,
                    "raw": r.raw,
                    "createdAt": r.created_at.isoformat(),
                }
            )
        out.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return out[:limit]
