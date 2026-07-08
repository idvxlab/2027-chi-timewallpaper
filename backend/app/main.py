from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.cors import build_cors_origins
from app.routes import asr, wallpaper


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

    app.include_router(asr.router, prefix="/asr", tags=["asr"])
    app.include_router(wallpaper.router, prefix="/generate-wallpaper", tags=["wallpaper"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()
