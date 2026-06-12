"""Outcome simulation. Percentages per PROJECT.md section 5.

plan_events is pure: injected rng, returns the full callback plan as data,
including deliberate duplicates and reordering, so chaos is testable.
"""

import random
from dataclasses import dataclass

P_DELIVERED = 0.90
P_FAILED = 0.08
P_OPENED = 0.60
P_READ = 0.70
P_CLICKED = 0.25
P_CONVERTED = 0.15


@dataclass(frozen=True)
class PlannedEvent:
    event_type: str
    delay_seconds: float


def _jitter(rng: random.Random, lo: float, hi: float) -> float:
    return rng.uniform(lo, hi)


def plan_events(
    rng: random.Random,
    jitter_min: float,
    jitter_max: float,
    duplicate_probability: float,
    reorder_probability: float,
) -> list[PlannedEvent]:
    types = ["sent"]
    roll = rng.random()
    if roll < P_FAILED:
        types.append("failed")
    elif roll < P_FAILED + (1 - P_FAILED - P_DELIVERED):
        pass
    else:
        types.append("delivered")
        if rng.random() < P_OPENED:
            types.append("opened")
            if rng.random() < P_READ:
                types.append("read")
                if rng.random() < P_CLICKED:
                    types.append("clicked")
                    if rng.random() < P_CONVERTED:
                        types.append("converted")

    delays = sorted(_jitter(rng, jitter_min, jitter_max) for _ in types)
    events = [PlannedEvent(t, d) for t, d in zip(types, delays, strict=True)]

    if len(events) > 1 and rng.random() < reorder_probability:
        i, j = rng.sample(range(len(events)), 2)
        events[i], events[j] = (
            PlannedEvent(events[j].event_type, events[i].delay_seconds),
            PlannedEvent(events[i].event_type, events[j].delay_seconds),
        )

    duplicates = [
        PlannedEvent(ev.event_type, ev.delay_seconds + _jitter(rng, 0.1, 2.0))
        for ev in list(events)
        if rng.random() < duplicate_probability
    ]
    return events + duplicates
