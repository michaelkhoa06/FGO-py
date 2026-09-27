"""Small, dependency-free data contracts for the battle controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ActionKind = Literal["skill", "skill_target", "enemy_target", "attack", "cards"]
ResultStatus = Literal["confirmed", "uncertain", "rejected", "error"]


@dataclass(frozen=True)
class Action:
    """A requested battle interaction.  All game-facing indexes are one based."""

    kind: str
    servant: int | None = None
    skill: int | None = None
    target: int | None = None
    cards: tuple[int, ...] = ()
    reason: str = ""


@dataclass
class Observation:
    """A normalized, observable view of the current battle screen."""

    id: str
    phase: str
    captured_at: str
    screen_path: str | None = None
    allies: list[dict] = field(default_factory=list)
    enemies: list[dict] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    wave: int | None = None
    turn: int | None = None
    target_count: int | None = None
    errors: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)


@dataclass
class ActionResult:
    """Evidence-backed result of one requested action."""

    action: Action
    status: ResultStatus
    before_observation_id: str | None
    after_observation_id: str | None
    evidence: list[str] = field(default_factory=list)
    skill_consumed: bool | None = None
    effect_observed: bool | None = None
    reason: str = ""

