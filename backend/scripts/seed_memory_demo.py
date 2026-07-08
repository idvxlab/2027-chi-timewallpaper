from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.models import InteractionLog, MessageLog, RelationshipState
from app.db.session import Base, SessionLocal, engine
from app.services.user_context import (
    DEFAULT_CHILD_USER_ID,
    DEFAULT_PARENT_USER_ID,
    ensure_user_context,
)


SEED_RUN_PREFIX = "seed-memory-demo"
SEED_INTERACTION_PREFIX = "seed-interaction-demo"
DEMO_RELATIONSHIP_ID = "memory-demo"


def cell(value: str, evidence: str, confidence: float = 0.9) -> dict:
    return {"value": value, "evidence": evidence, "confidence": confidence}


def short_table(
    *,
    event: str,
    scene: str,
    time: str,
    subject: str,
    objects: str,
    affect: str,
    intensity: str,
    ambiguity: str,
    intent: str,
    response: str,
    disclosure: str,
) -> dict:
    return {
        "tableName": "short_term_semantic_table",
        "agentRole": "Script Analyzer / 短期语义填表智能体",
        "designBoundary": "只提取文本中的具体事实、情绪和沟通意图；不生成视觉隐喻。",
        "A_situational_semantics": {
            "event": cell(event, "seeded historical short-term table"),
            "scene": cell(scene, "seeded historical short-term table"),
            "time": cell(time, "seeded historical short-term table"),
            "subject": cell(subject, "seeded historical short-term table"),
            "object": cell(objects, "seeded historical short-term table"),
        },
        "B_affective_semantics": {
            "momentary_affect": cell(affect, "seeded historical short-term table"),
            "affective_intensity": cell(intensity, "seeded historical short-term table"),
            "affective_ambiguity": cell(ambiguity, "seeded historical short-term table"),
        },
        "C_communicative_semantics": {
            "intent_type": cell(intent, "seeded historical short-term table"),
            "desired_response": cell(response, "seeded historical short-term table"),
            "disclosure_depth": cell(disclosure, "seeded historical short-term table"),
        },
    }


def seed_messages(now: datetime) -> list[MessageLog]:
    return [
        MessageLog(
            message_id="seed-msg-001",
            run_id=f"{SEED_RUN_PREFIX}-001",
            user_id=DEFAULT_PARENT_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            input_type="text",
            transcript="今天下午我去小区花园散步，桂花开了，闻起来很香，想给你看看。",
            short_term_table=short_table(
                event="小区花园散步时桂花开放，妈妈想分享香气和生活瞬间",
                scene="小区花园",
                time="下午",
                subject="妈妈",
                objects="桂花、花园小路",
                affect="愉悦",
                intensity="轻微",
                ambiguity="明确",
                intent="分享生活",
                response="轻触回应",
                disclosure="日常近况",
            ),
            emotion={"primary": "愉悦"},
            situation={"event": "桂花开放", "scene": "小区花园"},
            communication={"intent": "分享生活", "desired_response": "轻触回应"},
            raw={"source": "seed_memory_demo"},
            created_at=now - timedelta(days=12),
        ),
        MessageLog(
            message_id="seed-msg-002",
            run_id=f"{SEED_RUN_PREFIX}-002",
            user_id=DEFAULT_CHILD_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            input_type="text",
            transcript="这周作业和小组汇报有点赶，我可能晚点看消息。",
            short_term_table=short_table(
                event="女儿作业和小组汇报较忙，说明会晚点看消息",
                scene="学校或学习空间",
                time="这周",
                subject="女儿",
                objects="作业、小组汇报",
                affect="焦虑",
                intensity="明显",
                ambiguity="明确",
                intent="说明近况",
                response="理解等待",
                disclosure="学习压力",
            ),
            emotion={"primary": "焦虑"},
            situation={"event": "学习任务繁忙", "scene": "学校"},
            communication={"intent": "说明近况", "desired_response": "理解等待"},
            raw={"source": "seed_memory_demo"},
            created_at=now - timedelta(days=10),
        ),
        MessageLog(
            message_id="seed-msg-003",
            run_id=f"{SEED_RUN_PREFIX}-003",
            user_id=DEFAULT_PARENT_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            input_type="text",
            transcript="不用急，忙完再说。晚上我煮了粥，天气凉了你也记得吃点热的。",
            short_term_table=short_table(
                event="妈妈回应女儿忙碌，提醒天气转凉要吃热食",
                scene="家里厨房",
                time="晚上",
                subject="妈妈、女儿",
                objects="粥、热食",
                affect="平静",
                intensity="轻微",
                ambiguity="明确",
                intent="表达关心",
                response="看见即可",
                disclosure="照顾提醒",
            ),
            emotion={"primary": "平静"},
            situation={"event": "饮食和天气提醒", "scene": "家里"},
            communication={"intent": "表达关心", "desired_response": "看见即可"},
            raw={"source": "seed_memory_demo"},
            created_at=now - timedelta(days=9),
        ),
        MessageLog(
            message_id="seed-msg-004",
            run_id=f"{SEED_RUN_PREFIX}-004",
            user_id=DEFAULT_PARENT_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            input_type="text",
            transcript="这两天你没怎么说话，我有点惦记你，但不用马上回。",
            short_term_table=short_table(
                event="妈妈注意到女儿这两天少回消息，表达惦记但不催促",
                scene="家中",
                time="这两天",
                subject="妈妈、女儿",
                objects="消息记录",
                affect="思念",
                intensity="明显",
                ambiguity="明确",
                intent="表达思念",
                response="轻触回应",
                disclosure="关系关心",
            ),
            emotion={"primary": "思念"},
            situation={"event": "联系减少", "scene": "家中"},
            communication={"intent": "表达思念", "desired_response": "轻触回应"},
            raw={"source": "seed_memory_demo"},
            created_at=now - timedelta(days=6),
        ),
        MessageLog(
            message_id="seed-msg-005",
            run_id=f"{SEED_RUN_PREFIX}-005",
            user_id=DEFAULT_CHILD_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            input_type="text",
            transcript="我刚看到了，前几天太忙了。看到你发的桂花照片，感觉很安心。",
            short_term_table=short_table(
                event="女儿解释前几天忙碌，并回应妈妈的桂花照片让自己安心",
                scene="学习房间",
                time="刚刚",
                subject="女儿、妈妈",
                objects="桂花照片",
                affect="平静",
                intensity="明显",
                ambiguity="明确",
                intent="回应与安抚",
                response="继续聊天",
                disclosure="情绪回应",
            ),
            emotion={"primary": "平静"},
            situation={"event": "回应妈妈的生活分享", "scene": "学习房间"},
            communication={"intent": "回应与安抚", "desired_response": "继续聊天"},
            raw={"source": "seed_memory_demo"},
            created_at=now - timedelta(days=3),
        ),
        MessageLog(
            message_id="seed-msg-006",
            run_id=f"{SEED_RUN_PREFIX}-006",
            user_id=DEFAULT_PARENT_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            input_type="text",
            transcript="今天社区合唱团排练了老歌，大家唱得挺开心，我也觉得精神好多了。",
            short_term_table=short_table(
                event="妈妈参加社区合唱团排练老歌，感到开心和精神恢复",
                scene="社区活动室",
                time="今天",
                subject="妈妈、社区合唱团",
                objects="老歌、合唱团、活动室",
                affect="愉悦",
                intensity="明显",
                ambiguity="明确",
                intent="分享生活",
                response="轻松回应",
                disclosure="生活近况",
            ),
            emotion={"primary": "愉悦"},
            situation={"event": "社区合唱团排练", "scene": "社区活动室"},
            communication={"intent": "分享生活", "desired_response": "轻松回应"},
            raw={"source": "seed_memory_demo"},
            created_at=now - timedelta(days=1),
        ),
    ]


