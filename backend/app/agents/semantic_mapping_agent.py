from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx

from app.providers.llm import get_llm_provider
from app.schemas.agent import LanguageEmotionResult, MemoryRelationResult, SemanticMappingResult
from app.services.semantic_graph import load_semantic_graph
from app.services.semantic_rag import load_semantic_rag


class SemanticMappingAgent:
    async def run(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        run_id: str | None = None,
    ) -> SemanticMappingResult:
        graph = load_semantic_graph()
        rag = load_semantic_rag()

        entries = self._collect_structured_semantic_entries(language, memory)
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

    def _collect_structured_semantic_entries(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
    ) -> list[dict[str, Any]]:
        short_table = language.short_term_table or {}
        long_table = memory.long_term_table or {}
        specs = [
            ("short_term_table", "event", self._table_value(short_table, "A_situational_semantics", "event")),
            ("short_term_table", "scene", self._table_value(short_table, "A_situational_semantics", "scene")),
            ("short_term_table", "time", self._table_value(short_table, "A_situational_semantics", "time")),
            ("short_term_table", "subject", self._table_value(short_table, "A_situational_semantics", "subject")),
            ("short_term_table", "object", self._table_value(short_table, "A_situational_semantics", "object")),
            ("short_term_table", "momentary_affect", self._table_value(short_table, "B_affective_semantics", "momentary_affect")),
            ("short_term_table", "affective_intensity", self._table_value(short_table, "B_affective_semantics", "affective_intensity")),
            ("short_term_table", "affective_ambiguity", self._table_value(short_table, "B_affective_semantics", "affective_ambiguity")),
            ("short_term_table", "intent_type", self._table_value(short_table, "C_communicative_semantics", "intent_type")),
            ("short_term_table", "desired_response", self._table_value(short_table, "C_communicative_semantics", "desired_response")),
            ("short_term_table", "disclosure_depth", self._table_value(short_table, "C_communicative_semantics", "disclosure_depth")),
            ("long_term_table", "social_connection_cue", self._table_value(long_table, "D_longitudinal_state_semantics", "social_connection_cue")),
            ("long_term_table", "fatigue_vitality_cue", self._table_value(long_table, "D_longitudinal_state_semantics", "fatigue_vitality_cue")),
            ("long_term_table", "stability_fluctuation", self._table_value(long_table, "D_longitudinal_state_semantics", "stability_fluctuation")),
            ("long_term_table", "recovery_decline_trend", self._table_value(long_table, "D_longitudinal_state_semantics", "recovery_decline_trend")),
            ("long_term_table", "interaction_frequency", self._table_value(long_table, "E_relational_semantics", "interaction_frequency")),
            ("long_term_table", "intimacy_distance", self._table_value(long_table, "E_relational_semantics", "intimacy_distance")),
            ("long_term_table", "reciprocity", self._table_value(long_table, "E_relational_semantics", "reciprocity")),
            ("long_term_table", "emotional_warmth", self._table_value(long_table, "E_relational_semantics", "emotional_warmth")),
            ("long_term_table", "relationship_trend", self._table_value(long_table, "E_relational_semantics", "relationship_trend")),
        ]

        entries: list[dict[str, Any]] = []
        for source, field, value in specs:
            if value in (None, "", []):
                continue
            entries.append(
                {
                    "source": source,
                    "field": field,
                    "value": value,
                    "valueText": self._value_text(value),
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
    ) -> dict[str, Any]:
        viewer_role = self._viewer_role(language)
        left_actor, right_actor = self._actor_roles(viewer_role)
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
        composition_contract = graph.composition_contract or {}
        left_zone_contract = composition_contract.get("left_bottom_zone", {})
        center_zone_contract = composition_contract.get("center_zone", {})
        upper_zone_contract = composition_contract.get("upper_right_zone", {})
        character_framing_contract = composition_contract.get("character_framing", {})

        must_include = self._unique(
            [
                "竖版9:16关系壁纸",
                "右上区域约占画面50%，是对方端主画面和最大生活空间",
                "中间真实空间路径约占画面20%",
                "左下区域约占画面30%",
                "右上和左下人物都只占各自区域内部空间的1/2到2/3",
                "左下人物的最终可见尺寸约为右上人物的一半",
                "双方人物脸部和表情清晰可见",
                "双方人物不要正脸直视镜头",
                "双方各自在自己的生活场景里做自己的事情",
                "中间从左下到右上的关系映射路径",
                *self._flatten(hit.get("must_include", []) for hit in graph_hits),
            ]
        )
        live_call_allowed = self._live_call_allowed(language)
        must_avoid = self._unique(
            [
                *graph.composition_contract.get("global_avoid", []),
                *self._flatten(hit.get("avoid", []) for hit in graph_hits),
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

        return {
            "agentRole": "Designer Agent / 读表式五层视觉设计智能体",
            "inputBoundary": "只读取短期语义表、长期关系表和语义图谱；不回看原始文本，不重新做语言理解。",
            "viewerRole": viewer_role,
            "shortTermTable": language.short_term_table,
            "longTermTable": memory.long_term_table,
            "composition": {
                "format": graph.composition_contract.get("format", "vertical_9_16_wallpaper"),
                "layoutContract": {
                    "aspectRatio": "9:16",
                    "composition": "asymmetric diagonal three-zone layout",
                    "upperRightZoneRatio": upper_zone_contract.get("target_area_ratio", 0.50),
                    "middlePathZoneRatio": center_zone_contract.get("target_area_ratio", 0.20),
                    "lowerLeftZoneRatio": left_zone_contract.get("target_area_ratio", 0.30),
                    "strictRule": "右上区域约50%且为最大生活空间；中间路径约20%；左下区域约30%。右上和左下的人物都占各自区域内部空间的1/2到2/3；最终画面里左下人物的可见头身高度和脸部面积约为右上人物的1/2。",
                },
                "characterFramingContract": {
                    "scaleRule": character_framing_contract.get(
                        "scale_rule",
                        "右上和左下都采用区域内人物占比规则：人物占各自区域内部空间的1/2到2/3；同时左下人物的最终可见尺寸约为右上人物的一半，不能因为前景透视而更大。",
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
                    "function": "当前端人物的生活现场和事件发生处",
                    "description": self._left_bottom_description(left_actor, language),
                    "bbox": left_zone_contract.get("bbox"),
                    "targetAreaRatio": left_zone_contract.get("target_area_ratio"),
                    "characterScaleRule": left_zone_contract.get("character_scale_rule"),
                    "expressionRequirement": left_zone_contract.get("expression_requirement"),
                },
                "centerZone": {
                    "function": "关系映射",
                    "description": self._center_relation_description(language, memory, relationship_metaphors, relation_state_metaphors),
                    "bbox": center_zone_contract.get("bbox"),
                    "targetAreaRatio": center_zone_contract.get("target_area_ratio"),
                },
                "upperRightZone": {
                    "actor": right_actor,
                    "function": "另一方人物、生活空间或想象中的被牵挂区域",
                    "description": self._upper_right_description(right_actor, memory),
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
                "characterAreaOccupancyWithinEachZone": "1/2_to_2/3",
                "doNotEnlargeUpperRightCharacter": True,
                "bothFacesAndExpressionsVisible": True,
                "avoidDirectCameraGaze": True,
                "communicationMode": "live_call_allowed" if live_call_allowed else "asynchronous_parallel_life",
                "avoidLiveCallUnlessExplicit": not live_call_allowed,
                "layoutStrictness": "high",
            },
            "priority": {
                "primary": ["左下当前端人物", "右上另一方大区域", "中间关系路径", "当前事件锚点", "双方各自生活状态"],
                "secondary": ["生活场域", "时间光线", "情绪氛围", "低压力反馈暗示"],
                "background": ["天气", "远景", "季节质感"],
                "suppress": must_avoid,
            },
            "mustInclude": must_include,
            "mustAvoid": must_avoid,
            "visualProposition": self._final_visual_proposition(language, memory),
        }

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
        prompt = f"""
你是 TimeWallpaper 的 Designer Agent，也就是“读表式五层视觉设计智能体”。
你不重新理解原始文本，只能读取短期语义表、长期关系表、语义图谱规则和五层视觉设计计划。

任务：
1. 保持固定构图比例：右上区域约50%且为最大生活空间，中间真实空间路径约20%，左下区域约30%。
2. 短期表中的事件、场景、时间、人物、物件必须保留为具体内容，不要替换成抽象类别。
3. 长期表中的联系频率、亲密距离、情感温度、关系趋势需要转成关系结构和空间组织。
4. 必须先服从 visualDials：路径宽窄、连续性、亮度、留白、共享空间、花苞/果实、光点密度、色温和人物动势都以 visualDials 为准，再翻译成自然画面描述。
5. 默认双方是异步陪伴：各自在自己的生活场景里做自己的事情，只通过中间关系映射相连；除非视觉内容计划明确允许 live_call，否则不要出现手机通话、视频通话、举手机对话或挥手打招呼。
6. 右上和左下人物都占各自区域内部空间的1/2到2/3；同时最终画面里左下人物的可见头身高度和脸部面积约为右上人物的1/2，不要因前景透视把左下人物画得更大。双方表情可读，但人物不要正脸直视镜头，视线应自然看向画面内物件、事件、路径、窗外、远处或画面外侧。
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
  "semantic_visual_instruction": "4-7句中文。必须包含右上约50%、中间路径约20%、左下约30%、右上和左下人物各占自身区域1/2到2/3、左下人物最终可见尺寸约为右上人物一半、人物不直视镜头、具体事件物件、按visualDials转译后的路径/空间/光线/花果/光点/动势效果、禁止项。"
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

        return (
            "请生成一张单张完整的竖版9:16关系壁纸，严格采用三段式斜向构图：右上区域约占50%并作为最大生活空间，中间真实空间路径约占20%，左下区域约占30%。"
            f"左下角是{composition['leftBottomZone']['actor']}：{composition['leftBottomZone']['description']}，人物只占左下区域内部空间的1/2到2/3，且最终可见尺寸约为右上人物的一半，应自然地做自己的事情，并保留{open_items}等具体生活锚点。"
            f"右上角是{composition['upperRightZone']['actor']}所在的最大区域：{composition['upperRightZone']['description']}，右上人物占右上区域内部空间的1/2到2/3，对方也应在自己的生活场景里自然活动，可以呈现为人物、生活空间或半透明想象场景。"
            f"中间区域用从左下延伸到右上的真实空间路径表达“{proposition}”：{composition['centerZone']['description']}。"
            f"关系空间参数要这样呈现：{dial_cues}"
            f"画面情绪强度约为{emotion_strength}，整体要把{self._join_layer([*layers.get('L1', []), *layers.get('L4', [])])}转化成光线、色温、姿态和空间距离。"
            "中间主连接必须像道路、桥、走廊、河岸、庭院小径或空间边界；光点只能作为路径点缀或交互反馈节点，不能替代路径本身。"
            "双方脸部和表情要可读，但不要正脸直视镜头；视线应自然看向画面内事件、物件、路径、窗外、天空、远处、手中的东西或画面外侧，像安静观察到的生活瞬间。"
            "默认这是异步陪伴关系，不是实时通话；不要出现手机通话、视频通话、举着手机对话或挥手打招呼。"
            "不要生成普通风景图，不要把人物放到画面中央，不要让双方变成近距离团圆合照，不要出现正面证件照、自拍感或人物盯着观众；不要出现文字、按钮、UI、logo、水印、聊天气泡或字幕。"
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
                        "左下角当前端人物和右上角另一方都要看见脸部与表情",
                        "右上区域为最大生活空间，右上人物占自身区域的1/2到2/3",
                        "左下人物占左下区域内部空间的1/2到2/3，保留事件物件和生活环境",
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
        return "；".join(f"{entry['field']}: {entry['valueText']}" for entry in entries)

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

    def _actor_roles(self, viewer_role: str) -> tuple[str, str]:
        if viewer_role == "child":
            return "子女端的当前人物", "老人或父母一方"
        return "老人或父母一方", "子女一方"

    def _left_bottom_description(self, actor: str, language: LanguageEmotionResult) -> str:
        short_table = language.short_term_table or {}
        scene = self._value_text(self._table_value(short_table, "A_situational_semantics", "scene")) or "生活场景"
        event = self._value_text(self._table_value(short_table, "A_situational_semantics", "event")) or "日常生活"
        objects = self._value_text(self._table_value(short_table, "A_situational_semantics", "object")) or "生活物件"
        emotion = self._value_text(self._table_value(short_table, "B_affective_semantics", "momentary_affect")) or "平静"
        return f"{actor}位于{scene}中，正在经历或回想{event}，身边有{objects}，神情带有{emotion}但不过度戏剧化；人物占左下区域内部空间的1/2到2/3，但最终可见尺寸约为右上人物的一半，保留事件物件和生活环境，脸部可读但不要正脸直视镜头"

    def _upper_right_description(self, actor: str, memory: MemoryRelationResult) -> str:
        long_table = memory.long_term_table or {}
        trend = self._value_text(self._table_value(long_table, "E_relational_semantics", "relationship_trend")) or "稳定"
        distance = self._value_text(self._table_value(long_table, "E_relational_semantics", "intimacy_distance")) or "稍远"
        warmth = self._value_text(self._table_value(long_table, "E_relational_semantics", "emotional_warmth")) or "温暖"
        return f"{actor}以远方生活空间、人物或半透明想象场景出现，右上区域约占画面50%且是最大生活空间；人物占右上区域内部空间的1/2到2/3；人物脸部和表情必须清晰可见，但不要正脸直视镜头，应自然地做自己的事情，不默认打电话或视频，表现关系趋势为{trend}、距离感为{distance}、情感温度为{warmth}"

    def _center_relation_description(
        self,
        language: LanguageEmotionResult,
        memory: MemoryRelationResult,
        relationship_metaphors: list[str],
        relation_state_metaphors: list[str],
    ) -> str:
        short_table = language.short_term_table or {}
        long_table = memory.long_term_table or {}
        intent = self._value_text(self._table_value(short_table, "C_communicative_semantics", "intent_type")) or "分享生活"
        response = self._value_text(self._table_value(short_table, "C_communicative_semantics", "desired_response")) or "轻触回应"
        trend = self._value_text(self._table_value(long_table, "E_relational_semantics", "relationship_trend")) or "稳定"
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
