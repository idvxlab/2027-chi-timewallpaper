from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_scene_default_is_calm() -> None:
    res = client.get("/scene")
    assert res.status_code == 200
    data = res.json()
    # 后端用 alias 序列化为 camelCase
    assert data.get("demoId") == "calm"
    assert "hotspots" in data
    assert isinstance(data["hotspots"], list)


def test_switch_demo_longing() -> None:
    res = client.post("/demo/longing")
    assert res.status_code == 200
    data = res.json()
    assert data["demoId"] == "longing"
    assert data["glow"] is True
    assert data["ambient"] == "/audio/ambient.mp3"


def test_switch_demo_fatigue() -> None:
    res = client.post("/demo/fatigue")
    assert res.status_code == 200
    assert res.json()["demoId"] == "fatigue"


def test_switch_demo_await_response() -> None:
    res = client.post("/demo/await_response")
    assert res.status_code == 200
    assert res.json()["demoId"] == "await_response"


def test_switch_demo_unknown() -> None:
    res = client.post("/demo/nope")
    assert res.status_code == 404
