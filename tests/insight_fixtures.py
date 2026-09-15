"""Synthetic provider replies for orchestration tests, not quality evaluation."""
import json
from src.ai_provider import ProviderResponse
from src.insight_evidence import unique_object


def evidence_reply(messages, label="배송 지연"):
    reviews = json.loads(messages[1]["content"])["reviews"]
    return ProviderResponse(json.dumps({"reviews": [
        {"review_number": i, "complaints": [] if label is None else [
            {"label": label, "quote": r["review_text"][:160]}], "praises": []}
        for i, r in enumerate(reviews, 1)]}, ensure_ascii=False), "test")


def staged_reply(messages, schema, narrative, label="배송 지연"):
    if "reviews" in schema["properties"]:
        return evidence_reply(messages, label)
    if "suggestion_candidates" in json.loads(messages[1]["content"]):
        try:
            payload = json.loads(narrative.content, object_pairs_hook=unique_object)
            if isinstance(payload.get("improvement_suggestions"), list) and payload["improvement_suggestions"]:
                payload["improvement_suggestions"] = json.loads(messages[1]["content"])["suggestion_candidates"][:1]
                return ProviderResponse(json.dumps(payload), narrative.model)
        except (ValueError, AttributeError):
            pass
    return narrative
