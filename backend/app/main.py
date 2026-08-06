from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.wallpaper_events import wallpaper_event_hub
from app.core.cors import build_cors_origins
from app.db.session import Base, engine
from app.db.compat import ensure_concurrency_schema
# noqa: 让 SQLAlchemy 知道要创建哪些表
import app.db.models  # noqa: F401
from app.routes import scene, demo, touch, asr, logs, ws, agent_runs, chatbox, character_assets, onboarding, relationships, sessions, subject, wallpapers


def create_app() -> FastAPI:
    app = FastAPI(title="Time Wallpaper API", version="0.2.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=build_cors_origins(settings.app_cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    generated_dir = Path(settings.storage_local_dir) / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/generated", StaticFiles(directory=generated_dir), name="generated")
    references_dir = Path(settings.storage_local_dir) / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/references", StaticFiles(directory=references_dir), name="references")
    audio_inputs_dir = Path(settings.storage_local_dir) / "audio-inputs"
    audio_inputs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/audio-inputs", StaticFiles(directory=audio_inputs_dir), name="audio-inputs")
    character_assets_dir = Path(settings.storage_local_dir) / "character-assets"
    character_assets_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/character-assets/files",
        StaticFiles(directory=character_assets_dir),
        name="character-assets-files",
    )
    style_references_dir = Path(settings.storage_local_dir) / "style-references"
    style_references_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/style-references",
        StaticFiles(directory=style_references_dir),
        name="style-references",
    )

    @app.on_event("startup")
    async def _init_db() -> None:
        ensure_concurrency_schema()
        await wallpaper_event_hub.start()

    @app.on_event("shutdown")
    async def _shutdown_services() -> None:
        from app.services.render_queue_service import render_queue_service

        await wallpaper_event_hub.stop()
        await render_queue_service.close()

    app.include_router(scene.router, prefix="/scene", tags=["scene"])
    app.include_router(demo.router, prefix="/demo", tags=["demo"])
    app.include_router(touch.router, prefix="/touch", tags=["touch"])
    app.include_router(asr.router, prefix="/asr", tags=["asr"])
    app.include_router(chatbox.router, tags=["chatbox"])
    app.include_router(agent_runs.router, prefix="/agent-runs", tags=["agent-runs"])
    app.include_router(character_assets.router, prefix="/character-assets", tags=["character-assets"])
    app.include_router(onboarding.router, prefix="/onboarding", tags=["onboarding"])
    app.include_router(relationships.router, prefix="/relationships", tags=["relationships"])
    app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
    app.include_router(subject.router, prefix="/extract-subject", tags=["subject"])
    app.include_router(wallpapers.router, prefix="/wallpapers", tags=["wallpapers"])
    app.include_router(logs.router, prefix="/logs", tags=["logs"])
    app.include_router(ws.router, tags=["ws"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()
