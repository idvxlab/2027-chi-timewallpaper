from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from time import perf_counter
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.agents.chatbot_agent import ChatBotAgent, ChatBotAudioContext
from app.agents.image_generation_agent import ImageGenerationAgent
from app.agents.language_emotion_agent import LanguageEmotionAgent
from app.agents.memory_relation_agent import MemoryRelationAgent
from app.agents.semantic_mapping_agent import SemanticMappingAgent
from app.db.models import AgentRunLog, AsrLog, MessageLog, RelationshipState, WallpaperLog
from app.db.session import Base, SessionLocal, engine
from app.schemas.agent import AgentRunResult, AgentStep, BaseSceneOut, ComfortReplyOut, MemoryObjectsOut
from app.services.comfort_reply_service import ComfortReplyService
from app.services.memory_object_service import MemoryObjectService
from app.services.user_context import normalize_user_context


class MultiAgentOrchestrator:
    def __init__(self) -> None:
        self.chatbot_agent = ChatBotAgent()
        self.language_emotion_agent = LanguageEmotionAgent()
        self.memory_relation_agent = MemoryRelationAgent()
        self.semantic_mapping_agent = SemanticMappingAgent()
        self.image_generation_agent = ImageGenerationAgent()
        self.comfort_reply_service = ComfortReplyService()
        self.memory_object_service = MemoryObjectService()

    async def run_base_scene(self) -> BaseSceneOut:
        run_id = uuid.uuid4().hex
        self._log(run_id, "run started input=base_scene")
        t0 = perf_counter()
        self._log(run_id, "step image_generation started")
        image = await self.image_generation_agent.run_base_scene(run_id=run_id)
        status = "completed" if image.wallpaper_url else "failed"
        self._log(
            run_id,
            f"step image_generation done elapsed={perf_counter() - t0:.2f}s imageUrl={image.wallpaper_url or '(empty)'}",
        )
        self._log(run_id, f"run done status={status}")
        return BaseSceneOut(
            status=status,
            image_url=image.wallpaper_url,
            generation_mode=image.generation_mode,
            raw=image.asset_metadata,
        )

    async def run_audio(
        self,
        audio: bytes,
        content_type: str,
        filename: str,
        previous_image_url: Optional[str] = None,
        role_reference_images: Optional[dict[str, str | None]] = None,
        user_id: Optional[str] = None,
        relationship_id: Optional[str] = None,
        generation_stage: Optional[str] = None,
        speaker_role: Optional[str] = None,
        analyze_only: bool = False,
        prepared_audio_context: Optional[ChatBotAudioContext] = None,
        run_id: Optional[str] = None,
        event_seq: Optional[int] = None,
    ) -> AgentRunResult:
        user_id, relationship_id = normalize_user_context(user_id, relationship_id)
        run_id = run_id or uuid.uuid4().hex
        created_at = datetime.utcnow()
        self._log(run_id, f"run started input=audio filename={filename} bytes={len(audio)} user={user_id} relationship={relationship_id}")
        steps = [
            AgentStep(name="chat_bot", status="running"),
            AgentStep(name="language_emotion"),
            AgentStep(name="memory_relation"),
            AgentStep(name="semantic_mapping"),
            AgentStep(name="image_generation"),
        ]
        steps[2].status = "running"
        memory_task = asyncio.create_task(
            self.memory_relation_agent.run_history(
                relationship_id=relationship_id,
                run_id=run_id,
            )
        )
        self._log(run_id, "step memory_relation started in parallel from database history")

        t0 = perf_counter()
        self._log(run_id, "step chatbot audio preparation started")
        audio_context = prepared_audio_context
        if audio_context is None:
            audio_context = await self.chatbot_agent.prepare_audio(
                audio,
                content_type=content_type,
                filename=filename,
                run_id=run_id,
            )
        else:
            self._log(run_id, "using ChatBot streaming audio context")
        self._log(
            run_id,
            f"step chatbot audio preparation done elapsed={perf_counter() - t0:.2f}s transcript={audio_context.transcript[:48]}",
        )

        steps[1].status = "running"
        self._log(run_id, "steps chatbot reply and language_emotion started in parallel")
        parallel_t0 = perf_counter()
        chat_task = asyncio.create_task(
            self.chatbot_agent.generate_reply(
                audio_context.transcript,
                voice_affect=audio_context.voice_affect,
                user_id=user_id,
                relationship_id=relationship_id,
                run_id=run_id,
                audio_context=audio_context,
            )
        )
        language_task = asyncio.create_task(
            self.language_emotion_agent.run_text(
                audio_context.transcript,
                asr_raw={
                    "asr": audio_context.asr_raw,
                    "audio_understanding": audio_context.audio_affect_raw,
                },
                voice_affect=audio_context.voice_affect,
                run_id=run_id,
                user_id=user_id,
                relationship_id=relationship_id,
            )
        )
        chat_bot, language, memory = await asyncio.gather(chat_task, language_task, memory_task)
        language = language.model_copy(update={"reply": chat_bot.reply})
        memory = self.memory_relation_agent.attach_current_short(memory, language, user_id=user_id)
        steps[0].status = "done"
        steps[1].status = "done"
        steps[2].status = "done"
        self._log(
            run_id,
            f"steps chatbot reply, language_emotion and memory_relation joined elapsed={perf_counter() - parallel_t0:.2f}s",
        )
        self._log_script_analyzer_internal(run_id, language.raw if isinstance(language.raw, dict) else {})
        self._log_initial_short_term_table(run_id, language.raw.get("initial_short_term_table") if isinstance(language.raw, dict) else None)
        self._log_script_reflection(run_id, language.raw.get("reflection") if isinstance(language.raw, dict) else None)
        self._log_short_term_table(run_id, language.short_term_table)
        await self._save_asr_log(language.transcript, language.raw)
        message_id = await self._save_message_log(run_id, user_id, relationship_id, "audio", language)

        steps[3].status = "running"
        self._log(run_id, f"step memory_relation done trend={memory.relational.get('relationship_trend')}")
        self._log_long_term_table(run_id, memory.long_term_table)
        state_saved = await self._save_relationship_state(
            relationship_id,
            memory,
            event_seq=event_seq,
        )
        if not state_saved:
            self._log(
                run_id,
                f"relationship state skipped stale event_seq={event_seq}",
            )

        t0 = perf_counter()
        self._log(run_id, "step semantic_mapping started")
        semantic = await self.semantic_mapping_agent.run(
            language,
            memory,
            run_id=run_id,
            relationship_id=relationship_id,
            user_id=user_id,
        )
        steps[3].status = "done"
        steps[4].status = "running"
        self._log(
            run_id,
            f"step semantic_mapping done elapsed={perf_counter() - t0:.2f}s hits={len(semantic.mapping_trace)} composer={semantic.cognitive_scaffold.get('composer')}",
        )
        self._log_designer_output(run_id, semantic)

        if analyze_only:
            steps[4].status = "pending"
            result = AgentRunResult(
                run_id=run_id,
                status="analyzed",
                user_id=user_id,
                relationship_id=relationship_id,
                steps=steps,
                chat_bot=chat_bot,
                language_emotion=language,
                memory_relation=memory,
                semantic_mapping=semantic,
                created_at=created_at,
                updated_at=datetime.utcnow(),
            )
            await self._save_run(result)
            self._log(
                run_id,
                "analysis done; view rendering delegated to versioned render tasks",
            )
            return result

        t0 = perf_counter()
        self._log(run_id, "step image_generation started")
        image = await self.image_generation_agent.run(
            semantic,
            previous_image_url=previous_image_url,
            role_reference_images=role_reference_images,
            run_id=run_id,
            generation_stage=generation_stage,
            speaker_role=speaker_role,
        )
        await self._save_wallpaper_log(
            run_id,
            message_id,
            relationship_id,
            semantic,
            image,
        )
        steps[4].status = "done"
        self._log(
            run_id,
            f"step image_generation done elapsed={perf_counter() - t0:.2f}s imageUrl={image.wallpaper_url or '(empty)'} regions={','.join(image.changed_regions)}",
        )

        result = AgentRunResult(
            run_id=run_id,
            status="done",
            user_id=user_id,
            relationship_id=relationship_id,
            steps=steps,
            chat_bot=chat_bot,
            language_emotion=language,
            memory_relation=memory,
            semantic_mapping=semantic,
            image_generation=image,
            created_at=created_at,
            updated_at=datetime.utcnow(),
        )
        await self._save_run(result)
        self._log(run_id, f"run done total_elapsed={(result.updated_at - result.created_at).total_seconds():.2f}s")
        return result

    async def run_text(
        self,
        transcript: str,
        previous_image_url: Optional[str] = None,
        role_reference_images: Optional[dict[str, str | None]] = None,
        user_id: Optional[str] = None,
        relationship_id: Optional[str] = None,
    ) -> AgentRunResult:
        user_id, relationship_id = normalize_user_context(user_id, relationship_id)
        run_id = uuid.uuid4().hex
        created_at = datetime.utcnow()
        self._log(run_id, f"run started input=text chars={len(transcript)} user={user_id} relationship={relationship_id}")
        steps = [
            AgentStep(name="chat_bot", status="running"),
            AgentStep(name="language_emotion", status="running"),
            AgentStep(name="memory_relation"),
            AgentStep(name="semantic_mapping"),
            AgentStep(name="image_generation"),
        ]
        steps[2].status = "running"
        memory_task = asyncio.create_task(
            self.memory_relation_agent.run_history(
                relationship_id=relationship_id,
                run_id=run_id,
            )
        )
        self._log(run_id, "step memory_relation started in parallel from database history")

        t0 = perf_counter()
        self._log(run_id, "steps chatbot reply and language_emotion started in parallel")
        chat_task = asyncio.create_task(
            self.chatbot_agent.generate_reply(
                transcript,
                user_id=user_id,
                relationship_id=relationship_id,
                run_id=run_id,
            )
        )
        language_task = asyncio.create_task(
            self.language_emotion_agent.run_text(
                transcript,
                asr_raw={"provider": "text_debug", "source": "manual_transcript"},
                run_id=run_id,
                user_id=user_id,
                relationship_id=relationship_id,
            )
        )
        chat_bot, language, memory = await asyncio.gather(chat_task, language_task, memory_task)
        language = language.model_copy(update={"reply": chat_bot.reply})
        memory = self.memory_relation_agent.attach_current_short(memory, language, user_id=user_id)
        steps[0].status = "done"
        steps[1].status = "done"
        steps[2].status = "done"
        self._log(run_id, f"steps chatbot reply, language_emotion and memory_relation joined elapsed={perf_counter() - t0:.2f}s transcript={language.transcript[:48]}")
        self._log_script_analyzer_internal(run_id, language.raw if isinstance(language.raw, dict) else {})
        self._log_initial_short_term_table(run_id, language.raw.get("initial_short_term_table") if isinstance(language.raw, dict) else None)
        self._log_script_reflection(run_id, language.raw.get("reflection") if isinstance(language.raw, dict) else None)
        self._log_short_term_table(run_id, language.short_term_table)
        await self._save_asr_log(language.transcript, language.raw)
        message_id = await self._save_message_log(run_id, user_id, relationship_id, "text", language)

        steps[3].status = "running"
        self._log(run_id, f"step memory_relation done trend={memory.relational.get('relationship_trend')}")
        self._log_long_term_table(run_id, memory.long_term_table)
        await self._save_relationship_state(relationship_id, memory)

        t0 = perf_counter()
        self._log(run_id, "step semantic_mapping started")
        semantic = await self.semantic_mapping_agent.run(
            language,
            memory,
            run_id=run_id,
            relationship_id=relationship_id,
            user_id=user_id,
        )
        steps[3].status = "done"
        steps[4].status = "running"
        self._log(
            run_id,
            f"step semantic_mapping done elapsed={perf_counter() - t0:.2f}s hits={len(semantic.mapping_trace)} composer={semantic.cognitive_scaffold.get('composer')}",
        )
        self._log_designer_output(run_id, semantic)

        t0 = perf_counter()
        self._log(run_id, "step image_generation started")
        image = await self.image_generation_agent.run(
            semantic,
            previous_image_url=previous_image_url,
            role_reference_images=role_reference_images,
            run_id=run_id,
        )
        steps[4].status = "done"
        await self._save_wallpaper_log(run_id, message_id, relationship_id, semantic, image)
        self._log(
            run_id,
            f"step image_generation done elapsed={perf_counter() - t0:.2f}s imageUrl={image.wallpaper_url or '(empty)'} regions={','.join(image.changed_regions)}",
        )

        result = AgentRunResult(
            run_id=run_id,
            status="done",
            user_id=user_id,
            relationship_id=relationship_id,
            steps=steps,
            chat_bot=chat_bot,
            language_emotion=language,
            memory_relation=memory,
            semantic_mapping=semantic,
            image_generation=image,
            created_at=created_at,
            updated_at=datetime.utcnow(),
        )
        await self._save_run(result)
        self._log(run_id, f"run done total_elapsed={(result.updated_at - result.created_at).total_seconds():.2f}s")
        return result

    async def run_comfort_reply(
        self,
        transcript: str,
        user_id: Optional[str] = None,
        relationship_id: Optional[str] = None,
        persist: bool = False,
    ) -> ComfortReplyOut:
        user_id, relationship_id = normalize_user_context(user_id, relationship_id)
        run_id = uuid.uuid4().hex
        self._log(run_id, f"comfort reply started chars={len(transcript)} user={user_id} relationship={relationship_id} persist={persist}")
        memory_task = asyncio.create_task(
            self.memory_relation_agent.run_history(
                relationship_id=relationship_id,
                run_id=run_id,
            )
        )
        self._log(run_id, "comfort memory_relation started in parallel from database history")

        t0 = perf_counter()
        language_task = asyncio.create_task(
            self.language_emotion_agent.run_text(
                transcript,
                asr_raw={"provider": "comfort_reply_debug", "source": "manual_transcript"},
                run_id=run_id,
                user_id=user_id,
                relationship_id=relationship_id,
            )
        )
        language, memory = await asyncio.gather(language_task, memory_task)
        memory = self.memory_relation_agent.attach_current_short(memory, language, user_id=user_id)
        self._log(run_id, f"comfort language_emotion and memory_relation joined elapsed={perf_counter() - t0:.2f}s transcript={language.transcript[:48]}")
        self._log_script_analyzer_internal(run_id, language.raw if isinstance(language.raw, dict) else {})
        self._log_initial_short_term_table(run_id, language.raw.get("initial_short_term_table") if isinstance(language.raw, dict) else None)
        self._log_script_reflection(run_id, language.raw.get("reflection") if isinstance(language.raw, dict) else None)
        self._log_short_term_table(run_id, language.short_term_table)

        message_id = ""
        if persist:
            await self._save_asr_log(language.transcript, language.raw)
            message_id = await self._save_message_log(run_id, user_id, relationship_id, "comfort_text", language)

        self._log(run_id, f"comfort memory_relation done trend={memory.relational.get('relationship_trend')}")
        self._log_long_term_table(run_id, memory.long_term_table)
        if persist:
            await self._save_relationship_state(relationship_id, memory)

        comfort = self.comfort_reply_service.build(language, memory, user_id)
        self._log_comfort_reply(run_id, comfort)

        result = ComfortReplyOut(
            run_id=run_id,
            status="done",
            user_id=user_id,
            relationship_id=relationship_id,
            comfort_reply=comfort,
        )
        if persist:
            await self._save_comfort_reply_run(result, message_id)
        self._log(run_id, "comfort reply done")
        return result

    async def run_memory_objects(
        self,
        user_id: Optional[str] = None,
        relationship_id: Optional[str] = None,
        limit: int = 8,
        threshold: int = 3,
        generate_missing: bool = True,
    ) -> MemoryObjectsOut:
        user_id, relationship_id = normalize_user_context(user_id, relationship_id)
        run_id = uuid.uuid4().hex
        self._log(
            run_id,
            (
                "memory objects started "
                f"user={user_id} relationship={relationship_id} "
                f"limit={limit} threshold={threshold} generate_missing={generate_missing}"
            ),
        )
        self._ensure_db()
        items = await self.memory_object_service.list_assets(
            relationship_id=relationship_id,
            threshold=threshold,
            limit=limit,
            generate_missing=generate_missing,
            run_id=run_id,
        )
        ready_count = sum(1 for item in items if item.ready)
        triggered = [item.name for item in items if item.mention_count >= threshold]
        self._log(
            run_id,
            f"memory objects done collected={len(items)} triggered={len(triggered)} ready={ready_count} items={','.join(triggered[:8])}",
        )

        result = MemoryObjectsOut(
            run_id=run_id,
            status="done",
            user_id=user_id,
            relationship_id=relationship_id,
            threshold=threshold,
            items=items,
            raw={"itemsCount": len(items), "readyCount": ready_count, "triggered": triggered},
        )
        await self._save_memory_objects_run(result)
        return result

    async def get_run(self, run_id: str) -> AgentRunResult:
        with SessionLocal() as session:
            row = session.query(AgentRunLog).filter(AgentRunLog.run_id == run_id).one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Agent run not found")
            payload = row.payload
        return AgentRunResult.model_validate(payload)

    async def _save_asr_log(self, transcript: str, raw: dict) -> None:
        self._ensure_db()
        with SessionLocal() as session:
            session.add(AsrLog(transcript=transcript[:1024], raw=raw))
            session.commit()

    async def _save_comfort_reply_run(self, result: ComfortReplyOut, message_id: str) -> None:
        self._ensure_db()
        now = datetime.utcnow()
        payload = result.model_dump(mode="json", by_alias=True)
        payload["messageId"] = message_id
        with SessionLocal() as session:
            session.add(
                AgentRunLog(
                    run_id=result.run_id,
                    status=result.status,
                    payload=payload,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

    async def _save_memory_objects_run(self, result: MemoryObjectsOut) -> None:
        self._ensure_db()
        now = datetime.utcnow()
        with SessionLocal() as session:
            session.add(
                AgentRunLog(
                    run_id=result.run_id,
                    status=result.status,
                    payload=result.model_dump(mode="json", by_alias=True),
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

    async def _save_message_log(self, run_id: str, user_id: str, relationship_id: str, input_type: str, language) -> str:
        self._ensure_db()
        message_id = uuid.uuid4().hex
        with SessionLocal() as session:
            session.add(
                MessageLog(
                    message_id=message_id,
                    run_id=run_id,
                    user_id=user_id,
                    relationship_id=relationship_id,
                    input_type=input_type,
                    transcript=language.transcript[:4096],
                    short_term_table=language.short_term_table,
                    emotion=language.emotion,
                    situation=language.situation,
                    communication=language.communication,
                    raw=language.raw,
                )
            )
            session.commit()
        return message_id

    async def _save_relationship_state(
        self,
        relationship_id: str,
        memory,
        *,
        event_seq: int | None = None,
    ) -> bool:
        self._ensure_db()
        now = datetime.utcnow()
        with SessionLocal() as session:
            row = (
                session.query(RelationshipState)
                .filter(RelationshipState.relationship_id == relationship_id)
                .one_or_none()
            )
            if row is None:
                session.add(
                    RelationshipState(
                        relationship_id=relationship_id,
                        long_term_table=memory.long_term_table,
                        longitudinal=memory.longitudinal,
                        relational=memory.relational,
                        history_summary=memory.history_summary,
                        version=1,
                        last_applied_event_seq=event_seq or 0,
                        updated_at=now,
                    )
                )
                try:
                    session.commit()
                    return True
                except IntegrityError:
                    session.rollback()
            else:
                if event_seq is not None and event_seq <= row.last_applied_event_seq:
                    return False
                expected_version = row.version
                filters = [
                    RelationshipState.relationship_id == relationship_id,
                    RelationshipState.version == expected_version,
                ]
                if event_seq is not None:
                    filters.append(
                        RelationshipState.last_applied_event_seq < event_seq
                    )
                updated = (
                    session.query(RelationshipState)
                    .filter(*filters)
                    .update(
                        {
                            RelationshipState.long_term_table: memory.long_term_table,
                            RelationshipState.longitudinal: memory.longitudinal,
                            RelationshipState.relational: memory.relational,
                            RelationshipState.history_summary: memory.history_summary,
                            RelationshipState.version: expected_version + 1,
                            RelationshipState.last_applied_event_seq: (
                                event_seq
                                if event_seq is not None
                                else row.last_applied_event_seq
                            ),
                            RelationshipState.updated_at: now,
                        },
                        synchronize_session=False,
                    )
                )
                session.commit()
                return updated == 1

        # A concurrent request may have inserted the row after our first read.
        with SessionLocal() as session:
            row = (
                session.query(RelationshipState)
                .filter(RelationshipState.relationship_id == relationship_id)
                .one()
            )
            if event_seq is not None and event_seq <= row.last_applied_event_seq:
                return False
            expected_version = row.version
            updated = (
                session.query(RelationshipState)
                .filter(
                    RelationshipState.relationship_id == relationship_id,
                    RelationshipState.version == expected_version,
                )
                .update(
                    {
                        RelationshipState.long_term_table: memory.long_term_table,
                        RelationshipState.longitudinal: memory.longitudinal,
                        RelationshipState.relational: memory.relational,
                        RelationshipState.history_summary: memory.history_summary,
                        RelationshipState.version: expected_version + 1,
                        RelationshipState.last_applied_event_seq: (
                            event_seq
                            if event_seq is not None
                            else row.last_applied_event_seq
                        ),
                        RelationshipState.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return updated == 1

    async def _save_wallpaper_log(self, run_id: str, message_id: str, relationship_id: str, semantic, image) -> None:
        self._ensure_db()
        with SessionLocal() as session:
            session.add(
                WallpaperLog(
                    wallpaper_id=uuid.uuid4().hex,
                    run_id=run_id,
                    message_id=message_id,
                    relationship_id=relationship_id,
                    prompt=semantic.semantic_visual_instruction[:4096],
                    image_url=image.wallpaper_url or "",
                    five_layer_plan=semantic.cognitive_scaffold.get("fiveLayerPlan", {}),
                    asset_metadata=image.asset_metadata,
                )
            )
            session.commit()

    async def _save_run(self, result: AgentRunResult) -> None:
        self._ensure_db()
        payload = result.model_dump(mode="json", by_alias=True)
        with SessionLocal() as session:
            session.add(
                AgentRunLog(
                    run_id=result.run_id,
                    status=result.status,
                    payload=payload,
                    created_at=result.created_at,
                    updated_at=result.updated_at,
                )
            )
            session.commit()

    def _ensure_db(self) -> None:
        Base.metadata.create_all(bind=engine)

    def _log_script_analyzer_internal(self, run_id: str, raw: dict) -> None:
        if not isinstance(raw, dict):
            return
        gist = raw.get("gist_layer") or {}
        extractor = raw.get("semantic_extractor") or {}
        question_answers = raw.get("question_answers") or {}
        if not (gist or extractor or question_answers):
            return
        lines = [
            "",
            "===== Script Analyzer 内部理解流程 START =====",
            "loop: Parallel(Gist Layer, Semantic Extractor) -> Question-Based Table Filler",
        ]
        if gist:
            lines.append("[Gist Layer]")
            for key in ("one_sentence_gist", "speaker_state", "life_rhythm", "communication_motive", "confidence"):
                if key in gist:
                    lines.append(f"  - {key}: {self._format_value(gist.get(key))}")
        if extractor:
            lines.append("[Semantic Extractor]")
            for key in ("events", "scenes", "times", "subjects", "objects", "affect_cues", "communication_cues"):
                value = extractor.get(key)
                if value:
                    lines.append(f"  - {key}: {self._truncate(self._format_value(value), 180)}")
        if question_answers:
            lines.append("[Question-Based Filler]")
            for key in (
                "event",
                "scene",
                "time",
                "subject",
                "object",
                "momentary_affect",
                "affective_intensity",
                "affective_ambiguity",
                "intent_type",
                "desired_response",
                "disclosure_depth",
            ):
                cell = question_answers.get(key)
                if isinstance(cell, dict):
                    lines.append(
                        f"  - {key}: answer={self._truncate(self._format_value(cell.get('answer')), 90)} "
                        f"| value={self._format_value(cell.get('value'))} | source={cell.get('source', '')}"
                    )
        lines.append("===== Script Analyzer 内部理解流程 END =====")
        self._log(run_id, "\n".join(lines))

    def _log_initial_short_term_table(self, run_id: str, table: dict | None) -> None:
        if not isinstance(table, dict):
            return
        lines = [
            "",
            "===== Script Analyzer 初始短期表 START =====",
            f"tableName: {table.get('tableName', '')}",
            f"agentRole: {table.get('agentRole', '')}",
            f"boundary: {table.get('designBoundary', '')}",
        ]
        for section_key, section_title in [
            ("A_situational_semantics", "A 情境事实语义"),
            ("B_affective_semantics", "B 情绪状态语义"),
            ("C_communicative_semantics", "C 沟通意图语义"),
        ]:
            lines.append(f"[{section_title}]")
            lines.extend(self._format_table_section(table.get(section_key, {})))
        lines.append("===== Script Analyzer 初始短期表 END =====")
        self._log(run_id, "\n".join(lines))

    def _log_short_term_table(self, run_id: str, table: dict) -> None:
        lines = [
            "",
            "===== Script Analyzer 最终短期表填写情况 START =====",
            f"tableName: {table.get('tableName', '')}",
            f"agentRole: {table.get('agentRole', '')}",
            f"boundary: {table.get('designBoundary', '')}",
        ]
        for section_key, section_title in [
            ("A_situational_semantics", "A 情境事实语义"),
            ("B_affective_semantics", "B 情绪状态语义"),
            ("C_communicative_semantics", "C 沟通意图语义"),
        ]:
            lines.append(f"[{section_title}]")
            lines.extend(self._format_table_section(table.get(section_key, {})))
        lines.append("===== Script Analyzer 最终短期表填写情况 END =====")
        self._log(run_id, "\n".join(lines))

    def _log_script_reflection(self, run_id: str, reflection: dict | None) -> None:
        if not isinstance(reflection, dict):
            return
        issues = reflection.get("issues") or []
        revisions = reflection.get("revisions") or {}
        lines = [
            "",
            "===== Reflection Agent 短期表校验反馈 START =====",
            f"agentRole: {reflection.get('agentRole', '')}",
            "boundary: 只反馈并修正 Script Analyzer 的短期表；不生成视觉隐喻，不决定画面怎么画。",
            f"utteranceGist: {reflection.get('utterance_gist', '')}",
        ]
        if issues:
            lines.append("[issues]")
            for index, issue in enumerate(issues, start=1):
                lines.append(
                    "  "
                    f"{index}. field={issue.get('field_path', '')} | old={self._format_value(issue.get('old'))} "
                    f"| fix={self._format_value(issue.get('fix'))} | reason={self._truncate(str(issue.get('problem', '')), 120)}"
                )
        else:
            lines.append("[issues]")
            lines.append("  (no correction needed)")
        if revisions:
            lines.append("[revisions]")
            for section, fields in revisions.items():
                if not isinstance(fields, dict):
                    continue
                lines.append(f"  {section}")
                for field, cell in fields.items():
                    if isinstance(cell, dict):
                        lines.append(
                            f"    - {field}: value={self._format_value(cell.get('value'))} | confidence={cell.get('confidence', '')}"
                        )
        lines.append("===== Reflection Agent 短期表校验反馈 END =====")
        self._log(run_id, "\n".join(lines))

    def _log_long_term_table(self, run_id: str, table: dict) -> None:
        lines = [
            "",
            "===== History Reasoner 长期表填写情况 START =====",
            f"tableName: {table.get('tableName', '')}",
            f"agentRole: {table.get('agentRole', '')}",
            f"boundary: {table.get('designBoundary', '')}",
        ]
        for section_key, section_title in [
            ("D_longitudinal_state_semantics", "D 长期心理状态语义"),
            ("E_relational_semantics", "E 关系语义"),
        ]:
            lines.append(f"[{section_title}]")
            lines.extend(self._format_table_section(table.get(section_key, {})))
        lines.append("===== History Reasoner 长期表填写情况 END =====")
        self._log(run_id, "\n".join(lines))

    def _log_designer_output(self, run_id: str, semantic) -> None:
        scaffold = semantic.cognitive_scaffold or {}
        five_layer_plan = scaffold.get("fiveLayerPlan", {})
        lines = [
            "",
            "===== Designer 五层画面设计 START =====",
            f"agentRole: {scaffold.get('agentRole', '')}",
            f"inputBoundary: {scaffold.get('inputBoundary', '')}",
            f"visualProposition: {scaffold.get('visualProposition', '')}",
        ]
        visual_dials = scaffold.get("visualDials", {})
        if isinstance(visual_dials, dict) and visual_dials:
            lines.append("[Visual Dials 参数层]")
            source_values = visual_dials.get("sourceValues", {})
            if isinstance(source_values, dict):
                lines.append(f"  sourceValues: {self._truncate(self._format_value(source_values), 260)}")
            for group_key, group_title in [
                ("relationshipSpace", "关系空间"),
                ("affectiveAtmosphere", "情绪氛围"),
                ("livingMetaphor", "生活隐喻"),
                ("feedback", "交互反馈"),
                ("stability", "稳定性"),
            ]:
                group = visual_dials.get(group_key, {})
                if not isinstance(group, dict) or not group:
                    continue
                formatted = []
                for key, value in group.items():
                    if isinstance(value, dict) and "value" in value:
                        formatted.append(f"{key}={value.get('value')}({value.get('level')})")
                    else:
                        formatted.append(f"{key}={self._format_value(value)}")
                lines.append(f"  {group_title}: {', '.join(formatted)}")
            if visual_dials.get("promptCues"):
                lines.append(f"  promptCues: {self._truncate(str(visual_dials.get('promptCues')), 260)}")
        for layer_key, layer_title in [
            ("L1_environment_layer", "L1 环境层"),
            ("L2_relational_structure_layer", "L2 关系结构层"),
            ("L3_object_event_layer", "L3 物件/事件层"),
            ("L4_character_layer", "L4 人物层"),
            ("L5_motion_feedback_layer", "L5 动态反馈层"),
        ]:
            layer = five_layer_plan.get(layer_key, {})
            lines.append(f"[{layer_title}]")
            if layer.get("sourceFields"):
                lines.append(f"  sourceFields: {', '.join(layer.get('sourceFields', []))}")
            if layer.get("designQuestion"):
                lines.append(f"  designQuestion: {layer.get('designQuestion')}")
            design_content = layer.get("designContent") or []
            if design_content:
                lines.append("  designContent:")
                for index, item in enumerate(design_content, start=1):
                    lines.append(f"    {index}. {item}")
            else:
                lines.append("  designContent: (empty)")
        lines.extend(
            [
                "----- Designer 完整生图 Prompt START -----",
                semantic.semantic_visual_instruction,
                "----- Designer 完整生图 Prompt END -----",
                "===== Designer 五层画面设计 END =====",
            ]
        )
        self._log(run_id, "\n".join(lines))

    def _log_comfort_reply(self, run_id: str, comfort) -> None:
        lines = [
            "",
            "===== Comfort Reply 建议回复 START =====",
            f"text: {comfort.text}",
            f"tone: {comfort.tone}",
            f"strategy: {comfort.strategy}",
            f"source: {self._truncate(self._format_value(comfort.source), 360)}",
            "===== Comfort Reply 建议回复 END =====",
        ]
        self._log(run_id, "\n".join(lines))

    def _format_table_section(self, section: dict) -> list[str]:
        if not isinstance(section, dict) or not section:
            return ["  (empty)"]
        lines = []
        for field, cell in section.items():
            if not isinstance(cell, dict):
                lines.append(f"  - {field}: {self._format_value(cell)}")
                continue
            value = self._format_value(cell.get("value"))
            confidence = cell.get("confidence", "")
            evidence = self._truncate(self._format_value(cell.get("evidence")), limit=96)
            lines.append(f"  - {field}: value={value} | confidence={confidence} | evidence={evidence}")
        return lines

    def _format_value(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _truncate(self, value: str, limit: int = 120) -> str:
        value = value.replace("\n", " ").strip()
        if len(value) <= limit:
            return value
        return value[: limit - 1] + "…"

    def _log(self, run_id: str, message: str) -> None:
        print(f"[agent-run:{run_id}] {message}", flush=True)
