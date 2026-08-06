from __future__ import annotations

from app.core.config import settings
from app.schemas.agent import ImageGenerationResult, SemanticMappingResult
from app.services.layered_painter_tools import layered_painter_tools
from app.services.pipeline_service import generate_wallpaper_from_prompt


class ImageGenerationAgent:
    RELATIONSHIP_REFLOW_MODES = {
        "merged",
        "path_only",
        "river_and_path",
        "river_only",
        "peripheral",
    }

    async def run_base_scene(self, run_id: str | None = None) -> ImageGenerationResult:
        self._log(run_id, "painter tool selector: tool=base_scene_only")
        result = await layered_painter_tools.generate_base_scene()
        wallpaper_url = result.get("imageUrl") or ""
        if wallpaper_url:
            self._log(run_id, f"painter base scene done imageUrl={wallpaper_url}")
        else:
            self._log(run_id, "painter base scene tool returned empty image")
        return ImageGenerationResult(
            wallpaper_url=wallpaper_url,
            generation_mode="layered_painter_tool:base_scene_only",
            changed_regions=["base_scene"],
            asset_metadata={
                "layered_tool": "base_scene_only",
                "raw": result.get("raw"),
                "quality": {
                    "mode": "base_scene_tool",
                    "painter_owns_tool_selection": True,
                    "no_characters_required": True,
                },
            },
        )

    async def run(
        self,
        semantic_mapping: SemanticMappingResult,
        previous_image_url: str | None = None,
        role_reference_images: dict[str, str | None] | None = None,
        run_id: str | None = None,
        generation_stage: str | None = None,
        speaker_role: str | None = None,
    ) -> ImageGenerationResult:
        regions = self._plan_regions(
            semantic_mapping.semantic_visual_instruction,
            five_layer_plan=self._five_layer_plan(semantic_mapping),
            generation_stage=generation_stage,
            speaker_role=speaker_role or self._current_role(semantic_mapping),
        )
        self._log(run_id, f"image region planning done regions={','.join(region['region_id'] for region in regions)}")

        if generation_stage in {"first_voice", "subsequent_update"}:
            layered = await self._try_layered_painter_tool(
                semantic_mapping=semantic_mapping,
                prompt="",
                previous_image_url=previous_image_url,
                role_reference_images=role_reference_images,
                regions=regions,
                run_id=run_id,
                generation_stage=generation_stage,
                speaker_role=speaker_role,
            )
            if layered is None:
                operation = (
                    "two-pass initialization"
                    if generation_stage == "first_voice"
                    else "single-pass regional update"
                )
                raise RuntimeError(f"Seedream {operation} failed")
            return layered

        safe_instruction = self._soften_instruction(semantic_mapping.semantic_visual_instruction)
        prompt = self._build_prompt(
            safe_instruction,
            previous_image_url=previous_image_url,
            regions=regions,
            role_reference_images=role_reference_images,
        )
        self._log(run_id, f"image prompt built chars={len(prompt)}")
        self._log(
            run_id,
            "final image prompt sent upstream:\n"
            "----- image_prompt START -----\n"
            f"{prompt}\n"
            "----- image_prompt END -----",
        )
        reference_urls = self._reference_urls(role_reference_images)
        if reference_urls:
            self._log(run_id, f"image reference inputs roles={','.join(reference_urls.keys())}")
        image = await generate_wallpaper_from_prompt(
            prompt,
            run_id=run_id,
            reference_image_urls=list(reference_urls.values()),
        )
        wallpaper_url = image.get("imageUrl") or previous_image_url or ""

        return ImageGenerationResult(
            wallpaper_url=wallpaper_url,
            generation_mode="single_prompt_image_mvp",
            changed_regions=[region["region_id"] for region in regions],
            asset_metadata={
                "parent_image": previous_image_url,
                "regions": regions,
                "masks": [],
                "prompt": prompt,
                "role_references": reference_urls,
                "generation_stage": generation_stage or "",
                "speaker_role": speaker_role or "",
                "raw": image.get("raw"),
                "quality": {
                    "mode": "mvp_rule_check",
                    "no_text_or_ui_required": True,
                    "composition_consistency_required": bool(previous_image_url),
                },
            },
        )

    async def _try_layered_painter_tool(
        self,
        *,
        semantic_mapping: SemanticMappingResult,
        prompt: str,
        previous_image_url: str | None,
        role_reference_images: dict[str, str | None] | None,
        regions: list[dict],
        run_id: str | None,
        generation_stage: str | None,
        speaker_role: str | None,
    ) -> ImageGenerationResult | None:
        if not settings.layered_image_tools_enabled:
            self._log(run_id, "painter tool selector: fallback=single_prompt_image_api reason=layered_tools_disabled")
            return None

        tool_name = self._select_layered_tool(
            previous_image_url,
            role_reference_images,
            generation_stage=generation_stage,
            speaker_role=speaker_role,
            five_layer_plan=self._five_layer_plan(semantic_mapping),
        )
        if tool_name is None:
            self._log(run_id, "painter tool selector: fallback=single_prompt_image_api reason=missing_layered_inputs")
            return None

        self._log(run_id, f"painter tool selector: tool={tool_name}")
        try:
            current_role = speaker_role or self._current_role(semantic_mapping)
            if tool_name == "initialize_wallpaper_view":
                refs = self._reference_urls(role_reference_images)
                result = await layered_painter_tools.initialize_wallpaper_view(
                    base_image_url=previous_image_url or "",
                    younger_image_bytes=await layered_painter_tools.resolve_reference_bytes(refs.get("child")),
                    elder_image_bytes=await layered_painter_tools.resolve_reference_bytes(refs.get("elder")),
                    designer_five_layer_plan=self._five_layer_plan(
                        semantic_mapping
                    ),
                    semantic_visual_instruction=(
                        semantic_mapping.semantic_visual_instruction
                    ),
                    speaker_role=current_role,
                )
            elif tool_name == "reflow_shared_relationship_view":
                refs = self._reference_urls(role_reference_images)
                result = await layered_painter_tools.reflow_shared_relationship_view(
                    base_image_url=previous_image_url or "",
                    younger_image_bytes=await layered_painter_tools.resolve_reference_bytes(
                        refs.get("child")
                    ),
                    elder_image_bytes=await layered_painter_tools.resolve_reference_bytes(
                        refs.get("elder")
                    ),
                    designer_five_layer_plan=self._five_layer_plan(
                        semantic_mapping
                    ),
                    semantic_visual_instruction=(
                        semantic_mapping.semantic_visual_instruction
                    ),
                    speaker_role=current_role,
                )
            elif tool_name == "update_current_side":
                refs = self._reference_urls(role_reference_images)
                speaker_reference = refs.get(self._normalize_role(current_role))
                result = await layered_painter_tools.update_current_side(
                    base_image_url=previous_image_url or "",
                    speaker_image_bytes=await layered_painter_tools.resolve_reference_bytes(
                        speaker_reference
                    ),
                    designer_five_layer_plan=self._five_layer_plan(
                        semantic_mapping
                    ),
                    semantic_visual_instruction=(
                        semantic_mapping.semantic_visual_instruction
                    ),
                    speaker_role=current_role,
                )
            else:
                return None
        except Exception as exc:
            self._log(
                run_id,
                f"painter layered tool failed tool={tool_name} error={exc}",
            )
            return None

        raw_result = result.get("raw")
        raw_metadata = raw_result if isinstance(raw_result, dict) else {}
        wallpaper_url = result.get("imageUrl") or ""
        if not wallpaper_url:
            error_detail = str(
                raw_metadata.get("error")
                or f"Seedream returned no wallpaper for {generation_stage}"
            )
            raise RuntimeError(error_detail)

        is_seedream_initial = generation_stage == "first_voice"
        is_seedream_reflow = tool_name == "reflow_shared_relationship_view"
        is_seedream_update = (
            generation_stage == "subsequent_update" and not is_seedream_reflow
        )
        changed_regions = [region["region_id"] for region in regions]
        if is_seedream_initial:
            changed_regions = ["upper_right", "lower_left"]
        if is_seedream_update:
            changed_regions = [self._speaker_region(current_role)]
        return ImageGenerationResult(
            wallpaper_url=wallpaper_url,
            generation_mode=(
                "seedream_single_pass:first_voice"
                if is_seedream_initial
                else (
                    "seedream_single_pass:relationship_reflow"
                    if is_seedream_reflow
                    else "seedream_single_pass:subsequent_update"
                )
            ),
            changed_regions=changed_regions,
            asset_metadata={
                "parent_image": previous_image_url,
                "regions": regions,
                "masks": [],
                "prompt": (
                    raw_metadata.get("final_prompt")
                    or raw_metadata.get("prompt")
                    or prompt
                ),
                "pass_prompts": {},
                "layered_tool": tool_name,
                "role_references": self._reference_urls(role_reference_images),
                "generation_stage": generation_stage or "",
                "speaker_role": current_role,
                "raw": raw_result,
                "quality": {
                    "mode": (
                        "seedream_prompt_v2_single_pass"
                        if is_seedream_initial
                        else (
                            "seedream_prompt_v2_relationship_reflow"
                            if is_seedream_reflow
                            else "seedream_prompt_only_regional_single_pass"
                        )
                    ),
                    "designer_output_only": True,
                    "painter_owns_tool_selection": True,
                    "provider_mask_used": False,
                    "local_composite_used": False,
                },
            },
        )

    def _select_layered_tool(
        self,
        previous_image_url: str | None,
        role_reference_images: dict[str, str | None] | None,
        generation_stage: str | None = None,
        speaker_role: str | None = None,
        five_layer_plan: dict | None = None,
    ) -> str | None:
        refs = self._reference_urls(role_reference_images)
        if generation_stage == "subsequent_update":
            layout = self._relationship_layout_state(five_layer_plan or {})
            if (
                layout.get("spatialMode") in self.RELATIONSHIP_REFLOW_MODES
                and previous_image_url
                and refs.get("elder")
                and refs.get("child")
            ):
                return "reflow_shared_relationship_view"
            normalized_speaker = self._normalize_role(speaker_role)
            return (
                "update_current_side"
                if previous_image_url and refs.get(normalized_speaker)
                else None
            )
        if generation_stage == "first_voice":
            if previous_image_url and refs.get("elder") and refs.get("child"):
                return "initialize_wallpaper_view"
            return None
        return None

    @staticmethod
    def _normalize_role(role: str | None) -> str:
        return "child" if (role or "").lower() in {"child", "daughter", "son"} else "elder"

    def _current_role(self, semantic_mapping: SemanticMappingResult) -> str:
        scaffold = semantic_mapping.cognitive_scaffold or {}
        role = str(scaffold.get("viewerRole") or scaffold.get("viewer_role") or "parent").lower()
        if role in {"child", "daughter", "son"}:
            return "child"
        return "parent"

    @classmethod
    def _speaker_region(cls, role: str | None) -> str:
        return (
            "upper_right"
            if cls._normalize_role(role) == "child"
            else "left_bottom"
        )

    @staticmethod
    def _five_layer_plan(
        semantic_mapping: SemanticMappingResult,
    ) -> dict:
        scaffold = semantic_mapping.cognitive_scaffold or {}
        plan = scaffold.get("fiveLayerPlan")
        return plan if isinstance(plan, dict) else {}

    def _plan_regions(
        self,
        instruction: str,
        *,
        five_layer_plan: dict | None = None,
        generation_stage: str | None = None,
        speaker_role: str | None = None,
    ) -> list[dict]:
        layout = self._relationship_layout_state(five_layer_plan or {})
        spatial_mode = str(layout.get("spatialMode") or "")
        if spatial_mode in self.RELATIONSHIP_REFLOW_MODES:
            elder_anchor = self._normalized_anchor(
                layout.get("elderAnchor"),
                fallback=[0.375, 0.642],
            )
            child_anchor = self._normalized_anchor(
                layout.get("childAnchor"),
                fallback=[0.57, 0.393],
            )
            elder_platform_anchor = self._normalized_anchor(
                layout.get("elderPlatformAnchor"),
                fallback=elder_anchor,
            )
            child_platform_anchor = self._normalized_anchor(
                layout.get("childPlatformAnchor"),
                fallback=child_anchor,
            )
            shared_scene_ratio = self._normalized_ratio(
                layout.get("sharedSceneRatio"),
                fallback=0.65,
            )
            environment_merge_ratio = self._normalized_ratio(
                layout.get("environmentMergeRatio"),
                fallback=0.675,
            )
            person_gap_ratio = self._normalized_ratio(
                layout.get("personGapRatio"),
                fallback=0.228,
            )
            platform_gap_ratio = self._normalized_ratio(
                layout.get("platformGapRatio"),
                fallback=0.0 if spatial_mode == "merged" else 0.25,
            )
            platform_overlap_ratio = self._normalized_ratio(
                layout.get("platformOverlapRatio"),
                fallback=0.0,
            )
            shared_ground_ratio = self._normalized_ratio(
                layout.get("sharedGroundRatio"),
                fallback=0.5,
            )
            padding_x = 0.16 + (0.06 * shared_scene_ratio)
            padding_y = 0.18 + (0.05 * shared_scene_ratio)
            shared_bbox = [
                round(max(0.0, min(elder_anchor[0], child_anchor[0], elder_platform_anchor[0], child_platform_anchor[0]) - padding_x), 3),
                round(max(0.04, min(elder_anchor[1], child_anchor[1], elder_platform_anchor[1], child_platform_anchor[1]) - padding_y), 3),
                round(min(1.0, max(elder_anchor[0], child_anchor[0], elder_platform_anchor[0], child_platform_anchor[0]) + padding_x), 3),
                round(min(0.96, max(elder_anchor[1], child_anchor[1], elder_platform_anchor[1], child_platform_anchor[1]) + padding_y), 3),
            ]
            return [
                {
                    "region_id": (
                        "shared_relationship_space"
                        if spatial_mode == "merged"
                        else "relationship_layout_space"
                    ),
                    "semantic_axes": [
                        "relationship_content",
                        "character_content",
                        "object_event_content",
                        "environment_content",
                    ],
                    "bbox": shared_bbox,
                    "strength": round(0.45 + (0.35 * environment_merge_ratio), 3),
                    "role_anchors": {
                        "elder": elder_anchor,
                        "child": child_anchor,
                    },
                    "platform_anchors": {
                        "elder": elder_platform_anchor,
                        "child": child_platform_anchor,
                    },
                    "person_gap_ratio": person_gap_ratio,
                    "platform_gap_ratio": platform_gap_ratio,
                    "platform_overlap_ratio": platform_overlap_ratio,
                    "shared_ground_ratio": shared_ground_ratio,
                    "shared_scene_ratio": shared_scene_ratio,
                    "environment_merge_ratio": environment_merge_ratio,
                    "central_feature": layout.get("centralFeature") or spatial_mode,
                    "reason": (
                        f"关系空间为{spatial_mode}：父母、子女及两块完整生活平台同时按动态锚点重排；"
                        "中央只能出现该档位指定的共享地面、小路、河流或距离留白。"
                    ),
                }
            ]

        if generation_stage == "first_voice":
            return [
                {"region_id": "upper_right"},
                {"region_id": "lower_left"},
            ]
        if generation_stage == "subsequent_update":
            return [{"region_id": self._speaker_region(speaker_role)}]

        regions = [
            {
                "region_id": "relation_path",
                "semantic_axes": ["relationship_content", "interaction_content"],
                "bbox": [0.16, 0.34, 0.84, 0.80],
                "strength": 0.35,
                "reason": "中间约20%区域必须表达从左下到右上的真实空间关系路径。",
            },
            {
                "region_id": "left_bottom_elder",
                "semantic_axes": ["character_content", "object_event_content"],
                "bbox": [0.04, 0.58, 0.42, 0.96],
                "strength": 0.32,
                "reason": "左下约30%固定为父母人物及其生活区域，人物占左下区域内部空间的1/2到2/3。",
            },
            {
                "region_id": "upper_right_child",
                "semantic_axes": ["character_content", "relationship_content"],
                "bbox": [0.22, 0.03, 0.92, 0.56],
                "strength": 0.45,
                "reason": "右上偏内侧约50%固定为子女人物及其生活区域，是最大生活空间；人物整体向左收，完整轮廓与右边框至少保留8%画布宽度的环境留白。",
            }
        ]
        if any(word in instruction for word in ("工作", "生病", "做饭", "散步", "物件", "痕迹")):
            regions.append(
                {
                    "region_id": "object_event_area",
                    "semantic_axes": ["object_event_content"],
                    "bbox": [0.50, 0.54, 0.88, 0.88],
                    "strength": 0.45,
                    "reason": "本次语义包含新的生活事件或物件锚点。",
                }
            )
        if any(word in instruction for word in ("疲惫", "姿态", "情绪", "年轻人")):
            regions.append(
                {
                    "region_id": "young_person_area",
                    "semantic_axes": ["character_content"],
                    "bbox": [0.54, 0.10, 0.92, 0.50],
                    "strength": 0.42,
                    "reason": "需要调整年轻人的姿态或情绪表达。",
                }
            )
        return regions

    @staticmethod
    def _relationship_layout_state(five_layer_plan: dict) -> dict:
        l2 = five_layer_plan.get("L2_relational_structure_layer")
        if not isinstance(l2, dict):
            return {}
        if isinstance(l2.get("layoutState"), dict):
            return l2["layoutState"]
        controls = l2.get("deterministicControls")
        if isinstance(controls, dict) and isinstance(controls.get("layoutState"), dict):
            return controls["layoutState"]
        return {}

    @staticmethod
    def _normalized_anchor(value, *, fallback: list[float]) -> list[float]:
        if not isinstance(value, list) or len(value) != 2:
            return fallback
        try:
            return [
                round(max(0.0, min(1.0, float(value[0]))), 3),
                round(max(0.0, min(1.0, float(value[1]))), 3),
            ]
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _normalized_ratio(value, *, fallback: float) -> float:
        try:
            return round(max(0.0, min(1.0, float(value))), 3)
        except (TypeError, ValueError):
            return fallback

    def _build_prompt(
        self,
        semantic_visual_instruction: str,
        previous_image_url: str | None,
        regions: list[dict],
        role_reference_images: dict[str, str | None] | None = None,
    ) -> str:
        region_text = "；".join(
            f"{region['region_id']}({region['reason']}，重绘强度 {region['strength']})"
            for region in regions
        )
        preserve = "延续上一张图的整体构图、人物身份、画风、色彩和镜头距离。" if previous_image_url else "建立稳定的 9:16 手机壁纸主构图。"
        reference_text = self._reference_prompt(role_reference_images)
        return f"""
生成一张竖版 9:16 手机动态壁纸图像，采用唯美手绘插画风格，适合家庭陪伴主题。

整体画风：
- 手绘绘本感、温柔水彩与水粉厚涂结合、可见纸张纹理和柔和笔触。
- 色彩清透、低饱和暖色、柔和漫反射光，画面像精致叙事插画，不像照片。
- 人物面部保留参考特征，但要转译成插画人物：柔和线条、自然表情、轻微手绘不完美感。
- 光效克制、朦胧、诗意，不要过度发光、过度锐化或商业海报式高光。
- 避免半写实 AI 肖像感、照片感、3D 渲染感、塑料皮肤、过度光滑、过度精修、网红写真感。

一致性要求：
{preserve}
如果提供上一张图作为 image2image/init image，请优先延续非重绘区域的构图和风格。
{reference_text}

固定产品构图：
- 严格三段式绝对比例：右上区域约占画面50%，中间关系路径约占画面20%，左下区域约占画面30%。
- 右上角固定为子女人物、生活空间或半透明想象场景，是最大生活空间。
- 右上人物占右上区域内部空间的1/2到2/3，脸部和表情清晰，同时保留充足的房间、窗、桌面、植物、天空或生活环境。
- 左下角固定为父母人物及其生活区域，左下人物占左下区域内部空间的1/2到2/3，脸部和情绪仍然可读，同时保留事件物件和生活环境。
- 人物相对尺度硬约束：最终画面里左下人物的可见头身高度和脸部面积约为右上人物的1/2，不要因为左下是前景就把左下人物画得比右上人物更大。
- 中间是从左下到右上的关系映射路径，主连接是可读的空间路径或结构路径，例如小路、桥、走廊、河岸、庭院路径、窗与窗之间的视线或生活空间边界。
- 双方人物姿态轻松，分别处在各自生活动作中；不要正脸直视镜头，视线应自然看向画面内事件、物件、路径、窗外、天空、远处、手中的东西或画面外侧。
- 双方通过中间关系路径形成异步陪伴感；手机或视频通话元素只在本次内容明确提到时出现。
- 光点可作为路径上的画面点缀、交互提示或局部反馈节点；画面仍以道路、桥、走廊、河岸、庭院小径等空间路径为主体。
- 最终呈现为一张完整壁纸画面，保留上方适合锁屏时间显示的干净空间。

本次单张图片内容描述：
{semantic_visual_instruction}

局部重绘区域规划：
{region_text}

画面规范：
- 画面中不加入文字、按钮、界面元素、logo、水印、聊天框或字幕。
- 整体保持温暖、明亮、安静、唯美、家庭陪伴感，像一张手绘叙事插画。
- 人物关系通过空间距离、生活场景和中间路径表达，光点只做细节点缀。
- 不使用手机通话、视频通话、挥手打招呼作为默认关系表达，除非本次内容明确提到。
- 避免只有光点、粒子或发光丝带构成连接；中间需要有实际空间路径结构。
- 避免人物正脸直视镜头、自拍感、证件照式肖像、摆拍肖像或盯着观众的眼神。
- 避免照片级真实皮肤、CG 质感、镜头炫光、过饱和、过锐利、AI 生成感强的人脸。
""".strip()

    def _soften_instruction(self, instruction: str) -> str:
        replacements = {
            "不要生成普通风景图": "画面重点放在人物关系和中间路径上",
            "不要把人物放到画面中央": "人物分别位于左下角和右上角区域",
            "不要让双方变成近距离团圆合照": "双方保持各自生活空间，通过路径相连",
            "不要出现文字、按钮、UI、logo、水印、聊天气泡或字幕": "画面保持纯插画壁纸质感，界面元素留白处理",
            "不要出现手机通话、视频通话、举着手机对话或挥手打招呼": "双方自然处在各自生活动作中",
            "不能替代路径本身": "作为路径上的细节点缀",
            "不能独自组成气泡式连接": "作为路径上的细节点缀",
        }
        softened = instruction
        for old, new in replacements.items():
            softened = softened.replace(old, new)
        return softened

    def _reference_urls(self, role_reference_images: dict[str, str | None] | None) -> dict[str, str]:
        if not role_reference_images:
            return {}
        return {
            role: url
            for role, url in role_reference_images.items()
            if role in {"elder", "child"} and isinstance(url, str) and url.strip()
        }

    def _reference_prompt(self, role_reference_images: dict[str, str | None] | None) -> str:
        refs = self._reference_urls(role_reference_images)
        if not refs:
            return "人物身份要求：没有提供角色参考图时，请保持人物自然、稳定、家庭陪伴感，并统一为手绘插画人物。"
        lines = [
            "角色参考图要求：",
            "- 先把上传的人物参考图统一转成当前壁纸的唯美手绘插画风格，再放入画面。",
            "- 参考图只用于人物身份、年龄感、发型、脸型、气质和表情特征；不复制参考图背景、服装文字或水印。",
            "- 人物应自然融入当前场景和光照，保持同一画风，避免照片拼贴感或半写实 AI 肖像感。",
        ]
        if refs.get("elder"):
            lines.append("- elder 参考图对应老人/父母一方，始终放在左下角。")
        if refs.get("child"):
            lines.append("- child 参考图对应子女/年轻一方，放在右上偏内侧；人物整体向左收，完整轮廓距右边框至少保留8%画布宽度的环境留白。")
        lines.append("- 如果参考人物和文本场景的服装不一致，可适度改成场景适配的衣着，但保留可识别的面部身份特征。")
        return "\n".join(lines)

    def _log(self, run_id: str | None, message: str) -> None:
        prefix = f"[agent-run:{run_id}]" if run_id else "[agent-run]"
        print(f"{prefix} {message}", flush=True)
