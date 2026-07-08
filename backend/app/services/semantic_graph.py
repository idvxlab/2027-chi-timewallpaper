from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


GRAPH_PATH = Path(__file__).resolve().parents[1] / "data" / "semantic_graph.json"


class SemanticGraph:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.version = payload.get("version", "unknown")
        self.layers = payload.get("mapping_layers", payload.get("layers", {}))
        self.composition_contract = payload.get("composition_contract", {})
        self.content_type_policy = payload.get("content_type_policy", {})
        self.rules = payload.get("rules", payload.get("variables", []))
        self.variables = self.rules
        self._by_field = {item.get("field"): item for item in self.rules if item.get("field")}

    def resolve(self, field: str, value: Any) -> Optional[dict[str, Any]]:
        rule = self._by_field.get(field)
        if not rule:
            return None
        normalized = self._format_value(value)
        scale = rule.get("scale", {})
        numeric_value = None
        if isinstance(scale, dict):
            numeric_value = scale.get(normalized)
        return {
            "id": rule.get("id"),
            "field": field,
            "semantic_slot": rule.get("semantic_slot"),
            "semantic_variable": rule.get("semantic_variable") or rule.get("semantic_slot"),
            "value": normalized,
            "category": rule.get("category", ""),
            "content_type": rule.get("content_type", ""),
            "strategy": rule.get("strategy", ""),
            "mapping_layers": rule.get("mapping_layers", rule.get("layers", [])),
            "layers": rule.get("mapping_layers", rule.get("layers", [])),
            "role": rule.get("role", ""),
            "aigc_visual_mapping": rule.get("aigc_visual_mapping", ""),
            "visual_metaphors": rule.get("visual_metaphors", []),
            "composition_effect": rule.get("composition_effect", {}),
            "parameters": rule.get("parameters", {}),
            "numeric_value": numeric_value,
            "must_include": rule.get("must_include", []),
            "avoid": rule.get("avoid", []),
        }

    def layer_name(self, layer_id: str) -> str:
        layer = self.layers.get(layer_id, {})
        return layer.get("zh") or layer.get("name") or layer_id

    def _format_value(self, value: Any) -> str:
        if isinstance(value, list):
            return "、".join(str(item) for item in value if item)
        if value is None:
            return ""
        return str(value)


@lru_cache(maxsize=1)
def load_semantic_graph() -> SemanticGraph:
    with GRAPH_PATH.open("r", encoding="utf-8") as f:
        return SemanticGraph(json.load(f))
