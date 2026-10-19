"""Deterministic, bounded escalation independent of fresh-worker lifecycle."""


def next_tier(history: list[dict], current: str) -> str:
    if current == "ESCALATED":
        return current
    latest = history[-1]
    if any(f["category"] in {"architecture", "reasoning"} for f in latest["findings"]):
        return "ESCALATED"
    if len(history) >= 2:
        def defects(review):
            return {f["id"] for f in review["findings"] if f["severity"] == "blocking"}
        if defects(history[-1]) & defects(history[-2]):
            return "ESCALATED"
    return "NORMAL"
