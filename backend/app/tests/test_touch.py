from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_touch_returns_event() -> None:
    res = client.post("/touch", json={"hotspotId": "lamp"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "scene" in body
    assert body["event"]["toast"]


def test_touch_unknown_hotspot() -> None:
    res = client.post("/touch", json={"hotspotId": "ghost"})
    assert res.status_code == 200
    assert res.json()["event"]["toast"] == "已收到你的触碰～"
