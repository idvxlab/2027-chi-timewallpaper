from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.services.semantic_graph import SemanticGraph, load_semantic_graph


class SemanticGraphRAG:
    def __init__(self, graph: SemanticGraph) -> None:
        self.graph = graph
        self.chunks = [self._build_chunk(item) for item in graph.rules]

    def retrieve(self, query: str, top_k: int = 12) -> list[dict[str, Any]]:
        terms = self._terms(query)
        scored = []
        for chunk in self.chunks:
            score = self._score(chunk, query, terms)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._hit(chunk, score) for score, chunk in scored[:top_k]]

    def _build_chunk(self, item: dict[str, Any]) -> dict[str, Any]:
        values = "、".join(item.get("values", []))
        aliases = "、".join(item.get("aliases", []))
        layers = "、".join(item.get("mapping_layers", item.get("layers", [])))
        metaphors = "；".join(
            metaphor.get("description", "")
            for metaphor in item.get("visual_metaphors", [])
            if metaphor.get("description")
        )
        must_include = "、".join(item.get("must_include", []))
        avoid = "、".join(item.get("avoid", []))
        content = (
            f"{item.get('category', '')}。"
            f"语义槽：{item.get('semantic_slot', item.get('semantic_variable', ''))}。"
            f"字段：{item.get('field', '')}。"
            f"内容类型：{item.get('content_type', '')}。"
            f"策略：{item.get('strategy', '')}。"
            f"可选取值：{values}。"
            f"别名：{aliases}。"
            f"映射层：{layers}。"
            f"规则作用：{item.get('role', '')}。"
            f"AIGC视觉映射：{item.get('aigc_visual_mapping', '')}。"
            f"视觉隐喻：{metaphors}。"
            f"必须包含：{must_include}。"
            f"避免：{avoid}。"
        )
        return {
            "id": item.get("id"),
            "field": item.get("field"),
            "category": item.get("category"),
            "semantic_slot": item.get("semantic_slot"),
            "semantic_variable": item.get("semantic_variable") or item.get("semantic_slot"),
            "content_type": item.get("content_type", ""),
            "strategy": item.get("strategy", ""),
            "values": item.get("values", []),
            "layers": item.get("mapping_layers", item.get("layers", [])),
            "mapping_layers": item.get("mapping_layers", item.get("layers", [])),
            "role": item.get("role", ""),
            "aigc_visual_mapping": item.get("aigc_visual_mapping", ""),
            "visual_metaphors": item.get("visual_metaphors", []),
            "composition_effect": item.get("composition_effect", {}),
            "parameters": item.get("parameters", {}),
            "must_include": item.get("must_include", []),
            "avoid": item.get("avoid", []),
            "visual_mapping": item.get("aigc_visual_mapping", "") or metaphors,
            "touch_action": "",
            "content": content,
        }

    def _score(self, chunk: dict[str, Any], query: str, terms: list[str]) -> float:
        content = chunk["content"]
        score = 0.0

        field = chunk.get("field", "")
        if field and field in query:
            score += 8.0

        for value in chunk.get("values", []):
            if value and value in query:
                score += 7.0

        semantic_variable = chunk.get("semantic_variable", "")
        if semantic_variable and semantic_variable in query:
            score += 4.0

        category = chunk.get("category", "")
        if category and category in query:
            score += 2.0

        for term in terms:
            if len(term) >= 2 and term in content:
                score += 1.0

        return score

    def _hit(self, chunk: dict[str, Any], score: float) -> dict[str, Any]:
        return {
            "id": chunk.get("id"),
            "field": chunk.get("field"),
            "semantic_variable": chunk.get("semantic_variable"),
            "semantic_slot": chunk.get("semantic_slot"),
            "category": chunk.get("category"),
            "layers": chunk.get("layers", []),
            "mapping_layers": chunk.get("mapping_layers", []),
            "content_type": chunk.get("content_type", ""),
            "strategy": chunk.get("strategy", ""),
            "role": chunk.get("role", ""),
            "visual_metaphors": chunk.get("visual_metaphors", []),
            "composition_effect": chunk.get("composition_effect", {}),
            "parameters": chunk.get("parameters", {}),
            "must_include": chunk.get("must_include", []),
            "avoid": chunk.get("avoid", []),
            "visual_mapping": chunk.get("visual_mapping", ""),
            "aigc_visual_mapping": chunk.get("aigc_visual_mapping", ""),
            "touch_action": chunk.get("touch_action", ""),
            "content": chunk.get("content", ""),
            "score": round(score, 3),
        }

    def _terms(self, query: str) -> list[str]:
        raw_terms = re.split(r"[\s,，。；;:：|/()\[\]{}]+", query)
        terms = [term.strip() for term in raw_terms if len(term.strip()) >= 2]
        compact = re.sub(r"\s+", "", query)
        terms.extend(compact[i : i + 2] for i in range(max(len(compact) - 1, 0)))
        return list(dict.fromkeys(terms))


@lru_cache(maxsize=1)
def load_semantic_rag() -> SemanticGraphRAG:
    return SemanticGraphRAG(load_semantic_graph())
