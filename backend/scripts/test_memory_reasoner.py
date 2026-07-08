from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.language_emotion_agent import LanguageEmotionAgent
from app.agents.memory_relation_agent import MemoryRelationAgent
from app.services.user_context import DEFAULT_PARENT_USER_ID, ensure_user_context


DEMO_RELATIONSHIP_ID = "memory-demo"


async def main() -> None:
    transcript = " ".join(sys.argv[1:]).strip()
    if not transcript:
        transcript = "今天社区合唱团又排练了，我发现自己比前几天更有精神，也想听听你最近怎么样。"

    ensure_user_context(DEFAULT_PARENT_USER_ID, DEMO_RELATIONSHIP_ID)
    run_id = "memory-reasoner-debug"
    language = await LanguageEmotionAgent().run_text(
        transcript,
        asr_raw={"provider": "memory_reasoner_debug", "source": "script"},
        run_id=run_id,
        user_id=DEFAULT_PARENT_USER_ID,
        relationship_id=DEMO_RELATIONSHIP_ID,
    )
    memory = await MemoryRelationAgent().run(
        language,
        relationship_id=DEMO_RELATIONSHIP_ID,
        run_id=run_id,
    )

    print("\n=== INPUT ===")
    print(transcript)
    print("\n=== SHORT TERM TABLE ===")
    print(json.dumps(language.short_term_table, ensure_ascii=False, indent=2))
    print("\n=== LONG TERM TABLE ===")
    print(json.dumps(memory.long_term_table, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
