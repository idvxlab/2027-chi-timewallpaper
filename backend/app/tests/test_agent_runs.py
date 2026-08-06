from fastapi.testclient import TestClient

from app.agents.language_emotion_agent import LanguageEmotionAgent
from app.core.config import settings
from app.db.models import MessageLog, RelationshipProfile, RelationshipState, WallpaperLog
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def _disable_live_text_providers(monkeypatch) -> None:
    for field in (
        "provider_api_key",
        "provider_api_base_url",
        "llm_api_key",
        "llm_api_base_url",
        "llm_chat_endpoint",
        "chatbot_llm_api_key",
        "chatbot_llm_api_base_url",
        "chatbot_llm_chat_endpoint",
    ):
        monkeypatch.setattr(settings, field, "")


def test_agent_run_audio_mvp_returns_separated_chatbot_and_semantic_outputs(monkeypatch) -> None:
    _disable_live_text_providers(monkeypatch)
    monkeypatch.setattr(settings, "doubao_asr_api_key", "")

    res = client.post(
        "/agent-runs/audio",
        files={"audio": ("recording.wav", b"fake-audio", "audio/wav")},
        data={"previousImageUrl": "/generated/day-1.png"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "done"
    assert body["runId"]

    result = body["result"]
    assert result["userId"] == "mother-demo"
    assert result["relationshipId"] == "family-demo"
    assert [step["name"] for step in result["steps"]] == [
        "chat_bot",
        "language_emotion",
        "memory_relation",
        "semantic_mapping",
        "image_generation",
    ]
    assert all(step["status"] == "done" for step in result["steps"])
    assert result["chatBot"]["transcript"]
    assert result["chatBot"]["reply"]
    assert result["languageEmotion"]["reply"] == result["chatBot"]["reply"]
    assert result["languageEmotion"]["transcript"]
    assert result["languageEmotion"]["shortTermTable"]["A_situational_semantics"]["event"]["value"]
    assert result["languageEmotion"]["shortTermTable"]["C_communicative_semantics"]["intent_type"]["value"]
    assert result["memoryRelation"]["longTermTable"]["D_longitudinal_state_semantics"]["social_connection_cue"]["value"]
    assert result["memoryRelation"]["longTermTable"]["E_relational_semantics"]["relationship_trend"]["value"]
    assert result["memoryRelation"]["memoryCard"]["backSummary"]

    instruction = result["semanticMapping"]["semanticVisualInstruction"]
    assert "不要出现文字" in instruction
    assert "连续完整" in instruction
    assert "动态反馈层" not in instruction
    assert "环境层" not in instruction
    scaffold = result["semanticMapping"]["cognitiveScaffold"]
    assert scaffold["agentRole"] == "Designer Agent / 读表式五层视觉设计智能体"
    assert scaffold["inputBoundary"] == "只读取父母与子女各自最新短期语义表、长期关系表和语义图谱；不回看原始文本，不重新做语言理解。"
    assert scaffold["shortTermTable"]["A_situational_semantics"]
    assert set(scaffold["roleShortTermTables"]) == {"elder", "child"}
    assert scaffold["roleShortTermTableSources"]["elder"]["source"] == "current_reflected_short_term_table"
    assert scaffold["roleEnvironmentStates"]["elder"]["available"] is True
    assert scaffold["longTermTable"]["E_relational_semantics"]
    assert scaffold["deterministicL2"]["contractVersion"] == "deterministic-l2-v1"
    assert "flowerDensity" in scaffold["deterministicL2"]["controls"]
    assert scaffold["visualDials"]["deterministicRelationship"] == scaffold["deterministicL2"]
    assert "structuredSemantics" not in scaffold
    assert scaffold["composition"]["leftBottomZone"]
    assert scaffold["composition"]["centerZone"]
    assert scaffold["composition"]["upperRightZone"]["scale"] == "dominant_space_area"
    assert scaffold["composition"]["layoutContract"]["upperRightZoneRatio"] == 0.50
    assert scaffold["deterministicL2"]["spatialMode"] == "path_only"
    assert scaffold["composition"]["layoutContract"]["middlePathZoneRatio"] == 0.08
    assert scaffold["composition"]["layoutContract"]["lowerLeftZoneRatio"] == 0.30
    assert scaffold["composition"]["layoutContract"]["aspectRatio"] == "1:1"
    assert scaffold["composition"]["layoutContract"]["logicalCanvas"] == "1536x1536"
    assert "continuous square canvas" in scaffold["composition"]["layoutContract"]["composition"]
    assert "protectedNarrativeStage" not in scaffold["composition"]["layoutContract"]
    assert "cameraSafeMargins" not in scaffold["composition"]["layoutContract"]
    assert "34%到40%" in scaffold["composition"]["characterFramingContract"]["scaleRule"]
    assert "不得超过42%" in scaffold["composition"]["characterFramingContract"]["scaleRule"]
    assert "平台" in scaffold["composition"]["characterFramingContract"]["bodyCompletenessRule"]
    assert scaffold["generationParameters"]["characterHeightRatioOfFullCanvas"] == "0.34_to_0.40_max_0.42"
    assert scaffold["generationParameters"]["characterHorizontalSafeZone"] == "silhouette_x_0.20_to_0.80_body_center_x_0.30_to_0.70"
    assert "直视镜头" in scaffold["composition"]["characterFramingContract"]["gazeRule"]
    assert scaffold["generationParameters"]["avoidDirectCameraGaze"] is True
    assert scaffold["mappingLayers"]["L1"]
    assert "contentLayers" not in scaffold
    assert scaffold["fiveLayerPlan"]["L1_environment_layer"]["designContent"]
    assert scaffold["fiveLayerPlan"]["L2_relational_structure_layer"]["designContent"]
    assert scaffold["fiveLayerPlan"]["L5_motion_feedback_layer"]["designContent"]
    assert "普通风景图" in scaffold["mustAvoid"]
    assert result["semanticMapping"]["openContentVisualizations"]
    assert result["semanticMapping"]["graphVersion"] == "chi-semantic-visual-graph-v4"
    assert "event" in result["semanticMapping"]["retrievalQuery"]
    assert "transcript:" not in result["semanticMapping"]["retrievalQuery"]
    assert result["semanticMapping"]["mappingTrace"]
    assert "score" in result["semanticMapping"]["mappingTrace"][0]

    image = result["imageGeneration"]
    assert image["generationMode"] == "single_prompt_image_mvp"
    assert image["changedRegions"] == ["relationship_layout_space"]
    assert image["assetMetadata"]["parent_image"] == "/generated/day-1.png"
    assert image["assetMetadata"]["masks"] == []

    lookup = client.get(f"/agent-runs/{body['runId']}")
    assert lookup.status_code == 200
    assert lookup.json()["runId"] == body["runId"]

    with SessionLocal() as session:
        message = session.query(MessageLog).filter(MessageLog.run_id == body["runId"]).one_or_none()
        assert message is not None
        assert message.relationship_id == "family-demo"
        assert message.short_term_table["A_situational_semantics"]["event"]["value"]
        relationship = (
            session.query(RelationshipProfile)
            .filter(RelationshipProfile.relationship_id == "family-demo")
            .one_or_none()
        )
        assert relationship is not None
        assert relationship.parent_user_id == "mother-demo"
        # The debug family can already have been updated through onboarding.
        # An agent run must preserve that persisted role instead of resetting it.
        assert relationship.parent_role in {"mother", "father"}
        assert relationship.child_user_id == "child-demo"
        assert relationship.child_role in {"daughter", "son"}
        state = (
            session.query(RelationshipState)
            .filter(RelationshipState.relationship_id == "family-demo")
            .one_or_none()
        )
        assert state is not None
        assert state.long_term_table["E_relational_semantics"]["relationship_trend"]["value"]
        wallpaper = session.query(WallpaperLog).filter(WallpaperLog.run_id == body["runId"]).one_or_none()
        assert wallpaper is not None
        assert wallpaper.relationship_id == "family-demo"
        assert wallpaper.five_layer_plan["L1_environment_layer"]["designContent"]


def test_agent_run_text_mvp_skips_asr_and_generates_prompt(monkeypatch) -> None:
    _disable_live_text_providers(monkeypatch)

    res = client.post(
        "/agent-runs/text",
        json={
            "transcript": "今天下班很晚，有点想家，桌上还有咖啡和文件。",
            "previousImageUrl": "/generated/day-1.png",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "done"
    result = body["result"]
    assert result["userId"] == "mother-demo"
    assert result["relationshipId"] == "family-demo"
    assert result["languageEmotion"]["transcript"] == "今天下班很晚，有点想家，桌上还有咖啡和文件。"
    assert result["languageEmotion"]["raw"]["asr"]["provider"] == "text_debug"
    assert result["semanticMapping"]["semanticVisualInstruction"]
    assert result["imageGeneration"]["assetMetadata"]["prompt"]


def test_language_agent_fallback_uses_short_table_enum_values() -> None:
    agent = LanguageEmotionAgent()

    result = agent._rule_understanding(
        "妈妈今天参加了社区合唱团，唱完以后很开心，说大家还约了下周再见。",
        asr_raw=None,
    )

    situation = result.situation
    assert situation["event_activity_type"] == "聚会"
    assert situation["scene_place_type"] == "社区"
    assert situation["actor_companion"] == ["父母"]
    assert result.short_term_table["A_situational_semantics"]["event"]["value"] == "聚会"
    assert result.short_term_table["A_situational_semantics"]["subject"]["value"] == ["父母"]
    assert result.short_term_table["B_affective_semantics"]["momentary_affect"]["value"] == "愉悦"

    perception_text = str(situation)
    assert "社区合唱团" not in result.short_term_table["A_situational_semantics"]["event"]["value"]
    assert "音符" not in perception_text
    assert "声波" not in perception_text


def test_script_reflection_corrects_late_overtime_return_home_semantics() -> None:
    agent = LanguageEmotionAgent()

    result = agent._rule_understanding(
        "我今天好累，加了好久的班，12点才回家",
        asr_raw=None,
    )

    table = result.short_term_table
    assert table["A_situational_semantics"]["event"]["value"] == "深夜加班后回家"
    assert table["A_situational_semantics"]["scene"]["value"] == "工作场所到回家路上"
    assert table["A_situational_semantics"]["time"]["value"] == "今天深夜，12点"
    assert table["A_situational_semantics"]["object"]["value"] == ["工作任务", "加班", "回家路途"]
    assert table["B_affective_semantics"]["momentary_affect"]["value"] == "疲惫"
    assert table["B_affective_semantics"]["affective_intensity"]["value"] == "明显"
    assert table["C_communicative_semantics"]["intent_type"]["value"] == "倾诉情绪"
    assert table["C_communicative_semantics"]["desired_response"]["value"] == "安慰或轻触回应"
    assert table["C_communicative_semantics"]["disclosure_depth"]["value"] == "情绪透露"
    assert result.raw["reflection"]["issues"]
    assert result.raw["reflection"]["revisions"]["A_situational_semantics"]["scene"]["value"] == "工作场所到回家路上"
