from __future__ import annotations

import uuid
from datetime import datetime

from app.agents.semantic_mapping_agent import SemanticMappingAgent
from app.db.models import MessageLog, RelationshipProfile
from app.db.session import Base, SessionLocal, engine
from app.schemas.agent import LanguageEmotionResult
from app.services.semantic_graph import load_semantic_graph


def _cell(value, confidence: float = 1.0) -> dict:
    return {"value": value, "confidence": confidence, "evidence": "test"}


def _short_table(*, time: str, scene: str, event: str, objects: list[str]) -> dict:
    return {
        "A_situational_semantics": {
            "event": _cell(event),
            "scene": _cell(scene),
            "time": _cell(time),
            "subject": _cell(["当前说话者"]),
            "object": _cell(objects),
        },
        "B_affective_semantics": {
            "momentary_affect": _cell("平静"),
        },
        "C_communicative_semantics": {
            "intent_type": _cell("分享生活"),
        },
    }


def test_time_profiles_have_explicit_sun_sunset_and_moon_markers() -> None:
    profiles = {
        profile["id"]: profile
        for profile in load_semantic_graph().role_scoped_l1_mapping["time_profiles"]
    }

    assert "sun" in profiles["daytime"]["celestial_marker"]
    assert "setting sun" in profiles["dusk"]["celestial_marker"]
    assert "moon" in profiles["evening"]["celestial_marker"]
    assert "moon" in profiles["late_night"]["celestial_marker"]


def _long_table() -> dict:
    return {
        "E_relational_semantics": {
            "intimacy_distance": _cell("近"),
            "emotional_warmth": _cell("温暖"),
            "interaction_frequency": _cell("经常联系"),
            "reciprocity": _cell("双向"),
            "relationship_trend": _cell("靠近"),
        }
    }


def test_loads_latest_short_table_for_both_relationship_roles() -> None:
    Base.metadata.create_all(bind=engine)
    suffix = uuid.uuid4().hex[:10]
    relationship_id = f"semantic-role-{suffix}"
    elder_user_id = f"elder-{suffix}"
    child_user_id = f"child-{suffix}"
    child_table = _short_table(
        time="晚上",
        scene="单位",
        event="下班离开办公室并关灯",
        objects=["电脑", "文件"],
    )

    with SessionLocal() as session:
        session.add(
            RelationshipProfile(
                relationship_id=relationship_id,
                parent_user_id=elder_user_id,
                child_user_id=child_user_id,
            )
        )
        session.add(
            MessageLog(
                message_id=f"message-{suffix}",
                run_id=f"run-{suffix}",
                user_id=child_user_id,
                relationship_id=relationship_id,
                input_type="audio",
                transcript="我下班准备关灯了",
                short_term_table=child_table,
                emotion={},
                situation={},
                communication={},
                raw={},
                created_at=datetime.utcnow(),
            )
        )
        session.commit()

    elder_table = _short_table(
        time="白天",
        scene="超市",
        event="买菜",
        objects=["购物篮"],
    )
    language = LanguageEmotionResult(
        transcript="我正在超市买菜",
        emotion={},
        situation={},
        communication={},
        short_term_table=elder_table,
        reply="",
        raw={"speaker_context": {"speaker_role": "parent"}},
    )
    agent = SemanticMappingAgent()

    try:
        persisted, persisted_sources = agent._load_persisted_role_short_term_tables(
            relationship_id
        )
        assert persisted["child"] == child_table
        assert persisted_sources["child"]["source"] == "latest_persisted_short_term_table"

        role_tables = {**persisted, "elder": elder_table}
        role_sources = {
            **persisted_sources,
            "elder": {"source": "current_reflected_short_term_table"},
        }
        environments = agent._build_role_environment_states(
            graph=load_semantic_graph(),
            role_short_tables=role_tables,
            role_short_table_sources=role_sources,
        )

        assert environments["elder"]["timeBucket"] == "daytime"
        assert environments["elder"]["sceneType"] == "supermarket"
        assert environments["elder"]["lighting"]["brightness"] == 0.9
        assert "sun" in environments["elder"]["lighting"]["celestialMarker"]
        assert environments["child"]["timeBucket"] == "evening"
        assert environments["child"]["sceneType"] == "workplace"
        assert environments["child"]["lighting"]["brightness"] == 0.3
        assert "moon" in environments["child"]["lighting"]["celestialMarker"]
        assert environments["child"]["lighting"]["lampState"] == "off"
        environment_prompt = " ".join(
            agent._role_environment_design_content(environments)
        )
        assert "中央安全区左侧偏下的父母区域独立呈现白天的超市" in environment_prompt
        assert "中央安全区右侧偏上的子女区域独立呈现晚上的单位" in environment_prompt
        assert "sun" in environment_prompt
        assert "moon" in environment_prompt
    finally:
        with SessionLocal() as session:
            session.query(MessageLog).filter(
                MessageLog.relationship_id == relationship_id
            ).delete(synchronize_session=False)
            session.query(RelationshipProfile).filter(
                RelationshipProfile.relationship_id == relationship_id
            ).delete(synchronize_session=False)
            session.commit()


