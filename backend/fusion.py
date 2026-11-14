"""Conservative fusion policy for normalized liveness and identity scores."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class RiskThresholds:
    live_allow: float
    live_deny: float
    similarity_allow: float
    similarity_deny: float


@dataclass(frozen=True)
class PolicyThresholds:
    spoof_veto: float
    phone_paper_veto: float
    risk_levels: Mapping[str, RiskThresholds]


@dataclass(frozen=True)
class Decision:
    outcome: str
    tier: str
    live_score: float
    similarity: float
    reasons: tuple[str, ...]


def load_policy_thresholds(path: str | Path) -> PolicyThresholds:
    """Load thresholds from config/thresholds.v1.json."""
    with Path(path).open(encoding="utf-8") as source:
        raw = json.load(source)

    risk_levels = {
        name: RiskThresholds(**values)
        for name, values in raw["risk_levels"].items()
    }
    thresholds = PolicyThresholds(
        spoof_veto=raw["spoof_veto"],
        phone_paper_veto=raw["phone_paper_veto"],
        risk_levels=risk_levels,
    )
    _validate_thresholds(thresholds)
    return thresholds


def decide(
    *,
    spoof_prob: float,
    signals: Mapping[str, Any],
    quality: bool,
    similarity: float,
    thresholds: PolicyThresholds,
    risk_level: str = "lobby",
    tier2_passed: bool | None = None,
    tier3_passed: bool | None = None,
) -> Decision:
    """Return ALLOW, STEP_UP, or DENY; invalid input raises rather than allowing."""
    _validate_thresholds(thresholds)
    spoof_prob = _probability("spoof_prob", spoof_prob)
    similarity = _probability("similarity", similarity)
    if not isinstance(quality, bool):
        raise ValueError("quality must be a boolean")
    if not isinstance(signals, Mapping):
        raise ValueError("signals must be a mapping")
    if risk_level not in thresholds.risk_levels:
        raise ValueError(f"unknown risk level: {risk_level}")
    if tier2_passed is not None and not isinstance(tier2_passed, bool):
        raise ValueError("tier results must be true, false, or null")
    if tier3_passed is not None and not isinstance(tier3_passed, bool):
        raise ValueError("tier results must be true, false, or null")
    if "hard_spoof_veto" in signals and not isinstance(signals["hard_spoof_veto"], bool):
        raise ValueError("signals.hard_spoof_veto must be a boolean")

    risk = thresholds.risk_levels[risk_level]
    live_score = 1.0 - spoof_prob
    tier = "tier3" if tier3_passed is not None else "tier2" if tier2_passed is not None else "tier1"
    reasons: list[str] = []

    if signals.get("hard_spoof_veto") is True:
        reasons.append("hard_spoof_veto")
    phone_paper_confidence = _probability(
        "signals.phone_paper_confidence",
        signals.get("phone_paper_confidence", 0.0),
    )
    if phone_paper_confidence >= thresholds.phone_paper_veto:
        reasons.append("phone_or_paper_veto")
    if spoof_prob >= thresholds.spoof_veto:
        reasons.append("spoof_score_veto")
    if reasons:
        return Decision("DENY", tier, live_score, similarity, tuple(reasons))

    if similarity < risk.similarity_deny:
        return Decision("DENY", tier, live_score, similarity, ("identity_below_deny_threshold",))
    if tier2_passed is False:
        return Decision("DENY", "tier2", live_score, similarity, ("tier2_failed",))
    if tier3_passed is False:
        return Decision("DENY", "tier3", live_score, similarity, ("tier3_failed",))
    if not quality:
        return Decision("STEP_UP", tier, live_score, similarity, ("recapture_quality",))
    if live_score <= risk.live_deny:
        return Decision("DENY", tier, live_score, similarity, ("liveness_below_deny_threshold",))

    identity_clear = similarity >= risk.similarity_allow
    passive_clear = live_score >= risk.live_allow
    step_up_passed = tier2_passed is True or tier3_passed is True

    if identity_clear and passive_clear:
        return Decision("ALLOW", tier, live_score, similarity, ("tier1_thresholds_met",))
    if identity_clear and step_up_passed:
        return Decision("ALLOW", tier, live_score, similarity, ("step_up_passed",))

    return Decision("STEP_UP", tier, live_score, similarity, ("uncertain_score_band",))


def _probability(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return result


def _validate_thresholds(thresholds: PolicyThresholds) -> None:
    if not isinstance(thresholds, PolicyThresholds):
        raise ValueError("thresholds must be a PolicyThresholds instance")
    _probability("spoof_veto", thresholds.spoof_veto)
    _probability("phone_paper_veto", thresholds.phone_paper_veto)
    if not thresholds.risk_levels:
        raise ValueError("at least one risk level is required")
    for name, risk in thresholds.risk_levels.items():
        _probability(f"{name}.live_allow", risk.live_allow)
        _probability(f"{name}.live_deny", risk.live_deny)
        _probability(f"{name}.similarity_allow", risk.similarity_allow)
        _probability(f"{name}.similarity_deny", risk.similarity_deny)
        if risk.live_deny >= risk.live_allow:
            raise ValueError(f"{name}: live_deny must be below live_allow")
        if risk.similarity_deny >= risk.similarity_allow:
            raise ValueError(f"{name}: similarity_deny must be below similarity_allow")