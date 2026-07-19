from __future__ import annotations

from app.core.config import settings
from app.schemas.agent import ImageGenerationResult, SemanticMappingResult
from app.services.layered_painter_tools import layered_painter_tools
from app.services.pipeline_service import generate_wallpaper_from_prompt


class ImageGenerationAgent:
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
    ) -> ImageGenerationResult:
        regions = self._plan_regions(semantic_mapping.semantic_visual_instruction)
        masks = self._plan_masks(regions)
        self._log(run_id, f"image region planning done regions={','.join(region['region_id'] for region in regions)}")
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
        layered = await self._try_layered_painter_tool(
            semantic_mapping=semantic_mapping,
            prompt=prompt,
            previous_image_url=previous_image_url,
            role_reference_images=role_reference_images,
            regions=regions,
            masks=masks,
            run_id=run_id,
        )
        if layered is not None:
            return layered

        image = await generate_wallpaper_from_prompt(
            prompt,
            run_id=run_id,
            reference_image_urls=list(reference_urls.values()),
        )
        wallpaper_url = image.get("imageUrl") or previous_image_url or ""

        return ImageGenerationResult(
            wallpaper_url=wallpaper_url,
            generation_mode="mask_image2image_mvp",
            changed_regions=[region["region_id"] for region in regions],
            asset_metadata={
                "parent_image": previous_image_url,
                "regions": regions,
                "masks": masks,
                "prompt": prompt,
                "role_references": reference_urls,
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
        masks: list[dict],
        run_id: str | None,
    ) -> ImageGenerationResult | None:
        if not settings.layered_image_tools_enabled:
            self._log(run_id, "painter tool selector: fallback=single_prompt_image_api reason=layered_tools_disabled")
            return None

        tool_name = self._select_layered_tool(previous_image_url, role_reference_images)
        if tool_name is None:
            self._log(run_id, "painter tool selector: fallback=single_prompt_image_api reason=missing_layered_inputs")
            return None

        self._log(run_id, f"painter tool selector: tool={tool_name}")
        try:
            current_role = self._current_role(semantic_mapping)
            if tool_name == "base_scene_then_first_voice_compose":
                refs = self._reference_urls(role_reference_images)
                elder_bytes = await layered_painter_tools.resolve_reference_bytes(refs.get("elder"))
                child_bytes = await layered_painter_tools.resolve_reference_bytes(refs.get("child"))
                base_result = await layered_painter_tools.generate_base_scene(
                    self_image_bytes=child_bytes,
                    partner_image_bytes=elder_bytes,
                )
                base_image_url = base_result.get("imageUrl") or ""
                if not base_image_url:
                    self._log(run_id, "painter base scene tool returned empty image; fallback=single_prompt_image_api")
                    return None
                self._log(run_id, f"painter base scene done imageUrl={base_image_url}")
                insert_result = await layered_painter_tools.first_voice_compose(
                    base_image_url=base_image_url,
                    younger_image_bytes=child_bytes,
                    elder_image_bytes=elder_bytes,
                    transcript=self._layered_tool_message(semantic_mapping),
                    current_role=current_role,
                )
                result = {
                    "imageUrl": insert_result.get("imageUrl") or base_image_url,
                    "raw": {
                        "baseScene": base_result.get("raw"),
                        "insertCharacters": insert_result.get("raw"),
                    },
                }
            elif tool_name == "first_voice_compose":
                refs = self._reference_urls(role_reference_images)
                result = await layered_painter_tools.first_voice_compose(
                    base_image_url=previous_image_url or "",
                    younger_image_bytes=await layered_painter_tools.resolve_reference_bytes(refs.get("child")),
                    elder_image_bytes=await layered_painter_tools.resolve_reference_bytes(refs.get("elder")),
                    transcript=self._layered_tool_message(semantic_mapping),
                    current_role=current_role,
                )
            elif tool_name == "update_current_side":
                result = await layered_painter_tools.update_current_side(
                    base_image_url=previous_image_url or "",
                    transcript=self._layered_tool_message(semantic_mapping),
                    current_side="left_bottom",
                )
            else:
                return None
        except Exception as exc:
            self._log(run_id, f"painter layered tool failed tool={tool_name} error={exc}; fallback=single_prompt_image_api")
            return None

        wallpaper_url = result.get("imageUrl") or previous_image_url or ""
        if not wallpaper_url:
            self._log(run_id, f"painter layered tool returned empty image tool={tool_name}; fallback=single_prompt_image_api")
            return None

        return ImageGenerationResult(
            wallpaper_url=wallpaper_url,
            generation_mode=f"layered_painter_tool:{tool_name}",
            changed_regions=[region["region_id"] for region in regions],
            asset_metadata={
                "parent_image": previous_image_url,
                "regions": regions,
                "masks": masks,
                "prompt": prompt,
                "layered_tool": tool_name,
                "role_references": self._reference_urls(role_reference_images),
                "raw": result.get("raw"),
                "quality": {
                    "mode": "layered_tool_with_prompt_fallback",
                    "designer_output_only": True,
                    "painter_owns_tool_selection": True,
                },
            },
        )

    def _select_layered_tool(
        self,
        previous_image_url: str | None,
        role_reference_images: dict[str, str | None] | None,
    ) -> str | None:
        refs = self._reference_urls(role_reference_images)
        if not previous_image_url and refs.get("elder") and refs.get("child"):
            return "base_scene_then_first_voice_compose"
        if previous_image_url and refs.get("elder") and refs.get("child"):
            return "first_voice_compose"
        if previous_image_url:
            return "update_current_side"
        return None

    def _current_role(self, semantic_mapping: SemanticMappingResult) -> str:
        scaffold = semantic_mapping.cognitive_scaffold or {}
        role = str(scaffold.get("viewerRole") or scaffold.get("viewer_role") or "parent").lower()
        if role in {"child", "daughter", "son"}:
            return "child"
        return "parent"

    def _layered_tool_message(self, semantic_mapping: SemanticMappingResult) -> str:
        plan = semantic_mapping.cognitive_scaffold or {}
        five_layers = plan.get("fiveLayerPlan") or {}
        layer_lines = []
        for key, layer in five_layers.items():
            if not isinstance(layer, dict):
                continue
            content = layer.get("designContent")
            if content:
                layer_lines.append(f"{key}: {content}")
        body = "\n".join(layer_lines)
        if not body:
            return semantic_mapping.semantic_visual_instruction
        return (
            f"{semantic_mapping.semantic_visual_instruction}\n\n"
            "五层视觉设计：\n"
            f"{body}"
        )

    def _plan_regions(self, instruction: str) -> list[dict]:
        regions = [
            {
                "region_id": "relation_path",
                "semantic_axes": ["relationship_content", "interaction_content"],
                "bbox": [0.16, 0.34, 0.84, 0.80],
                "strength": 0.35,
                "reason": "中间约20%区域必须表达从左下到右上的真实空间关系路径。",
            },
            {
                "region_id": "left_bottom_current_actor",
                "semantic_axes": ["character_content", "object_event_content"],
                "bbox": [0.04, 0.58, 0.42, 0.96],
                "strength": 0.32,
                "reason": "左下约30%固定为当前端人物和本次事件锚点，人物占左下区域内部空间的1/2到2/3。",
            },
            {
                "region_id": "upper_right_other_side",
                "semantic_axes": ["character_content", "relationship_content"],
                "bbox": [0.26, 0.03, 0.98, 0.56],
                "strength": 0.45,
                "reason": "右上约50%固定为另一方人物或生活空间，是最大生活空间，人物占该区域内部空间的1/2到2/3。",
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

    def _plan_masks(self, regions: list[dict]) -> list[dict]:
        return [
            {
                "region_id": region["region_id"],
                "mask_type": "normalized_bbox",
                "bbox": region["bbox"],
                "blur": 12,
                "dilate": 24,
            }
            for region in regions
        ]

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
- 右上角是另一方人物、生活空间或半透明想象场景，是最大生活空间。
- 右上人物占右上区域内部空间的1/2到2/3，脸部和表情清晰，同时保留充足的房间、窗、桌面、植物、天空或生活环境。
- 左下角是当前端人物和本次事件现场，左下人物占左下区域内部空间的1/2到2/3，脸部和情绪仍然可读，同时保留事件物件和生活环境。
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
            lines.append("- elder 参考图对应老人/父母一方；老人端时放在左下角，子女端时放在右上角。")
        if refs.get("child"):
            lines.append("- child 参考图对应子女/年轻一方；老人端时放在右上角，子女端时放在左下角。")
        lines.append("- 如果参考人物和文本场景的服装不一致，可适度改成场景适配的衣着，但保留可识别的面部身份特征。")
        return "\n".join(lines)

    def _log(self, run_id: str | None, message: str) -> None:
        prefix = f"[agent-run:{run_id}]" if run_id else "[agent-run]"
        print(f"{prefix} {message}", flush=True)
