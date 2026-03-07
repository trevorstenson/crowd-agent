from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

DECAY_BUCKETS_MINUTES = (
    (5, 1.0),
    (10, 0.5),
    (15, 0.2),
)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def reaction_weight(created_at: datetime, now: datetime | None = None) -> float:
    """Return the current weight for a reaction based on its age."""
    now = ensure_utc(now or datetime.now(timezone.utc))
    created = ensure_utc(created_at)
    age_minutes = max(0.0, (now - created).total_seconds() / 60.0)

    for max_age_minutes, weight in DECAY_BUCKETS_MINUTES:
        if age_minutes < max_age_minutes:
            return weight
    return 0.0


def weighted_reaction_value(content: str, created_at: datetime, now: datetime | None = None) -> float:
    if content not in {"+1", "-1"}:
        return 0.0
    sign = 1.0 if content == "+1" else -1.0
    return sign * reaction_weight(created_at, now=now)


def weighted_net_reactions(reactions: Iterable, now: datetime | None = None) -> float:
    total = 0.0
    now = ensure_utc(now or datetime.now(timezone.utc))
    for reaction in reactions:
        created_at = getattr(reaction, "created_at", None)
        content = getattr(reaction, "content", None)
        if created_at is None or content is None:
            continue
        total += weighted_reaction_value(content, created_at, now=now)
    return total
