from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from typing import Any, Optional

import httpx

from app.providers.llm import get_llm_provider
from app.db.models import MessageLog, RelationshipProfile
from app.db.session import SessionLocal
from app.schemas.agent import LanguageEmotionResult, MemoryRelationResult, SemanticMappingResult
from app.services.semantic_graph import load_semantic_graph
from app.services.semantic_rag import load_semantic_rag


class SemanticMappingAgent:
    async def run(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        run_id: str | None = None,
        relationship_id: str | None = None,
        user_id: str | None = None,
    ) -> SemanticMappingResult:
        graph = load_semantic_graph()
        rag = load_semantic_rag()

        role_short_tables, role_short_table_sources = await self._load_role_short_term_tables(
            language=language,
            relationship_id=relationship_id,
            user_id=user_id,
        )
        entries = self._collect_structured_semantic_entries(
            language,
            memory,
            role_short_tables=role_short_tables,
        )
        retrieval_query = self._build_structured_retrieval_query(entries)
        graph_hits = self._resolve_graph_rules(graph, entries)
        retrieval_hits = rag.retrieve(retrieval_query, top_k=10)
        mapping_trace = self._merge_mapping_trace(graph_hits, retrieval_hits)

        open_content = self._build_open_content_visualizations(graph_hits)
        visual_plan = self._build_visual_content_plan(
            graph=graph,
            graph_hits=graph_hits,
            open_content=open_content,
            language=language,
            memory=memory,
            role_short_tables=role_short_tables,
            role_short_table_sources=role_short_table_sources,
        )

        instruction = await self._try_llm_compile_image_description(
            visual_plan=visual_plan,
            graph_hits=graph_hits,
            open_content=open_content,
            run_id=run_id,
        )
        if instruction is None:
            instruction = self._compile_image_description_fallback(visual_plan)
            composer = "rule_fallback"
        else:
            composer = "llm"

        visual_plan["imageDescription"] = instruction
        visual_plan["composer"] = composer
        self._log(
            run_id,
            "semantic visual instruction passed to Image Agent:\n"
            "----- semantic_visual_instruction START -----\n"
            f"{instruction}\n"
            "----- semantic_visual_instruction END -----",
        )

        return SemanticMappingResult(
            semantic_visual_instruction=instruction,
            graph_version=graph.version,
            retrieval_query=retrieval_query,
            cognitive_scaffold=visual_plan,
            open_content_visualizations=open_content,
            mapping_trace=mapping_trace,
        )

    async def _load_role_short_term_tables(
        self,
        *,
        language: LanguageEmotionResult,
        relationship_id: str | None,
        user_id: str | None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        role_tables: dict[str, dict[str, Any]] = {"elder": {}, "child": {}}
        sources: dict[str, dict[str, Any]] = {
            "elder": {"source": "unavailable"},
            "child": {"source": "unavailable"},
        }

        if relationship_id:
            persisted_tables, persisted_sources = await asyncio.to_thread(
                self._load_persisted_role_short_term_tables,
                relationship_id,
            )
            role_tables.update(persisted_tables)
            sources.update(persisted_sources)

        current_role = self._speaker_role(language)
        if current_role not in {"elder", "child"} and relationship_id and user_id:
            current_role = await asyncio.to_thread(
                self._role_for_relationship_user,
                relationship_id,
                user_id,
            )
        if current_role in {"elder", "child"}:
            role_tables[current_role] = deepcopy(language.short_term_table or {})
            sources[current_role] = {
                "source": "current_reflected_short_term_table",
                "userId": user_id or "",
            }

        return role_tables, sources

    def _load_persisted_role_short_term_tables(
        self,
        relationship_id: str,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        tables: dict[str, dict[str, Any]] = {"elder": {}, "child": {}}
        sources: dict[str, dict[str, Any]] = {
            "elder": {"source": "unavailable"},
            "child": {"source": "unavailable"},
        }
        with SessionLocal() as session:
            relationship = (
                session.query(RelationshipProfile)
                .filter(RelationshipProfile.relationship_id == relationship_id)
                .one_or_none()
            )
            if relationship is None:
                return tables, sources

            role_users = {
                "elder": relationship.parent_user_id,
                "child": relationship.child_user_id,
            }
            for role, role_user_id in role_users.items():
                if not role_user_id:
                    continue
                row = (
                    session.query(MessageLog)
                    .filter(
                        MessageLog.relationship_id == relationship_id,
                        MessageLog.user_id == role_user_id,
                    )
                    .order_by(MessageLog.created_at.desc(), MessageLog.id.desc())
                    .first()
                )
                if row is None or not row.short_term_table:
                    continue
                tables[role] = deepcopy(row.short_term_table)
                sources[role] = {
                    "source": "latest_persisted_short_term_table",
                    "messageId": row.message_id,
                    "userId": role_user_id,
                    "createdAt": row.created_at.isoformat() if row.created_at else "",
                }
        return tables, sources

    def _role_for_relationship_user(self, relationship_id: str, user_id: str) -> str:
        with SessionLocal() as session:
            relationship = (
                session.query(RelationshipProfile)
                .filter(RelationshipProfile.relationship_id == relationship_id)
                .one_or_none()
            )
            if relationship is None:
                return ""
            if user_id == relationship.parent_user_id:
                return "elder"
            if user_id == relationship.child_user_id:
                return "child"
        return ""

    def _collect_structured_semantic_entries(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        role_short_tables: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        long_table = memory.long_term_table or {}
        role_short_tables = role_short_tables or {
            self._speaker_role(language) or "elder": language.short_term_table or {},
        }
        specs: list[tuple[str, str, Any, str | None]] = []
        for role in ("elder", "child"):
            short_table = role_short_tables.get(role) or {}
            role_specs = [
                ("event", self._table_value(short_table, "A_situational_semantics", "event")),
                ("scene", self._table_value(short_table, "A_situational_semantics", "scene")),
                ("time", self._table_value(short_table, "A_situational_semantics", "time")),
                ("subject", self._table_value(short_table, "A_situational_semantics", "subject")),
                ("object", self._table_value(short_table, "A_situational_semantics", "object")),
                ("momentary_affect", self._table_value(short_table, "B_affective_semantics", "momentary_affect")),
                ("affective_intensity", self._table_value(short_table, "B_affective_semantics", "affective_intensity")),
                ("affective_ambiguity", self._table_value(short_table, "B_affective_semantics", "affective_ambiguity")),
                ("intent_type", self._table_value(short_table, "C_communicative_semantics", "intent_type")),
                ("desired_response", self._table_value(short_table, "C_communicative_semantics", "desired_response")),
                ("disclosure_depth", self._table_value(short_table, "C_communicative_semantics", "disclosure_depth")),
            ]
            specs.extend(
                (f"{role}_short_term_table", field, value, role)
                for field, value in role_specs
            )
        specs.extend([
            ("long_term_table", "social_connection_cue", self._table_value(long_table, "D_longitudinal_state_semantics", "social_connection_cue"), None),
            ("long_term_table", "fatigue_vitality_cue", self._table_value(long_table, "D_longitudinal_state_semantics", "fatigue_vitality_cue"), None),
            ("long_term_table", "stability_fluctuation", self._table_value(long_table, "D_longitudinal_state_semantics", "stability_fluctuation"), None),
            ("long_term_table", "recovery_decline_trend", self._table_value(long_table, "D_longitudinal_state_semantics", "recovery_decline_trend"), None),
            ("long_term_table", "interaction_frequency", self._table_value(long_table, "E_relational_semantics", "interaction_frequency"), None),
            ("long_term_table", "intimacy_distance", self._table_value(long_table, "E_relational_semantics", "intimacy_distance"), None),
            ("long_term_table", "reciprocity", self._table_value(long_table, "E_relational_semantics", "reciprocity"), None),
            ("long_term_table", "emotional_warmth", self._table_value(long_table, "E_relational_semantics", "emotional_warmth"), None),
            ("long_term_table", "relationship_trend", self._table_value(long_table, "E_relational_semantics", "relationship_trend"), None),
        ])

        entries: list[dict[str, Any]] = []
        for source, field, value, role_scope in specs:
            if value in (None, "", []):
                continue
            entries.append(
                {
                    "source": source,
                    "field": field,
                    "value": value,
                    "valueText": self._value_text(value),
                    "roleScope": role_scope,
                }
            )
        return entries

    def _resolve_graph_rules(self, graph, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hits = []
        for entry in entries:
            resolved = graph.resolve(entry["field"], entry["value"])
            if not resolved:
                continue
            hits.append(
                {
                    **resolved,
                    "source": entry["source"],
                    "role_scope": entry.get("roleScope"),
                    "value_raw": entry["value"],
                    "match_type": "field_rule",
                    "score": 1.0,
                }
            )
        return hits

    def _build_open_content_visualizations(self, graph_hits: list[dict[str, Any]]) -> list[dict[str, str]]:
        visualizations: list[dict[str, str]] = []
        for hit in graph_hits:
            if hit.get("content_type") != "open_concrete_content":
                continue
            for value in self._as_list(hit.get("value_raw", hit.get("value"))):
                if not value:
                    continue
                field = hit.get("field", "")
                target_layer = self._target_content(field)
                visualizations.append(
                    {
                        "source": str(value),
                        "field": field,
                        "roleScope": str(hit.get("role_scope") or ""),
                        "targetLayer": target_layer,
                        "visualization": self._open_content_sentence(field, str(value)),
                    }
                )
        return visualizations

    def _build_visual_content_plan(
        self,
        graph,
        graph_hits: list[dict[str, Any]],
        open_content: list[dict[str, str]],
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        role_short_tables: dict[str, dict[str, Any]],
        role_short_table_sources: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        viewer_role = self._viewer_role(language)
        left_actor, right_actor = self._actor_roles(viewer_role)
        role_environment_states = self._build_role_environment_states(
            graph=graph,
            role_short_tables=role_short_tables,
            role_short_table_sources=role_short_table_sources,
        )
        deterministic_l2 = self._build_deterministic_l2_parameters(
            graph=graph,
            long_table=memory.long_term_table or {},
        )
        spatial_mode = str(deterministic_l2.get("spatialMode") or "river_and_path")
        merged_relationship_space = spatial_mode == "merged"
        spatial_requirements = {
            "merged": "最近：两块生活平台整体融合成同一片共享地面，中央无河无路",
            "path_only": "比较近：两块生活平台明显靠近，中央只有一条短而窄的小路，不得有河流或溪流",
            "river_and_path": "不近不远：两块生活平台保持中等距离，中央同时有河流和小路",
            "river_only": "比较远：两块生活平台进一步拉开，中央只有河水，不得有小路或桥",
            "peripheral": "最远：两块生活平台保持最宽间隔，中央保留宽阔河流且不建立小路或桥；两个人物仍完整位于中央显示安全区内",
        }
        spatial_requirement = spatial_requirements.get(
            spatial_mode, spatial_requirements["river_and_path"]
        )
        central_zone_ratios = {
            "merged": 0.0,
            "path_only": 0.08,
            "river_and_path": 0.2,
            "river_only": 0.24,
            "peripheral": 0.36,
        }
        left_description = self._role_zone_description(
            left_actor,
            role_environment_states["elder"],
            zone="left_bottom",
        )
        right_description = self._role_zone_description(
            right_actor,
            role_environment_states["child"],
            zone="upper_right",
        )
        intensity = self._numeric_value(graph_hits, "affective_intensity", default=0.45)
        ambiguity_clarity = self._numeric_value(graph_hits, "affective_ambiguity", default=0.65)
        disclosure_depth = self._numeric_value(graph_hits, "disclosure_depth", default=0.3)

        mapping_layers = {
            "L1": self._content_for_layers(graph_hits, open_content, ["L1"]),
            "L2": self._content_for_layers(graph_hits, open_content, ["L2"]),
            "L3": self._content_for_layers(graph_hits, open_content, ["L3"]),
            "L4": self._content_for_layers(graph_hits, open_content, ["L4"]),
            "L5": self._content_for_layers(graph_hits, open_content, ["L5"]),
        }
        five_layer_plan = self._build_five_layer_plan(mapping_layers, language, memory)
        five_layer_plan["L1_environment_layer"]["roleStates"] = role_environment_states
        five_layer_plan["L1_environment_layer"]["designContent"] = self._unique(
            [
                *self._role_environment_design_content(role_environment_states),
                str(
                    (graph.role_scoped_l1_mapping.get("transition_contract") or {}).get(
                        "rule",
                        "",
                    )
                ),
                *five_layer_plan["L1_environment_layer"]["designContent"],
            ]
        )
        five_layer_plan["L2_relational_structure_layer"]["deterministicControls"] = deterministic_l2
        five_layer_plan["L2_relational_structure_layer"]["layoutState"] = deterministic_l2[
            "layoutState"
        ]
        five_layer_plan["L2_relational_structure_layer"]["designContent"] = self._unique(
            [
                self._deterministic_l2_design_content(deterministic_l2),
                *five_layer_plan["L2_relational_structure_layer"]["designContent"],
            ]
        )
        composition_contract = graph.composition_contract or {}
        left_zone_contract = composition_contract.get("left_bottom_zone", {})
        center_zone_contract = composition_contract.get("center_zone", {})
        upper_zone_contract = composition_contract.get("upper_right_zone", {})
        character_framing_contract = composition_contract.get("character_framing", {})

        must_include = self._unique(
            [
                "一张连续完整的1:1方形1536×1536关系壁纸",
                "整个方形画布是同一个连续纸雕世界，不划分中央面板和左右边条",
                "子女位于中央显示安全区的右侧偏上，父母位于其左侧偏下",
                spatial_requirement,
                "父母位于中央显示安全区左侧偏下",
                "人物和重要物件与画布外边缘保留自然留白",
                "两端人物采用完整头身构图；平台边缘不得从人物腰部或躯干处截断身体",
                "两端人物头到脚高度均保持为完整方形画布的34%到40%，任一人物不得超过42%",
                "两端人物完整轮廓位于x=0.20到0.80，身体中心位于x=0.30到0.70",
                "双方人物脸部和表情清晰可见",
                "双方人物不要正脸直视镜头",
                "双方各自在自己的生活场景里做自己的事情",
                "人物和生活平台的位置、间距与中央元素严格服从五档关系空间状态",
                *self._flatten(hit.get("must_include", []) for hit in graph_hits),
            ]
        )
        live_call_allowed = self._live_call_allowed(language)
        must_avoid = self._unique(
            [
                *graph.composition_contract.get("global_avoid", []),
                *self._flatten(hit.get("avoid", []) for hit in graph_hits),
                *({
                    "merged": ["两人之间的河流", "两人之间的小路", "两块分离的平台", "中央沟壑或桥"],
                    "path_only": ["中央河流或溪流", "中央桥梁", "大尺度中央沟壑"],
                    "river_and_path": ["缺失河流", "缺失小路"],
                    "river_only": ["中央连接小路", "中央桥梁"],
                    "peripheral": ["人物贴近或越过画布外缘", "共享平台", "中央缺失河流", "直接连接小路或桥"],
                }.get(spatial_mode, [])),
                *(
                    []
                    if live_call_allowed
                    else ["手机通话画面", "视频通话画面", "举着手机对话", "挥手打招呼", "双方正在实时聊天"]
                ),
            ]
        )

        relationship_metaphors = self._metaphor_descriptions(graph_hits, fields=("intent_type",))
        interaction_metaphors = self._metaphor_descriptions(graph_hits, fields=("desired_response",))
        relation_state_metaphors = self._metaphor_descriptions(
            graph_hits,
            fields=("interaction_frequency", "intimacy_distance", "reciprocity", "emotional_warmth", "relationship_trend"),
        )
        affective_metaphors = self._metaphor_descriptions(
            graph_hits,
            fields=("momentary_affect", "affective_intensity", "affective_ambiguity"),
        )
        visual_dials = self._build_visual_dials(
            language=language,
            memory=memory,
            intensity=intensity,
            ambiguity_clarity=ambiguity_clarity,
            disclosure_depth=disclosure_depth,
        )
        visual_dials["deterministicRelationship"] = deterministic_l2

        return {
            "agentRole": "Designer Agent / 读表式五层视觉设计智能体",
            "inputBoundary": "只读取父母与子女各自最新短期语义表、长期关系表和语义图谱；不回看原始文本，不重新做语言理解。",
            "viewerRole": viewer_role,
            "shortTermTable": language.short_term_table,
            "roleShortTermTables": role_short_tables,
            "roleShortTermTableSources": role_short_table_sources,
            "longTermTable": memory.long_term_table,
            "roleEnvironmentStates": role_environment_states,
            "deterministicL2": deterministic_l2,
            "composition": {
                "format": "square_1_1_continuous_wallpaper",
                "layoutContract": {
                    "aspectRatio": "1:1",
                    "logicalCanvas": "1536x1536",
                    "composition": "asymmetric diagonal three-zone layout across one continuous square canvas",
                    "upperRightZoneRatio": upper_zone_contract.get("target_area_ratio", 0.50),
                    "middlePathZoneRatio": central_zone_ratios.get(spatial_mode, 0.2),
                    "lowerLeftZoneRatio": left_zone_contract.get("target_area_ratio", 0.30),
                    "strictRule": (
                        "整个1536×1536画布必须是连续单一纸雕世界；"
                        + spatial_requirement
                        + "。不得生成中央面板、左右边条、垂直接缝或色差边界。"
                        "两端人物头到脚高度均为全画布34%到40%且不得超过42%；完整轮廓位于x=0.20到0.80。"
                        "人物按完整头身构图；平台边缘不得从腰部或躯干截断身体，道具可以自然遮挡腰部以下。"
                    ),
                },
                "characterFramingContract": {
                    "scaleRule": character_framing_contract.get(
                        "scale_rule",
                        "两端人物头到脚高度均为完整方形画布的34%到40%，任一人物不得超过42%；关系档位不得改变人物尺度。",
                    ),
                    "bodyCompletenessRule": character_framing_contract.get(
                        "body_completeness_rule",
                        "双方采用完整头身构图；平台不得从腰部或躯干截断；有意义的活动道具可以自然遮挡腰部以下。",
                    ),
                    "expressionRule": character_framing_contract.get(
                        "expression_rule",
                        "双方脸部和情绪都要可读，但不要做正面证件照或自拍式凝视。",
                    ),
                    "gazeRule": character_framing_contract.get(
                        "gaze_rule",
                        "人物不要正脸直视镜头；视线应自然看向画面内事件、物件、路径、窗外、天空、远处、手中的东西或画面外侧。",
                    ),
                    "cameraFeeling": character_framing_contract.get(
                        "camera_feeling",
                        "像安静观察到的生活瞬间，不是摆拍肖像。",
                    ),
                },
                "leftBottomZone": {
                    "actor": left_actor,
                    "function": "父母人物固定生活区域；父母说话时为事件发生处",
                    "description": left_description,
                    "bbox": left_zone_contract.get("bbox"),
                    "targetAreaRatio": left_zone_contract.get("target_area_ratio"),
                    "characterScaleRule": left_zone_contract.get("character_scale_rule"),
                    "expressionRequirement": left_zone_contract.get("expression_requirement"),
                },
                "centerZone": {
                    "function": "关系映射",
                    "description": self._center_relation_description(
                        language,
                        memory,
                        relationship_metaphors,
                        relation_state_metaphors,
                        deterministic_l2,
                    ),
                    "bbox": center_zone_contract.get("bbox"),
                    "targetAreaRatio": center_zone_contract.get("target_area_ratio"),
                },
                "upperRightZone": {
                    "actor": right_actor,
                    "function": "子女人物固定生活区域；子女说话时为事件发生处",
                    "description": right_description,
                    "bbox": upper_zone_contract.get("bbox"),
                    "scale": upper_zone_contract.get("scale", "dominant_figure_area"),
                    "targetAreaRatio": upper_zone_contract.get("target_area_ratio"),
                    "relativeAreaRule": upper_zone_contract.get("relative_area_rule"),
                    "expressionRequirement": upper_zone_contract.get("expression_requirement"),
                },
            },
            "mappingLayers": mapping_layers,
            "fiveLayerPlan": five_layer_plan,
            "openContentVisualizations": open_content,
            "mappedMetaphors": {
                "communication": relationship_metaphors,
                "interactionFeedback": interaction_metaphors,
                "relationshipState": relation_state_metaphors,
                "affective": affective_metaphors,
            },
            "visualDials": visual_dials,
            "generationParameters": {
                "emotionVisualStrength": round(intensity, 2),
                "expressionClarity": round(ambiguity_clarity, 2),
                "privacyAndCareSignal": round(disclosure_depth, 2),
                "relationPathDirection": "left_bottom_to_upper_right",
                "relationPathRequired": True,
                "upperRightScale": "dominant_space_not_dominant_face",
                "upperRightTargetAreaRatio": 0.50,
                "middlePathTargetAreaRatio": 0.20,
                "leftBottomTargetAreaRatio": 0.30,
                "characterHeightRatioOfFullCanvas": "0.34_to_0.40_max_0.42",
                "characterHorizontalSafeZone": "silhouette_x_0.20_to_0.80_body_center_x_0.30_to_0.70",
                "doNotEnlargeUpperRightCharacter": True,
                "bothFacesAndExpressionsVisible": True,
                "avoidDirectCameraGaze": True,
                "communicationMode": "live_call_allowed" if live_call_allowed else "asynchronous_parallel_life",
                "avoidLiveCallUnlessExplicit": not live_call_allowed,
                "layoutStrictness": "high",
            },
            "priority": {
                "primary": ["中央安全区左侧偏下的父母人物", "中央安全区右侧偏上的子女人物", "中间关系路径", "当前事件锚点", "双方各自生活状态"],
                "secondary": ["生活场域", "时间光线", "情绪氛围", "低压力反馈暗示"],
                "background": ["天气", "远景", "季节质感"],
                "suppress": must_avoid,
            },
            "mustInclude": must_include,
            "mustAvoid": must_avoid,
            "visualProposition": self._final_visual_proposition(language, memory),
        }

    def _build_role_environment_states(
        self,
        *,
        graph,
        role_short_tables: dict[str, dict[str, Any]],
        role_short_table_sources: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        contract = graph.role_scoped_l1_mapping or {}
        role_zones = contract.get("role_zones", {})
        time_profiles = contract.get("time_profiles", [])
        scene_profiles = contract.get("scene_profiles", [])
        lamp_rules = contract.get("lamp_state_rules", {})
        result: dict[str, dict[str, Any]] = {}

        for role in ("elder", "child"):
            table = role_short_tables.get(role) or {}
            time_value = self._value_text(
                self._table_value(table, "A_situational_semantics", "time")
            )
            scene_value = self._value_text(
                self._table_value(table, "A_situational_semantics", "scene")
            )
            event_value = self._value_text(
                self._table_value(table, "A_situational_semantics", "event")
            )
            object_value = self._value_text(
                self._table_value(table, "A_situational_semantics", "object")
            )
            affect_value = self._value_text(
                self._table_value(table, "B_affective_semantics", "momentary_affect")
            )
            time_profile = self._match_semantic_profile(time_value, time_profiles)
            scene_profile = self._match_semantic_profile(scene_value, scene_profiles)
            event_context = " ".join((event_value, object_value))
            lamp_state = str(time_profile.get("default_lamp_state") or "contextual")
            if self._contains_any(event_context, lamp_rules.get("force_off_when", [])):
                lamp_state = "off"
            elif self._contains_any(event_context, lamp_rules.get("force_on_when", [])):
                lamp_state = "on"

            result[role] = {
                "available": bool(table),
                "role": role,
                "zone": role_zones.get(role, {}),
                "source": role_short_table_sources.get(role, {"source": "unavailable"}),
                "timeRaw": time_value,
                "timeBucket": time_profile.get("id", "unknown"),
                "timeConfidence": self._table_confidence(
                    table,
                    "A_situational_semantics",
                    "time",
                ),
                "sceneRaw": scene_value,
                "sceneType": scene_profile.get("id", "open_scene"),
                "sceneConfidence": self._table_confidence(
                    table,
                    "A_situational_semantics",
                    "scene",
                ),
                "event": event_value,
                "objects": self._as_list(
                    self._table_value(table, "A_situational_semantics", "object")
                ),
                "affect": affect_value,
                "lighting": {
                    "brightness": time_profile.get("brightness"),
                    "colorTemperature": time_profile.get("color_temperature", "neutral"),
                    "skyState": time_profile.get("sky_state", "preserve_previous"),
                    "celestialMarker": time_profile.get("celestial_marker", "preserve_previous"),
                    "lampState": lamp_state,
                    "updatePolicy": time_profile.get("update_policy", "apply"),
                },
                "sceneAnchors": scene_profile.get("anchors", [])[:3],
                "sceneAvoid": scene_profile.get("avoid", []),
            }
        return result

    def _build_deterministic_l2_parameters(
        self,
        *,
        graph,
        long_table: dict[str, Any],
    ) -> dict[str, Any]:
        contract = graph.deterministic_l2_parameters or {}
        components = contract.get("components", {})
        default_confidence = float(contract.get("default_confidence", 0.65))
        component_scores: dict[str, dict[str, Any]] = {}
        weighted_score = 0.0
        total_weight = 0.0

        for component, spec in components.items():
            field = str(spec.get("field") or component)
            value = self._value_text(
                self._table_value(long_table, "E_relational_semantics", field)
            )
            raw_score = self._categorical_score(
                value,
                spec.get("value_scores", {}),
                fallback=float(spec.get("fallback_score", 0.5)),
            )
            confidence = self._table_confidence(
                long_table,
                "E_relational_semantics",
                field,
                default=default_confidence,
            )
            effective_score = 0.5 + ((raw_score - 0.5) * confidence)
            weight = float(spec.get("weight", 0.0))
            weighted_score += effective_score * weight
            total_weight += weight
            component_scores[component] = {
                "field": field,
                "value": value,
                "rawScore": round(raw_score, 3),
                "confidence": round(confidence, 3),
                "effectiveScore": round(effective_score, 3),
                "weight": weight,
                "visualResponsibility": spec.get("visual_responsibility", []),
            }

        relationship_score = weighted_score / total_weight if total_weight else 0.5
        relationship_score = self._clamp(relationship_score, 0.0, 1.0)
        controls: dict[str, float] = {}
        for name, spec in (contract.get("controls") or {}).items():
            low = float(spec.get("min", 0.0))
            high = float(spec.get("max", 1.0))
            driver_name = str(spec.get("driver") or "relationship_score")
            if driver_name == "relationship_score":
                driver = relationship_score
            else:
                driver = float(
                    component_scores.get(driver_name, {}).get(
                        "effectiveScore",
                        relationship_score,
                    )
                )
            if spec.get("direction") == "decreasing":
                driver = 1.0 - driver
            controls[name] = round(low + ((high - low) * driver), 3)

        spatial_modes = contract.get("spatial_modes") or {}
        spatial_driver = str(spatial_modes.get("driver") or "relationship_score")
        spatial_driver_score = str(spatial_modes.get("driver_score") or "effectiveScore")
        spatial_score = (
            relationship_score
            if spatial_driver == "relationship_score"
            else float(
                component_scores.get(spatial_driver, {}).get(
                    spatial_driver_score,
                    relationship_score,
                )
            )
        )
        merged_min = float(spatial_modes.get("merged_min_score", 0.85))
        path_only_min = float(spatial_modes.get("path_only_min_score", 0.65))
        river_and_path_min = float(
            spatial_modes.get("river_and_path_min_score", 0.4)
        )
        river_only_min = float(spatial_modes.get("river_only_min_score", 0.2))
        if spatial_score >= merged_min:
            spatial_mode = "merged"
            connection_policy = "shared_ground_only"
        elif spatial_score >= path_only_min:
            spatial_mode = "path_only"
            connection_policy = "short_path_only"
        elif spatial_score >= river_and_path_min:
            spatial_mode = "river_and_path"
            connection_policy = "river_and_path"
        elif spatial_score >= river_only_min:
            spatial_mode = "river_only"
            connection_policy = "river_only"
        else:
            spatial_mode = "peripheral"
            connection_policy = "distant_river_only"
        for name, value in (
            (spatial_modes.get("mode_overrides") or {}).get(spatial_mode, {})
        ).items():
            controls[str(name)] = float(value)

        layout_state = self._build_l2_layout_state(
            spatial_modes=spatial_modes,
            spatial_score=spatial_score,
            spatial_mode=spatial_mode,
        )

        warmth_score = float(
            component_scores.get("emotional_warmth", {}).get(
                "effectiveScore",
                relationship_score,
            )
        )
        palettes = contract.get("flower_color_palettes", {})
        if warmth_score < 0.4:
            flower_palette = palettes.get("cool", [])
        elif warmth_score < 0.7:
            flower_palette = palettes.get("stable", [])
        else:
            flower_palette = palettes.get("warm", [])

        return {
            "contractVersion": contract.get("version", "deterministic-l2-v1"),
            "relationshipScore": round(relationship_score, 3),
            "spatialScore": round(spatial_score, 3),
            "spatialMode": spatial_mode,
            "connectionPolicy": connection_policy,
            "spatialRule": (
                (spatial_modes.get("mode_rules") or {}).get(spatial_mode, "")
            ),
            "layoutState": layout_state,
            "componentScores": component_scores,
            "controls": controls,
            "flowerColorPalette": flower_palette,
            "smoothing": contract.get("smoothing", {}),
            "semanticSeparation": contract.get("semantic_separation", {}),
        }

    def _build_l2_layout_state(
        self,
        *,
        spatial_modes: dict[str, Any],
        spatial_score: float,
        spatial_mode: str,
    ) -> dict[str, Any]:
        interpolation = spatial_modes.get("layout_interpolation") or {}
        progress = self._clamp(float(spatial_score), 0.0, 1.0)

        def scalar(name: str, far: float, near: float) -> float:
            spec = interpolation.get(name) or {}
            start = float(spec.get("far", far))
            end = float(spec.get("near", near))
            return round(start + ((end - start) * progress), 3)

        def anchor(name: str, far: list[float], near: list[float]) -> list[float]:
            spec = interpolation.get(name) or {}
            start = spec.get("far", far)
            end = spec.get("near", near)
            if not isinstance(start, list) or len(start) != 2:
                start = far
            if not isinstance(end, list) or len(end) != 2:
                end = near
            return [
                round(float(start[index]) + ((float(end[index]) - float(start[index])) * progress), 3)
                for index in range(2)
            ]

        layout_intents = {
            "peripheral": "two_widely_separated_platforms_with_people_in_display_safe_zone",
            "river_only": "two_distant_platforms_separated_by_river",
            "river_and_path": "two_platforms_with_river_and_path",
            "path_only": "two_close_platforms_joined_by_short_path",
            "merged": "one_shared_living_space",
        }
        mode_profile = (
            (spatial_modes.get("layout_mode_profiles") or {}).get(spatial_mode, {})
        )

        def mode_scalar(name: str, fallback: float) -> float:
            try:
                return round(float(mode_profile.get(name, fallback)), 3)
            except (TypeError, ValueError):
                return round(fallback, 3)

        def mode_anchor_x(name: str, fallback: list[float]) -> list[float]:
            try:
                return [round(float(mode_profile.get(name, fallback[0])), 3), fallback[1]]
            except (TypeError, ValueError):
                return fallback

        interpolated_person_gap = scalar("person_gap_ratio", 0.7, 0.06)
        interpolated_platform_gap = scalar("platform_gap_ratio", 0.58, 0.0)
        interpolated_platform_overlap = scalar("platform_overlap_ratio", 0.0, 0.22)
        interpolated_shared_ground = scalar("shared_ground_ratio", 0.0, 0.95)
        return {
            "coordinateSystem": "normalized_canvas_xy",
            "layoutProgress": round(progress, 3),
            "spatialMode": spatial_mode,
            "layoutIntent": layout_intents.get(spatial_mode, layout_intents["river_and_path"]),
            "elderAnchor": mode_anchor_x(
                "elderAnchorX",
                anchor("elder_anchor", [0.20, 0.76], [0.48, 0.58]),
            ),
            "childAnchor": mode_anchor_x(
                "childAnchorX",
                anchor("child_anchor", [0.80, 0.24], [0.52, 0.46]),
            ),
            "elderPlatformAnchor": mode_anchor_x(
                "elderPlatformAnchorX",
                anchor("elder_platform_anchor", [0.08, 0.90], [0.45, 0.74]),
            ),
            "childPlatformAnchor": mode_anchor_x(
                "childPlatformAnchorX",
                anchor("child_platform_anchor", [0.92, 0.43], [0.55, 0.64]),
            ),
            "personGapRatio": mode_scalar("personGapRatio", interpolated_person_gap),
            "platformGapRatio": mode_scalar("platformGapRatio", interpolated_platform_gap),
            "platformOverlapRatio": mode_scalar(
                "platformOverlapRatio", interpolated_platform_overlap
            ),
            "sharedGroundRatio": mode_scalar("sharedGroundRatio", interpolated_shared_ground),
            "sharedSceneRatio": scalar("shared_scene_ratio", 0.05, 0.85),
            "environmentMergeRatio": scalar("environment_merge_ratio", 0.0, 0.9),
            "centralFeature": {
                "merged": "shared_ground",
                "path_only": "short_path",
                "river_and_path": "river_and_path",
                "river_only": "river",
                "peripheral": "river",
            }.get(spatial_mode, "river_and_path"),
            "roleOrderInvariant": "elder remains lower-left of child while both move inward",
        }

    def _role_environment_design_content(
        self,
        role_states: dict[str, dict[str, Any]],
    ) -> list[str]:
        content: list[str] = []
        labels = {"elder": "中央安全区左侧偏下的父母区域", "child": "中央安全区右侧偏上的子女区域"}
        for role in ("elder", "child"):
            state = role_states.get(role) or {}
            label = labels[role]
            if not state.get("available"):
                content.append(f"{label}没有新短期表，保持上一张壁纸中的场景和时间光照不变")
                continue
            lighting = state.get("lighting") or {}
            anchors = "、".join(str(item) for item in state.get("sceneAnchors", []) if item)
            brightness = lighting.get("brightness")
            celestial_marker = str(lighting.get("celestialMarker") or "preserve_previous")
            brightness_text = (
                f"局部亮度{brightness}"
                if isinstance(brightness, (int, float))
                else "保留上一张精确时段的局部亮度"
            )
            content.append(
                f"{label}独立呈现{state.get('timeRaw') or '既有时间'}的{state.get('sceneRaw') or '既有场景'}，"
                f"{brightness_text}，色温{lighting.get('colorTemperature') or 'neutral'}，"
                f"灯光{lighting.get('lampState') or 'contextual'}，"
                f"局部天空时间标志{celestial_marker}"
                + (f"，场景锚点只保留{anchors}" if anchors else "")
            )
        return content

    def _deterministic_l2_design_content(self, deterministic_l2: dict[str, Any]) -> str:
        controls = deterministic_l2.get("controls") or {}
        layout = deterministic_l2.get("layoutState") or {}
        palette = "、".join(
            str(item) for item in deterministic_l2.get("flowerColorPalette", []) if item
        )
        spatial_mode = deterministic_l2.get("spatialMode", "connected")
        spatial_rule = deterministic_l2.get("spatialRule", "")
        return (
            f"关系结构使用确定性参数：关系分数{deterministic_l2.get('relationshipScore', 0.5)}，"
            f"空间模式{spatial_mode}，"
            f"花木密度{controls.get('flowerDensity', 0.5)}，开放花朵比例{controls.get('bloomRatio', 0.5)}，"
            f"两个生活空间间距{controls.get('zoneGapRatio', 0.25)}，共享空间比例{controls.get('sharedSpaceRatio', 0.15)}，"
            f"路径长度{controls.get('pathLength', 0.55)}、宽度{controls.get('pathWidth', 0.07)}、亮度{controls.get('connectionBrightness', 0.55)}"
            f"，父母锚点{layout.get('elderAnchor', [0.24, 0.74])}，子女锚点{layout.get('childAnchor', [0.69, 0.28])}"
            f"，人物间距{layout.get('personGapRatio', 0.55)}，共享场景比例{layout.get('sharedSceneRatio', 0.05)}"
            f"，环境融合比例{layout.get('environmentMergeRatio', 0.0)}，父母平台锚点{layout.get('elderPlatformAnchor', [0.26, 0.72])}"
            f"，子女平台锚点{layout.get('childPlatformAnchor', [0.72, 0.3])}，平台间距{layout.get('platformGapRatio', 0.34)}"
            f"，平台交叠比例{layout.get('platformOverlapRatio', 0.0)}，共享地面比例{layout.get('sharedGroundRatio', 0.05)}"
            f"，中央元素{layout.get('centralFeature', 'river_and_path')}"
            + (f"，花朵颜色限定为{palette}" if palette else "")
            + (f"。{spatial_rule}" if spatial_rule else "")
        )

    def _match_semantic_profile(
        self,
        value: str,
        profiles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = value.strip().lower()
        for profile in profiles:
            matches = [str(item).strip().lower() for item in profile.get("matches", [])]
            if normalized and any(item == normalized or item in normalized for item in matches):
                return profile
        return {}

    def _contains_any(self, text: str, candidates: Any) -> bool:
        return any(str(candidate) in text for candidate in candidates or [])

    def _categorical_score(
        self,
        value: str,
        value_scores: dict[str, Any],
        *,
        fallback: float,
    ) -> float:
        if value in value_scores:
            return self._clamp(float(value_scores[value]), 0.0, 1.0)
        matches = [
            (key, score)
            for key, score in value_scores.items()
            if str(key) in value or (value and value in str(key))
        ]
        if not matches:
            return self._clamp(fallback, 0.0, 1.0)
        key, score = max(matches, key=lambda item: len(str(item[0])))
        del key
        return self._clamp(float(score), 0.0, 1.0)

    async def _try_llm_compile_image_description(
        self,
        visual_plan: dict[str, Any],
        graph_hits: list[dict[str, Any]],
        open_content: list[dict[str, str]],
        run_id: str | None = None,
    ) -> Optional[str]:
        provider = get_llm_provider()
        if provider is None:
            self._log(run_id, "semantic compiler skipped LLM: provider not configured")
            return None

        trace = [
            {
                "field": hit.get("field"),
                "value": hit.get("value"),
                "content_type": hit.get("content_type"),
                "strategy": hit.get("strategy"),
                "mapping_layers": hit.get("mapping_layers"),
                "visual_metaphors": hit.get("visual_metaphors"),
                "parameters": hit.get("parameters"),
                "must_include": hit.get("must_include"),
                "avoid": hit.get("avoid"),
            }
            for hit in graph_hits
        ]
        spatial_mode = str(
            (visual_plan.get("deterministicL2") or {}).get("spatialMode")
            or "river_and_path"
        )
        relationship_layout_rule = {
            "merged": "最近：两块生活平台融合成同一片共享地面，中央无河无路。",
            "path_only": "比较近：两块平台明显靠近，中央只有一条短小路，禁止河流、溪流和桥。",
            "river_and_path": "不近不远：两块平台保持中等距离，中央同时保留河流和小路。",
            "river_only": "比较远：两块平台拉开，中央只有河水，禁止小路和桥。",
            "peripheral": "最远：两块平台保持最宽间隔，中央保留宽阔河流，禁止直接小路或桥；两个人物仍完整位于中央显示安全区内。",
        }.get(spatial_mode, "中央同时保留河流和小路。")
        prompt = f"""
你是 TimeWallpaper 的 Designer Agent，也就是“读表式五层视觉设计智能体”。
你不重新理解原始文本，只能读取短期语义表、长期关系表、语义图谱规则和五层视觉设计计划。

任务：
	1. 输出一张连续完整的1:1方形1536×1536壁纸。整个画布是同一个纸雕世界，不划分中央面板和左右边条，不出现垂直接缝、色差边界或拼贴分区。{relationship_layout_rule}
2. 短期表中的事件、场景、时间、人物、物件必须保留为具体内容，不要替换成抽象类别。
3. 长期表中的联系频率、亲密距离、情感温度、关系趋势需要转成关系结构和空间组织。
4. 必须先服从 visualDials：路径宽窄、连续性、亮度、留白、共享空间、花苞/果实、光点密度、色温和人物动势都以 visualDials 为准，再翻译成自然画面描述。
5. 默认双方是异步陪伴：各自在自己的生活场景里做自己的事情，只通过中间关系映射相连；除非视觉内容计划明确允许 live_call，否则不要出现手机通话、视频通话、举手机对话或挥手打招呼。
6. 两端人物头到脚高度都保持为完整方形画布的34%到40%，任一人物不得超过42%；关系档位只改变间距，不改变人物尺度或镜头缩放。两人的完整轮廓必须位于x=0.20到0.80，身体中心必须位于x=0.30到0.70。两端人物采用完整头身构图，平台边缘不得在腰部或躯干处截断人物；桌子、购物篮、座椅等有意义的活动道具可以自然遮挡腰部以下。双方表情可读，但人物不要正脸直视镜头，视线应自然看向画面内物件、事件、路径、窗外、远处或画面外侧。
	7. 按五层设计计划综合成一张图，不要把五层写成技术说明。
	8. 最后只输出一段“单张图片应该是什么样”的中文描述。
	9. 不要输出环境层、关系结构层、L1/L2、visualDials 等术语。

视觉内容计划：
{json.dumps(visual_plan, ensure_ascii=False)}

图谱规则命中：
{json.dumps(trace, ensure_ascii=False)}

开放内容视觉化：
{json.dumps(open_content, ensure_ascii=False)}

输出 JSON：
{{
	  "semantic_visual_instruction": "4-7句中文。必须包含连续完整的1:1方形1536×1536画布、禁止中央面板和左右边条、关系空间模式、两端人物高度为全画布34%到40%且不得超过42%、人物完整轮廓位于x=0.20到0.80、人物完整头身且平台不得从腰部截断、道具允许自然遮挡腰部以下、人物不直视镜头、具体事件物件、按visualDials转译后的空间/光线/花果/光点/动势效果、禁止项。关系近时不得再要求中间路径约20%。"
}}
""".strip()
        try:
            self._log(run_id, "semantic compiler LLM request started")
            text = await provider.chat(
                prompt,
                system="你只输出可解析 JSON。你把结构化视觉内容计划编译成单张关系壁纸描述。",
                response_format={"type": "json_object"},
                temperature=0.15,
            )
            data = self._loads_json(text)
            instruction = str(data.get("semantic_visual_instruction", "")).strip()
            if not instruction:
                self._log(run_id, "semantic compiler LLM returned empty instruction")
                return None
            forbidden_terms = ("环境层", "关系结构层", "物件事件层", "人物层", "动态反馈层", "L1", "L2", "L3", "L4", "L5")
            if any(term in instruction for term in forbidden_terms):
                self._log(run_id, "semantic compiler LLM output contained scaffold terms, fallback")
                return None
            self._log(run_id, "semantic compiler LLM response parsed")
            return instruction
        except (KeyError, TypeError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            self._log(run_id, f"semantic compiler LLM failed, fallback={type(exc).__name__}: {exc}")
            return None

    def _compile_image_description_fallback(self, visual_plan: dict[str, Any]) -> str:
        composition = visual_plan["composition"]
        layers = visual_plan["mappingLayers"]
        proposition = visual_plan["visualProposition"]
        open_items = "、".join(item["source"] for item in visual_plan["openContentVisualizations"]) or "当前生活物件"
        emotion_strength = visual_plan["generationParameters"]["emotionVisualStrength"]
        dial_cues = self._visual_dial_prompt_cues(visual_plan.get("visualDials", {}))
        spatial_mode = str(
            (visual_plan.get("deterministicL2") or {}).get("spatialMode")
            or "river_and_path"
        )
        spatial_sentence = {
            "merged": "两块完整生活平台融合为同一片共享地面，中央无河无路",
            "path_only": "两块完整生活平台明显靠近，中央只有一条短而窄的小路，没有河流、溪流或桥",
            "river_and_path": "两块完整生活平台保持中等距离，中央同时保留河流和小路",
            "river_only": "两块完整生活平台进一步拉开，中央只有河水，没有小路或桥",
            "peripheral": "两块完整生活平台保持最宽间隔，中央以宽阔河流保持大尺度距离且没有直接小路或桥；两个人物仍完整位于中央显示安全区内",
        }.get(spatial_mode, "中央同时保留河流和小路")
        layout_intro = (
            "请生成一张连续完整的1:1方形1536×1536关系壁纸。整个画布是同一个连续纸雕世界，不划分中央面板和左右边条；"
            + spatial_sentence
            + "。人物和家具随各自平台整体移动，不能只移动人物。两端人物高度均为全画布34%到40%且不得超过42%，完整轮廓位于x=0.20到0.80，身体中心位于x=0.30到0.70；不得出现垂直接缝、色差边界或拼贴分区。"
        )
        center_sentence = (
            f"中央关系区域表达‘{proposition}’：{composition['centerZone']['description']}。"
        )
        connection_rule = "五档空间硬约束：" + spatial_sentence + "；不得混入其他档位的中央元素。"

        return "".join(
            [
                layout_intro,
                f"中央安全区左侧偏下是{composition['leftBottomZone']['actor']}：{composition['leftBottomZone']['description']}，人物头到脚高度为全画布34%到40%，应自然地做自己的事情，并保留{open_items}等具体生活锚点。人物采用完整头身构图，平台边缘不得从腰部或躯干截断；有意义的活动道具允许自然遮挡腰部以下。",
                f"中央安全区右侧偏上是{composition['upperRightZone']['actor']}：{composition['upperRightZone']['description']}，人物头到脚高度为全画布34%到40%，对方也应在自己的生活场景里自然活动，可以呈现为人物、生活空间或半透明想象场景。人物采用完整头身构图，平台边缘不得从腰部或躯干截断；有意义的活动道具允许自然遮挡腰部以下。",
                center_sentence,
                f"关系空间参数要这样呈现：{dial_cues}",
                f"画面情绪强度约为{emotion_strength}，整体要把{self._join_layer([*layers.get('L1', []), *layers.get('L4', [])])}转化成光线、色温、姿态和空间距离。",
                connection_rule,
                "双方脸部和表情要可读，但不要正脸直视镜头；视线应自然看向画面内事件、物件、路径、窗外、天空、远处、手中的东西或画面外侧，像安静观察到的生活瞬间。",
                "默认这是异步陪伴关系，不是实时通话；不要出现手机通话、视频通话、举着手机对话或挥手打招呼。",
                "不要生成普通风景图，不要把人物推到中央安全区之外，不要让双方变成近距离团圆合照，不要出现正面证件照、自拍感或人物盯着观众；不要出现文字、按钮、UI、logo、水印、聊天气泡或字幕。",
            ]
        )

    def _build_visual_dials(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        intensity: float,
        ambiguity_clarity: float,
        disclosure_depth: float,
    ) -> dict[str, Any]:
        short_table = language.short_term_table or {}
        long_table = memory.long_term_table or {}
        affect = self._value_text(self._table_value(short_table, "B_affective_semantics", "momentary_affect"))
        affect_intensity = self._value_text(self._table_value(short_table, "B_affective_semantics", "affective_intensity"))
        intent = self._value_text(self._table_value(short_table, "C_communicative_semantics", "intent_type"))
        desired_response = self._value_text(self._table_value(short_table, "C_communicative_semantics", "desired_response"))
        social_connection = self._value_text(
            self._table_value(long_table, "D_longitudinal_state_semantics", "social_connection_cue")
        )
        fatigue_vitality = self._value_text(
            self._table_value(long_table, "D_longitudinal_state_semantics", "fatigue_vitality_cue")
        )
        stability = self._value_text(
            self._table_value(long_table, "D_longitudinal_state_semantics", "stability_fluctuation")
        )
        interaction_frequency = self._value_text(
            self._table_value(long_table, "E_relational_semantics", "interaction_frequency")
        )
        intimacy = self._value_text(self._table_value(long_table, "E_relational_semantics", "intimacy_distance"))
        reciprocity = self._value_text(self._table_value(long_table, "E_relational_semantics", "reciprocity"))
        warmth = self._value_text(self._table_value(long_table, "E_relational_semantics", "emotional_warmth"))
        trend = self._value_text(self._table_value(long_table, "E_relational_semantics", "relationship_trend"))

        path_width = 0.48
        path_continuity = 0.72
        path_brightness = 0.58
        path_length = 0.55
        shared_space_ratio = 0.35
        negative_space = 0.34
        boundary_softness = 0.58
        flower_fruit_density = 0.42
        light_particle_density = 0.32
        warm_light_strength = 0.58
        saturation = 0.48
        shadow_softness = 0.52
        motion_energy = 0.45
        weather_disturbance = 0.18
        visual_change_amplitude = 0.30

        if "近" in intimacy:
            path_length -= 0.18
            path_width += 0.12
            path_brightness += 0.12
            shared_space_ratio += 0.16
            negative_space -= 0.12
        elif "远" in intimacy or "距离" in intimacy:
            path_length += 0.18
            path_width -= 0.12
            path_brightness -= 0.10
            shared_space_ratio -= 0.12
            negative_space += 0.16

        if any(word in warmth for word in ("温暖", "稳定")):
            warm_light_strength += 0.16
            boundary_softness += 0.12
            flower_fruit_density += 0.18
            path_brightness += 0.08
        if any(word in warmth for word in ("疏离", "冷淡")):
            warm_light_strength -= 0.22
            flower_fruit_density -= 0.18
            path_brightness -= 0.14
            negative_space += 0.18
        if "克制" in warmth:
            light_particle_density -= 0.10
            saturation -= 0.08
        if "安慰" in warmth:
            boundary_softness += 0.18
            shadow_softness += 0.10

        if any(word in trend for word in ("靠近", "修复")):
            path_continuity += 0.14
            path_brightness += 0.12
            flower_fruit_density += 0.10
            negative_space -= 0.08
        if any(word in trend for word in ("疏远", "停滞")):
            path_width -= 0.10
            path_continuity -= 0.16
            path_brightness -= 0.12
            negative_space += 0.14
            flower_fruit_density -= 0.10

        if any(word in interaction_frequency for word in ("经常", "密集")):
            light_particle_density += 0.22
            visual_change_amplitude += 0.12
        elif "偶尔" in interaction_frequency:
            light_particle_density += 0.02
        elif any(word in interaction_frequency for word in ("未联系", "减少", "少")):
            light_particle_density -= 0.16
            path_brightness -= 0.08
        if any(word in social_connection for word in ("陪伴减少", "独处", "减少")):
            negative_space += 0.12
            light_particle_density -= 0.10
        if "双向" in reciprocity:
            light_particle_density += 0.10
            path_continuity += 0.06
        elif "单向" in reciprocity:
            light_particle_density -= 0.06
            shared_space_ratio -= 0.06

        if any(word in affect for word in ("愉悦", "期待")):
            saturation += 0.12
            motion_energy += 0.12
            warm_light_strength += 0.10
        elif any(word in affect for word in ("平静", "思念")):
            saturation -= 0.04
            motion_energy -= 0.04
        elif any(word in affect for word in ("焦虑", "悲伤", "疲惫")):
            saturation -= 0.12
            motion_energy -= 0.18
            shadow_softness += 0.12
            boundary_softness += 0.08
        if any(word in fatigue_vitality for word in ("疲惫", "活动减少", "动作变慢", "休息")):
            motion_energy -= 0.16
            saturation -= 0.06
        elif any(word in fatigue_vitality for word in ("活力恢复", "活动恢复")):
            motion_energy += 0.12
            saturation += 0.06
        if "波动" in affect_intensity or "波动" in stability:
            weather_disturbance += 0.18
            visual_change_amplitude += 0.16
        elif any(word in stability for word in ("稳定", "恢复")):
            weather_disturbance -= 0.06
            path_continuity += 0.06

        intensity = self._clamp(intensity, 0.0, 1.0)
        saturation = (saturation * 0.65) + (intensity * 0.35)
        path_brightness = (path_brightness * 0.75) + (intensity * 0.25)
        facial_expression_clarity = self._clamp(ambiguity_clarity, 0.25, 0.90)

        dials = {
            "sourceValues": {
                "momentaryAffect": affect,
                "affectiveIntensity": affect_intensity,
                "intentType": intent,
                "desiredResponse": desired_response,
                "socialConnectionCue": social_connection,
                "fatigueVitalityCue": fatigue_vitality,
                "stabilityFluctuation": stability,
                "interactionFrequency": interaction_frequency,
                "intimacyDistance": intimacy,
                "reciprocity": reciprocity,
                "emotionalWarmth": warmth,
                "relationshipTrend": trend,
            },
            "relationshipSpace": {
                "pathWidth": self._dial(path_width),
                "pathContinuity": self._dial(path_continuity),
                "pathBrightness": self._dial(path_brightness),
                "pathLength": self._dial(path_length),
                "sharedSpaceRatio": self._dial(shared_space_ratio),
                "negativeSpace": self._dial(negative_space),
                "boundarySoftness": self._dial(boundary_softness),
            },
            "affectiveAtmosphere": {
                "warmLightStrength": self._dial(warm_light_strength),
                "saturation": self._dial(saturation),
                "shadowSoftness": self._dial(shadow_softness),
                "motionEnergy": self._dial(motion_energy),
                "facialExpressionClarity": self._dial(facial_expression_clarity),
            },
            "livingMetaphor": {
                "flowerFruitDensity": self._dial(flower_fruit_density),
                "lifeTraceDensity": self._dial(disclosure_depth),
                "pathGrowthSignal": self._dial(path_continuity * 0.6 + flower_fruit_density * 0.4),
            },
            "feedback": {
                "lightParticleDensity": self._dial(light_particle_density),
                "responsePressure": self._dial(0.18 if desired_response in {"看见即可", "轻触回应"} else 0.34),
                "feedbackLocality": "path_nodes_or_object_edges_only",
            },
            "stability": {
                "weatherDisturbance": self._dial(weather_disturbance),
                "visualChangeAmplitude": self._dial(visual_change_amplitude),
            },
        }
        dials["promptCues"] = self._visual_dial_prompt_cues(dials)
        return dials

    def _visual_dial_prompt_cues(self, visual_dials: dict[str, Any]) -> str:
        if not visual_dials:
            return "中间路径保持清晰连续，暖光和光点克制，空间留白适中。"
        relationship = visual_dials.get("relationshipSpace", {})
        affective = visual_dials.get("affectiveAtmosphere", {})
        metaphor = visual_dials.get("livingMetaphor", {})
        feedback = visual_dials.get("feedback", {})
        stability = visual_dials.get("stability", {})
        return (
            f"路径宽度{self._dial_label(relationship.get('pathWidth'))}、连续性{self._dial_label(relationship.get('pathContinuity'))}、亮度{self._dial_label(relationship.get('pathBrightness'))}，"
            f"两端共享空间{self._dial_label(relationship.get('sharedSpaceRatio'))}，空间留白{self._dial_label(relationship.get('negativeSpace'))}，边界柔和度{self._dial_label(relationship.get('boundarySoftness'))}；"
            f"暖光强度{self._dial_label(affective.get('warmLightStrength'))}、色彩饱和度{self._dial_label(affective.get('saturation'))}、人物动势{self._dial_label(affective.get('motionEnergy'))}、表情清晰度{self._dial_label(affective.get('facialExpressionClarity'))}；"
            f"花苞/果实密度{self._dial_label(metaphor.get('flowerFruitDensity'))}，路径光点密度{self._dial_label(feedback.get('lightParticleDensity'))}，天气或光影扰动{self._dial_label(stability.get('weatherDisturbance'))}。"
        )

    def _dial(self, value: float) -> dict[str, Any]:
        value = self._clamp(value, 0.0, 1.0)
        return {"value": round(value, 2), "level": self._dial_level(value)}

    def _dial_level(self, value: float) -> str:
        if value < 0.25:
            return "very_low"
        if value < 0.45:
            return "low"
        if value < 0.65:
            return "medium"
        if value < 0.82:
            return "high"
        return "very_high"

    def _dial_label(self, dial: Any) -> str:
        if not isinstance(dial, dict):
            return "中等"
        labels = {
            "very_low": "很低",
            "low": "偏低",
            "medium": "中等",
            "high": "偏高",
            "very_high": "很高",
        }
        return labels.get(str(dial.get("level")), "中等")

    def _clamp(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _build_five_layer_plan(
        self,
        mapping_layers: dict[str, list[str]],
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
    ) -> dict[str, Any]:
        short_table = language.short_term_table or {}
        long_table = memory.long_term_table or {}
        event = self._value_text(self._table_value(short_table, "A_situational_semantics", "event"))
        scene = self._value_text(self._table_value(short_table, "A_situational_semantics", "scene"))
        objects = self._value_text(self._table_value(short_table, "A_situational_semantics", "object"))
        affect = self._value_text(self._table_value(short_table, "B_affective_semantics", "momentary_affect"))
        intent = self._value_text(self._table_value(short_table, "C_communicative_semantics", "intent_type"))
        distance = self._value_text(self._table_value(long_table, "E_relational_semantics", "intimacy_distance"))
        warmth = self._value_text(self._table_value(long_table, "E_relational_semantics", "emotional_warmth"))
        trend = self._value_text(self._table_value(long_table, "E_relational_semantics", "relationship_trend"))

        return {
            "L1_environment_layer": {
                "sourceFields": ["A.scene", "A.time", "B.momentary_affect"],
                "designQuestion": "对方在哪里、什么时候、整体氛围如何被感知？",
                "designContent": self._unique(
                    [
                        f"以{scene or '生活空间'}作为可识别场域",
                        f"用{affect or '平静'}情绪调节光线、色温和空气感",
                        *mapping_layers.get("L1", []),
                    ]
                ),
            },
            "L2_relational_structure_layer": {
                "sourceFields": ["E.intimacy_distance", "E.emotional_warmth", "E.relationship_trend", "C.intent_type"],
                "designQuestion": "双方关系如何，是靠近、疏远、修复还是稳定？",
                "designContent": self._unique(
                    [
                        f"用左下到右上的真实空间路径表达{intent or '分享生活'}",
                        f"路径距离和边界感体现{distance or '适度距离'}",
                        f"路径亮度、开合和连续性体现{warmth or '温暖'}与{trend or '稳定'}",
                        *mapping_layers.get("L2", []),
                    ]
                ),
            },
            "L3_object_event_layer": {
                "sourceFields": ["A.event", "A.object"],
                "designQuestion": "最近发生了什么，哪些物件承载这件事？",
                "designContent": self._unique(
                    [
                        f"保留{event or '当前事件'}作为主要生活事件",
                        f"保留{objects or '生活痕迹'}作为具体事件锚点",
                        *mapping_layers.get("L3", []),
                    ]
                ),
            },
            "L4_character_layer": {
                "sourceFields": ["A.subject", "B.momentary_affect", "D.fatigue_vitality_cue"],
                "designQuestion": "人物是谁，正在以什么状态生活？",
                "designContent": self._unique(
                    [
                        "中央安全区左侧偏下的父母人物和右侧偏上的子女人物都要看见脸部与表情",
                        "两端人物头到脚高度均为全画布34%到40%，任一人物不得超过42%",
                        "两端人物完整轮廓位于x=0.20到0.80，身体中心位于x=0.30到0.70，并保留事件物件和生活环境",
                        "双方采用完整头身构图，平台前沿必须下移到身体下方，不得从腰部或躯干截断人物",
                        "桌子、购物篮、座椅等有意义的活动道具可以自然遮挡腰部以下，但人物身体必须在道具后保持连续可信",
                        "人物不要正脸直视镜头，视线自然看向画面内事件、物件、路径或画面外侧",
                        "双方各自在自己的生活场景里自然行动，不默认实时通话",
                        *mapping_layers.get("L4", []),
                    ]
                ),
            },
            "L5_motion_feedback_layer": {
                "sourceFields": ["C.desired_response", "E.reciprocity", "E.interaction_frequency"],
                "designQuestion": "交互如何被轻量提示，如何维持连接？",
                "designContent": self._unique(
                    [
                        "交互反馈只作用在路径节点、物件旁或局部区域",
                        "光点可以作为路径点缀，不能替代中间路径本身",
                        *mapping_layers.get("L5", []),
                    ]
                ),
            },
        }

    def _build_structured_retrieval_query(self, entries: list[dict[str, Any]]) -> str:
        return "；".join(
            f"{entry.get('roleScope') + '.' if entry.get('roleScope') else ''}{entry['field']}: {entry['valueText']}"
            for entry in entries
        )

    def _merge_mapping_trace(self, graph_hits: list[dict[str, Any]], retrieval_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in graph_hits:
            item_id = str(hit.get("id"))
            seen.add(item_id)
            merged.append(self._compact_trace_hit(hit))
        for hit in retrieval_hits:
            item_id = str(hit.get("id"))
            if item_id in seen:
                continue
            seen.add(item_id)
            merged.append(self._compact_trace_hit({**hit, "match_type": "semantic_retrieval"}))
        return merged

    def _compact_trace_hit(self, hit: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": hit.get("id"),
            "field": hit.get("field"),
            "semantic_slot": hit.get("semantic_slot"),
            "value": hit.get("value"),
            "category": hit.get("category"),
            "content_type": hit.get("content_type"),
            "strategy": hit.get("strategy"),
            "mapping_layers": hit.get("mapping_layers", hit.get("layers", [])),
            "visual_metaphors": hit.get("visual_metaphors", []),
            "composition_effect": hit.get("composition_effect", {}),
            "parameters": hit.get("parameters", {}),
            "must_include": hit.get("must_include", []),
            "avoid": hit.get("avoid", []),
            "match_type": hit.get("match_type", ""),
            "score": hit.get("score", 1.0),
        }

    def _loads_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise
            return json.loads(match.group(0))

    def _viewer_role(self, language: LanguageEmotionResult) -> str:
        raw = language.raw or {}
        speaker_context = raw.get("speaker_context") if isinstance(raw.get("speaker_context"), dict) else {}
        role = str(
            raw.get("viewerRole")
            or raw.get("viewer_role")
            or speaker_context.get("speaker_role")
            or "parent"
        )
        if role in {"elder", "old", "parent", "mother", "father"}:
            return "parent"
        if role in {"child", "daughter", "son"}:
            return "child"
        return "parent"

    def _speaker_role(self, language: LanguageEmotionResult) -> str:
        raw = language.raw or {}
        speaker_context = raw.get("speaker_context") if isinstance(raw.get("speaker_context"), dict) else {}
        role = str(
            raw.get("viewerRole")
            or raw.get("viewer_role")
            or speaker_context.get("speaker_role")
            or ""
        ).lower()
        if role in {"elder", "old", "parent", "mother", "father"}:
            return "elder"
        if role in {"child", "daughter", "son"}:
            return "child"
        return ""

    def _actor_roles(self, viewer_role: str) -> tuple[str, str]:
        del viewer_role
        return "老人或父母一方", "子女一方"

    def _role_zone_description(
        self,
        actor: str,
        role_state: dict[str, Any],
        *,
        zone: str,
    ) -> str:
        scene = role_state.get("sceneRaw") or "已保存的生活场景"
        event = role_state.get("event") or "日常生活"
        objects = "、".join(str(item) for item in role_state.get("objects", []) if item) or "生活物件"
        affect = role_state.get("affect") or "平静"
        time_value = role_state.get("timeRaw") or "已保存的时间状态"
        lighting = role_state.get("lighting") or {}
        brightness = lighting.get("brightness")
        brightness_text = f"局部亮度约{brightness}" if isinstance(brightness, (int, float)) else "保留既有局部光照"
        lamp_state = lighting.get("lampState") or "contextual"
        scale_rule = "人物头到脚高度为全画布34%到40%且不得超过42%，完整轮廓位于x=0.20到0.80"
        if not role_state.get("available"):
            return f"{actor}暂无可用短期表，保持上一张壁纸中的生活场景、时间光线、位置和尺度不变；{scale_rule}"
        return (
            f"{actor}位于{scene}，时间是{time_value}，正在经历或回想{event}，身边有{objects}，"
            f"神情带有{affect}但不过度戏剧化；{brightness_text}，局部灯光状态为{lamp_state}；"
            f"{scale_rule}；采用完整头身构图，平台前沿位于身体下方且不得从腰部或躯干截断；"
            "桌子、购物篮、座椅等活动道具可以自然遮挡腰部以下；脸部可读但不要正脸直视镜头"
        )

    def _left_bottom_description(self, actor: str, language: LanguageEmotionResult) -> str:
        short_table = language.short_term_table or {}
        scene = self._value_text(self._table_value(short_table, "A_situational_semantics", "scene")) or "生活场景"
        event = self._value_text(self._table_value(short_table, "A_situational_semantics", "event")) or "日常生活"
        objects = self._value_text(self._table_value(short_table, "A_situational_semantics", "object")) or "生活物件"
        emotion = self._value_text(self._table_value(short_table, "B_affective_semantics", "momentary_affect")) or "平静"
        return f"{actor}位于{scene}中，正在经历或回想{event}，身边有{objects}，神情带有{emotion}但不过度戏剧化；人物头到脚高度为全画布34%到40%，完整轮廓位于中央显示安全区，保留事件物件和生活环境，脸部可读但不要正脸直视镜头"

    def _upper_right_description(self, actor: str, memory: MemoryRelationResult) -> str:
        long_table = memory.long_term_table or {}
        trend = self._value_text(self._table_value(long_table, "E_relational_semantics", "relationship_trend")) or "稳定"
        distance = self._value_text(self._table_value(long_table, "E_relational_semantics", "intimacy_distance")) or "稍远"
        warmth = self._value_text(self._table_value(long_table, "E_relational_semantics", "emotional_warmth")) or "温暖"
        return f"{actor}以右侧偏上的生活空间、人物或半透明想象场景出现；人物头到脚高度为全画布34%到40%，完整轮廓位于中央显示安全区；人物脸部和表情必须清晰可见，但不要正脸直视镜头，应自然地做自己的事情，不默认打电话或视频，表现关系趋势为{trend}、距离感为{distance}、情感温度为{warmth}"

    def _left_bottom_relationship_description(
        self,
        actor: str,
        memory: MemoryRelationResult,
    ) -> str:
        long_table = memory.long_term_table or {}
        trend = self._value_text(
            self._table_value(
                long_table,
                "E_relational_semantics",
                "relationship_trend",
            )
        ) or "稳定"
        warmth = self._value_text(
            self._table_value(
                long_table,
                "E_relational_semantics",
                "emotional_warmth",
            )
        ) or "温暖"
        return (
            f"{actor}位于中央安全区左侧偏下的生活空间里自然活动，作为本次未说话的一方保持中性日常状态；"
            "人物头到脚高度为全画布34%到40%，完整轮廓不得越过中央显示安全区，"
            f"脸部可读但不要直视镜头；关系趋势为{trend}、情感温度为{warmth}"
        )

    def _upper_right_speaker_description(
        self,
        actor: str,
        language: LanguageEmotionResult,
    ) -> str:
        short_table = language.short_term_table or {}
        scene = self._value_text(
            self._table_value(short_table, "A_situational_semantics", "scene")
        ) or "生活场景"
        event = self._value_text(
            self._table_value(short_table, "A_situational_semantics", "event")
        ) or "日常生活"
        objects = self._value_text(
            self._table_value(short_table, "A_situational_semantics", "object")
        ) or "生活物件"
        emotion = self._value_text(
            self._table_value(
                short_table,
                "B_affective_semantics",
                "momentary_affect",
            )
        ) or "平静"
        return (
            f"{actor}位于中央安全区右侧偏上的{scene}中，正在经历或回想{event}，"
            f"身边有{objects}，神情带有{emotion}但不过度戏剧化；"
            "人物头到脚高度为全画布34%到40%，完整轮廓不得越过中央显示安全区，脸部和表情清晰可读但不要直视镜头"
        )

    def _center_relation_description(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        relationship_metaphors: list[str],
        relation_state_metaphors: list[str],
        deterministic_l2: dict[str, Any] | None = None,
    ) -> str:
        short_table = language.short_term_table or {}
        long_table = memory.long_term_table or {}
        intent = self._value_text(self._table_value(short_table, "C_communicative_semantics", "intent_type")) or "分享生活"
        response = self._value_text(self._table_value(short_table, "C_communicative_semantics", "desired_response")) or "轻触回应"
        trend = self._value_text(self._table_value(long_table, "E_relational_semantics", "relationship_trend")) or "稳定"
        spatial_mode = str((deterministic_l2 or {}).get("spatialMode") or "river_and_path")
        if spatial_mode == "merged":
            return (
                f"关系趋势为{trend}，两块生活平台连同人物、家具和场景边缘一起向中央靠近，"
                "平台边缘必须相接或轻微交叠并形成同一片共享地面；删除两人之间原有的道路、河流、溪流、桥、沟壑和大片留白，"
                "用共享庭院、连续花木、相接地面和交叠纸层填满中央。"
                f"{response}只形成花朵、物件旁或共享区域内的轻量反馈。"
            )
        if spatial_mode == "path_only":
            return (
                f"关系趋势为{trend}，两块生活平台明显靠近，中央只保留一条短而窄的小路；"
                "删除河流、溪流、桥和沟壑。"
                f"{response}只形成小路或物件旁的轻量反馈。"
            )
        if spatial_mode == "river_only":
            return (
                f"关系趋势为{trend}，两块生活平台进一步拉开，中央只保留河水；"
                "删除连接小路和桥。"
                f"{response}只形成两岸物件旁的轻量反馈。"
            )
        if spatial_mode == "peripheral":
            return (
                f"关系趋势为{trend}，两块生活平台保持最宽间隔，但两个人物仍完整位于中央显示安全区内，"
                "中央保留宽阔河流形成大尺度距离，不建立直接小路或桥。"
                f"{response}只形成两岸各自空间内的轻量反馈。"
            )
        if spatial_mode == "river_and_path":
            return (
                f"关系趋势为{trend}，两块生活平台保持中等距离，中央同时保留一条河流和一条小路。"
                f"{response}只形成河岸、小路或物件旁的轻量反馈。"
            )
        metaphors = "；".join(self._unique([*relationship_metaphors, *relation_state_metaphors])) or "用小路、桥、走廊、河岸、庭院路径或空间边界连接两端"
        return f"围绕{intent}建立从左下到右上的真实空间路径，结合{trend}的关系趋势，{metaphors}。{response}只影响路径节点、物件旁或局部区域的轻量反馈，光点可以点缀路径，但不能替代路径本身。"

    def _final_visual_proposition(self, language: LanguageEmotionResult, memory: MemoryRelationResult) -> str:
        short_table = language.short_term_table or {}
        long_table = memory.long_term_table or {}
        event = self._value_text(self._table_value(short_table, "A_situational_semantics", "event")) or "一次生活分享"
        objects = self._value_text(self._table_value(short_table, "A_situational_semantics", "object")) or "生活痕迹"
        intent = self._value_text(self._table_value(short_table, "C_communicative_semantics", "intent_type")) or "分享生活"
        warmth = self._value_text(self._table_value(long_table, "E_relational_semantics", "emotional_warmth")) or "温暖"
        return f"把{event}中的{objects}转化为一条承载{intent}和{warmth}关系温度的视觉连接"

    def _live_call_allowed(self, language: LanguageEmotionResult) -> bool:
        short_table = language.short_term_table or {}
        structured_text = " ".join(
            self._value_text(value)
            for value in [
                self._table_value(short_table, "A_situational_semantics", "event"),
                self._table_value(short_table, "A_situational_semantics", "object"),
                self._table_value(short_table, "C_communicative_semantics", "intent_type"),
                self._table_value(short_table, "C_communicative_semantics", "desired_response"),
                self._table_value(short_table, "C_communicative_semantics", "disclosure_depth"),
            ]
        )
        return any(word in structured_text for word in ("打电话", "视频", "视频通话", "通话", "电话", "语音通话"))

    def _content_for_layers(
        self,
        graph_hits: list[dict[str, Any]],
        open_content: list[dict[str, str]],
        layer_ids: list[str],
    ) -> list[str]:
        output: list[str] = []
        layer_set = set(layer_ids)
        for hit in graph_hits:
            if not layer_set.intersection(hit.get("mapping_layers", [])):
                continue
            role = hit.get("role")
            value = hit.get("value")
            if role and value:
                output.append(f"{hit.get('field')}={value}：{role}")
            visual_mapping = hit.get("aigc_visual_mapping") or hit.get("visual_mapping")
            if visual_mapping:
                output.append(f"AIGC视觉映射：{visual_mapping}")
            for metaphor in hit.get("visual_metaphors", []):
                desc = metaphor.get("description")
                if desc:
                    output.append(desc)
        for item in open_content:
            target_layer = item.get("targetLayer")
            if target_layer in layer_set:
                output.append(item["visualization"])
        return self._unique(output)

    def _metaphor_descriptions(self, graph_hits: list[dict[str, Any]], fields: tuple[str, ...]) -> list[str]:
        output: list[str] = []
        for hit in graph_hits:
            if hit.get("field") not in fields:
                continue
            for metaphor in hit.get("visual_metaphors", []):
                desc = metaphor.get("description")
                if desc:
                    output.append(desc)
        return self._unique(output)

    def _numeric_value(self, graph_hits: list[dict[str, Any]], field: str, default: float) -> float:
        for hit in graph_hits:
            if hit.get("field") == field and isinstance(hit.get("numeric_value"), (int, float)):
                return float(hit["numeric_value"])
        return default

    def _open_content_sentence(self, field: str, value: str) -> str:
        if field == "object":
            return f"直接把{value}作为画面中的具体生活痕迹、事件物件或可触摸记忆锚点。"
        if field == "scene":
            return f"把{value}具体化为可识别的生活空间结构，而不是抽象背景。"
        if field == "event":
            return f"用人物动作、环境痕迹或物件状态表达{value}这件事。"
        if field == "subject":
            return f"把{value}作为人物身份或陪伴关系线索处理。"
        return f"保留{value}作为具体视觉内容。"

    def _target_content(self, field: str) -> str:
        if field in {"scene", "time"}:
            return "L1"
        if field == "subject":
            return "L4"
        if field in {"event", "object"}:
            return "L3"
        return "L3"

    def _table_value(self, table: dict[str, Any], section: str, field: str) -> Any:
        section_value = table.get(section, {})
        if not isinstance(section_value, dict):
            return None
        cell = section_value.get(field, {})
        if isinstance(cell, dict) and cell.get("value") not in (None, "", []):
            return cell["value"]
        return None

    def _table_confidence(
        self,
        table: dict[str, Any],
        section: str,
        field: str,
        *,
        default: float = 0.0,
    ) -> float:
        section_value = table.get(section, {})
        if not isinstance(section_value, dict):
            return self._clamp(default, 0.0, 1.0)
        cell = section_value.get(field, {})
        if not isinstance(cell, dict):
            return self._clamp(default, 0.0, 1.0)
        confidence = cell.get("confidence", default)
        try:
            return self._clamp(float(confidence), 0.0, 1.0)
        except (TypeError, ValueError):
            return self._clamp(default, 0.0, 1.0)

    def _as_list(self, value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    def _value_text(self, value: Any) -> str:
        if isinstance(value, list):
            return "、".join(str(item) for item in value if item)
        return str(value or "")

    def _flatten(self, values: Any) -> list[str]:
        output: list[str] = []
        for value in values:
            if isinstance(value, list):
                output.extend(str(item) for item in value if item)
            elif value:
                output.append(str(value))
        return output

    def _unique(self, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            value = str(value).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            output.append(value)
        return output

    def _join_layer(self, values: Any) -> str:
        if isinstance(values, list):
            return "、".join(values[:4])
        return str(values or "")

    def _log(self, run_id: str | None, message: str) -> None:
        prefix = f"[agent-run:{run_id}]" if run_id else "[agent-run]"
        print(f"{prefix} {message}", flush=True)
