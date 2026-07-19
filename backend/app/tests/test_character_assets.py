from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from app.main import app
from app.routes import character_assets
from app.services.character_asset_service import CharacterAssetService
from app.services.layered_tools.insert_characters_into_wallpaper_service import (
    _build_lower_left_current_prompt,
    _build_upper_right_other_prompt,
)
from app.services.layered_painter_tools import layered_painter_tools


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 48), (235, 220, 196)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_character_prompt_matches_base_picturebook_style() -> None:
    prompt = CharacterAssetService._build_prompt("elder")

    assert "儿童绘本" in prompt
    assert "手绘墨线水彩" in prompt
    assert "大块透明水彩" in prompt
    assert "不要碎线" in prompt
    assert "保留第一张照片人物的身份特征" in prompt
    assert "不生成房间" in prompt


def test_character_asset_upload_contract(monkeypatch) -> None:
    now = datetime.utcnow()

    async def fake_create(**kwargs):
        assert kwargs["user_id"] == "child-demo"
        assert kwargs["role"] == "child"
        assert kwargs["relationship_id"] == "memory-demo"
        assert kwargs["source_bytes"]
        assert kwargs["style_reference_bytes"]
        return SimpleNamespace(
            asset_id="char-test",
            user_id="child-demo",
            relationship_id="memory-demo",
            role="child",
            status="ready",
            style_version="classic-picturebook-line-watercolor-v1",
            source_image_url="/character-assets/files/char-test/source.png",
            style_reference_url="/character-assets/files/char-test/style-reference.png",
            master_image_url="/character-assets/files/char-test/master.png",
            portrait_image_url="/character-assets/files/char-test/portrait.png",
            half_body_image_url="/character-assets/files/char-test/half-body.png",
            full_body_image_url="/character-assets/files/char-test/master.png",
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(character_assets.character_asset_service, "create", fake_create)

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.post(
            "/character-assets",
            data={"userId": "child-demo", "relationshipId": "memory-demo", "role": "child"},
            files={
                "image": ("child.png", _png_bytes(), "image/png"),
                "styleReference": ("style.png", _png_bytes(), "image/png"),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assetId"] == "char-test"
    assert payload["masterImageUrl"].endswith("/master.png")
    assert payload["role"] == "child"


def test_insert_prompts_reuse_cartoon_asset_and_base_style() -> None:
    upper_prompt = _build_upper_right_other_prompt("elder parent/other side", True)
    lower_prompt = _build_lower_left_current_prompt(
        "今天在图书馆写报告，看电脑看得很累",
        "younger child/current speaker",
        True,
    )

    for prompt in (upper_prompt, lower_prompt):
        assert "已经定稿并可重复使用" in prompt
        assert "卡通人物资产" in prompt
        assert "儿童绘本线描水彩" in prompt
        assert "大块透明水彩" in prompt
        assert "不是待重新设计的照片" in prompt
        assert "不要换脸" in prompt

    assert "不要复制当前说话者本次事件" in upper_prompt
    assert "只在左下当前说话者一侧表现本次内容" in lower_prompt
    assert "今天在图书馆写报告，看电脑看得很累" in lower_prompt
    assert "不要用固定活动模板替换本次内容" in lower_prompt


def test_layered_painter_resolves_character_asset_static_url() -> None:
    path = layered_painter_tools._local_static_path(
        "/character-assets/files/char-test/master.png"
    )

    assert path is not None
    assert path.as_posix().endswith(
        "data/uploads/character-assets/char-test/master.png"
    )
