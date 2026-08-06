from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.main import app
from app.services import subject_extraction_service


def test_extract_subject_v2_returns_fallback_cutout(tmp_path, monkeypatch) -> None:
    storage_dir = tmp_path / "uploads"
    generated_dir = storage_dir / "generated"
    generated_dir.mkdir(parents=True)
    source_path = generated_dir / "wallpaper-test.png"
    Image.new("RGB", (120, 200), (120, 180, 100)).save(source_path)

    monkeypatch.setattr(settings, "storage_local_dir", str(storage_dir))
    monkeypatch.setattr(settings, "public_api_base_url", "http://testserver")
    monkeypatch.setattr(subject_extraction_service, "_run_rembg", lambda image: None)

    with TestClient(app) as client:
        response = client.post(
            "/extract-subject/extract-subject-v2",
            json={
                "imageUrl": "http://testserver/generated/wallpaper-test.png",
                "viewerRole": "child",
                "region": "lower",
                "pointX": 0.25,
                "pointY": 0.75,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fallback"] is True
    assert payload["raw"]["personRole"] == "elder"
    assert payload["cutoutUrl"].startswith("http://testserver/generated/cutout-")
    output_name = payload["cutoutUrl"].rsplit("/", 1)[1]
    assert (generated_dir / output_name).is_file()


def test_extract_subject_rejects_non_generated_url() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/extract-subject/extract-subject-v2",
            json={
                "imageUrl": "https://example.com/wallpaper.png",
                "viewerRole": "elder",
                "region": "upper",
                "pointX": 0.75,
                "pointY": 0.2,
            },
        )

    assert response.status_code == 400


def test_person_role_is_fixed_by_region_for_both_viewers() -> None:
    assert subject_extraction_service.resolve_person_role("elder", "upper") == "young"
    assert subject_extraction_service.resolve_person_role("child", "upper") == "young"
    assert subject_extraction_service.resolve_person_role("elder", "lower") == "elder"
    assert subject_extraction_service.resolve_person_role("child", "lower") == "elder"
