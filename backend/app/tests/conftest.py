import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def disable_external_render_queue(monkeypatch):
    """Unit tests keep deterministic in-process rendering unless explicitly overridden."""
    monkeypatch.setattr(settings, "render_queue_enabled", False)