def seed_interactions(now: datetime) -> list[InteractionLog]:
    return [
        InteractionLog(
            interaction_id=f"{SEED_INTERACTION_PREFIX}-001",
            user_id=DEFAULT_CHILD_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            interaction_type="slide",
            target_id="history-card-osmanthus",
            payload={"direction": "up", "meaning": "查看妈妈之前的桂花分享"},
            created_at=now - timedelta(days=3, hours=2),
        ),
        InteractionLog(
            interaction_id=f"{SEED_INTERACTION_PREFIX}-002",
            user_id=DEFAULT_CHILD_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            interaction_type="flip",
            target_id="memory-card-osmanthus",
            payload={"meaning": "翻看卡片背后的文字记录"},
            created_at=now - timedelta(days=3, hours=1),
        ),
        InteractionLog(
            interaction_id=f"{SEED_INTERACTION_PREFIX}-003",
            user_id=DEFAULT_PARENT_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            interaction_type="press",
            target_id="child-room-area",
            payload={"meaning": "轻触查看女儿状态"},
            created_at=now - timedelta(days=2),
        ),
        InteractionLog(
            interaction_id=f"{SEED_INTERACTION_PREFIX}-004",
            user_id=DEFAULT_CHILD_USER_ID,
            relationship_id=DEMO_RELATIONSHIP_ID,
            interaction_type="hold",
            target_id="voice-input",
            payload={"meaning": "准备语音回应但未立即发送"},
            created_at=now - timedelta(hours=16),
        ),
    ]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_user_context(DEFAULT_PARENT_USER_ID, DEMO_RELATIONSHIP_ID)
    now = datetime.utcnow()

    with SessionLocal() as session:
        session.query(MessageLog).filter(MessageLog.run_id.like(f"{SEED_RUN_PREFIX}%")).delete(
            synchronize_session=False
        )
        session.query(InteractionLog).filter(
            InteractionLog.interaction_id.like(f"{SEED_INTERACTION_PREFIX}%")
        ).delete(synchronize_session=False)
        session.query(RelationshipState).filter(
            RelationshipState.relationship_id == DEMO_RELATIONSHIP_ID
        ).delete(synchronize_session=False)

        session.add_all(seed_messages(now))
        session.add_all(seed_interactions(now))
        session.commit()

    print(
        "Seeded memory demo: "
        f"relationship_id={DEMO_RELATIONSHIP_ID}, "
        f"parent={DEFAULT_PARENT_USER_ID}, child={DEFAULT_CHILD_USER_ID}, "
        "messages=6, interactions=4"
    )


if __name__ == "__main__":
    main()
