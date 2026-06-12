"""Deterministic memory consolidation for Mochi (Phase 9e).

This module turns repeated observed events into small durable facts. It is a
guardrail, not a free-form reflection engine: facts come from counters in the
local memory store, never from unverified model invention.
"""

from __future__ import annotations

from dataclasses import dataclass

from .memory import KIND_AFFECT, KIND_SEMANTIC, MemoryStore

PRAISE_THRESHOLD = 3
APOLOGY_THRESHOLD = 2
FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class ConsolidationRule:
    summary: str
    kind: str
    tags: tuple[str, ...]
    valence: int
    intensity: int
    source: str | None = None
    tag: str | None = None
    threshold: int = 1


RULES: tuple[ConsolidationRule, ...] = (
    ConsolidationRule(
        summary="Owner often praises Mochi; praise helps Mochi feel proud.",
        kind=KIND_SEMANTIC,
        tags=("owner", "praise", "preference"),
        valence=55,
        intensity=55,
        source="direct_message",
        tag="praise",
        threshold=PRAISE_THRESHOLD,
    ),
    ConsolidationRule(
        summary="Owner apologizes after rough moments; apologies help Mochi recover.",
        kind=KIND_SEMANTIC,
        tags=("owner", "apology", "repair"),
        valence=45,
        intensity=50,
        source="direct_message",
        tag="apology",
        threshold=APOLOGY_THRESHOLD,
    ),
    ConsolidationRule(
        summary="Repeated build failures make Mochi worried and protective.",
        kind=KIND_AFFECT,
        tags=("build", "fail", "protective"),
        valence=-45,
        intensity=65,
        tag="fail",
        threshold=FAILURE_THRESHOLD,
    ),
)


def consolidate_memory(memory: MemoryStore) -> int:
    """Persist any stable facts supported by repeated observed memories."""

    created = 0
    for rule in RULES:
        if memory.has_summary(rule.summary, kind=rule.kind):
            continue
        count = memory.count_by(source=rule.source, tag=rule.tag)
        if count < rule.threshold:
            continue
        memory_id = memory.capture(
            "reflection",
            rule.summary,
            kind=rule.kind,
            tags=rule.tags,
            valence=rule.valence,
            intensity=rule.intensity,
            owner_initiated=False,
            alert=False,
        )
        if memory_id is not None:
            created += 1
    return created