def test_deterministic_l2_relationship_controls_are_monotonic() -> None:
    agent = SemanticMappingAgent()
    mapped = agent._build_deterministic_l2_parameters(
        graph=load_semantic_graph(),
        long_table=_long_table(),
    )

    assert mapped["contractVersion"] == "deterministic-l2-v1"
    assert mapped["relationshipScore"] > 0.8
    assert mapped["spatialScore"] >= 0.75
    assert mapped["spatialMode"] == "path_only"
    assert mapped["connectionPolicy"] == "short_path_only"
    assert mapped["controls"]["flowerDensity"] > 0.7
    assert mapped["controls"]["bloomRatio"] > 0.7
    assert mapped["controls"]["pathLength"] == 0.22
    assert mapped["controls"]["pathWidth"] == 0.05
    assert mapped["controls"]["sharedSpaceRatio"] > 0.2
    layout = mapped["layoutState"]
    assert layout["spatialMode"] == "path_only"
    assert layout["layoutIntent"] == "two_close_platforms_joined_by_short_path"
    assert layout["elderAnchor"] == [0.45, 0.625]
    assert layout["childAnchor"] == [0.55, 0.405]
    assert layout["personGapRatio"] == 0.1
    assert layout["elderPlatformAnchor"] == [0.43, 0.78]
    assert layout["childPlatformAnchor"] == [0.57, 0.588]
    assert layout["platformGapRatio"] == 0.06
    assert layout["platformOverlapRatio"] == 0.0
    assert layout["sharedGroundRatio"] == 0.65
    assert layout["centralFeature"] == "short_path"
    assert layout["sharedSceneRatio"] == 0.65
    assert layout["environmentMergeRatio"] == 0.675
    assert mapped["flowerColorPalette"] == [
        "soft_peach",
        "pale_coral",
        "warm_yellow",
    ]
    l2_prompt = agent._deterministic_l2_design_content(mapped)
    assert f"花木密度{mapped['controls']['flowerDensity']}" in l2_prompt
    assert f"两个生活空间间距{mapped['controls']['zoneGapRatio']}" in l2_prompt
    assert f"人物间距{layout['personGapRatio']}" in l2_prompt
    assert f"共享场景比例{layout['sharedSceneRatio']}" in l2_prompt
    assert "中间只保留一条短而窄的生活小路" in l2_prompt


def test_intimacy_distance_maps_to_five_spatial_modes() -> None:
    agent = SemanticMappingAgent()
    graph = load_semantic_graph()
    expected = {
        "共享空间变大": ("merged", "shared_ground", 0.47, 0.53, 0.46, 0.54),
        "近": ("path_only", "short_path", 0.45, 0.55, 0.43, 0.57),
        "未明确": ("river_and_path", "river_and_path", 0.43, 0.57, 0.39, 0.61),
        "稍远": ("river_only", "river", 0.41, 0.59, 0.35, 0.65),
        "远": ("peripheral", "river", 0.39, 0.61, 0.31, 0.69),
    }

    for value, (
        mode,
        central_feature,
        elder_x,
        child_x,
        elder_platform_x,
        child_platform_x,
    ) in expected.items():
        long_table = _long_table()
        long_table["E_relational_semantics"]["intimacy_distance"] = _cell(value)
        mapped = agent._build_deterministic_l2_parameters(
            graph=graph,
            long_table=long_table,
        )
        assert mapped["spatialMode"] == mode
        assert mapped["layoutState"]["centralFeature"] == central_feature
        assert mapped["layoutState"]["elderAnchor"][0] == elder_x
        assert mapped["layoutState"]["childAnchor"][0] == child_x
        assert mapped["layoutState"]["elderPlatformAnchor"][0] == elder_platform_x
        assert mapped["layoutState"]["childPlatformAnchor"][0] == child_platform_x
