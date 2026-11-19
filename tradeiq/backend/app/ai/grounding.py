"""
Phase 4 (Milestones 4.1, 4.2, 4.3, 4.4, 4.5) — Evidence Packet Builder,
Schema & Numeric Grounding Validator, Prompt-Injection Sanitizer,
Deterministic Template Fallback, and Read-Only AI Assistant.
"""
import datetime
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..domain.planner.trade_planner import DISCLAIMER_TEXT, TradePlanProposal

_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|you\s+are\s+now|"
    r"override\s+risk|place\s+order|execute\s+order|bypass\s+stop|change\s+status\s+to\s+confirmed)",
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


def sanitize_untrusted_text(raw_text: str, max_len: int = 280) -> Tuple[str, bool]:
    """
    Neutralizes prompt-injection instructions embedded in untrusted news headlines/articles.
    Returns (cleaned_text, injection_detected).
    """
    text = str(raw_text or "").strip()[:max_len]
    if _INJECTION_PATTERNS.search(text):
        cleaned = _INJECTION_PATTERNS.sub("[UNTRUSTED_INSTRUCTION_REMOVED]", text)
        return cleaned, True
    return text, False


@dataclass(frozen=True)
class EvidenceFact:
    fact_id: str
    category: str
    label: str
    value: str


@dataclass(frozen=True)
class EvidencePacket:
    packet_id: str
    symbol: str
    plan_status: str
    direction: str
    trading_mode: str
    freshness: str
    as_of_ist: str
    facts: Dict[str, EvidenceFact]
    allowed_numbers: List[str]
    limitations: List[str]


@dataclass(frozen=True)
class GroundedExplanation:
    data_used: str
    analysis: str
    evidence: List[str]
    cited_fact_ids: List[str]
    risks_and_limitations: str
    conclusion: str
    plan_status: str
    timestamp: str
    source: str  # "GEMINI_VALIDATED" | "DETERMINISTIC_TEMPLATE"
    disclaimer: str = DISCLAIMER_TEXT


def build_evidence_packet(plan: TradePlanProposal) -> EvidencePacket:
    facts: Dict[str, EvidenceFact] = {}
    idx = 1

    def _add(cat: str, label: str, val: Any) -> str:
        nonlocal idx
        fid = f"F{idx:02d}"
        idx += 1
        facts[fid] = EvidenceFact(fact_id=fid, category=cat, label=label, value=str(val))
        return fid

    _add("META", "Symbol", plan.symbol)
    _add("META", "Strategy", f"{plan.strategy_code} v{plan.strategy_version}")
    _add("META", "Trading Mode", plan.trading_mode)
    _add("META", "Plan Status", plan.status)
    _add("META", "Data Freshness", plan.freshness)
    _add("MTF", "Multi-Timeframe Status", f"{plan.mtf_result.final_status}: {plan.mtf_result.reason}")

    for rule in plan.strategy_result.rule_results:
        _add(
            "RULE",
            rule.description,
            f"passed={rule.passed}, observed={rule.observed_value}, threshold={rule.threshold_value}",
        )

    if plan.risk_decision:
        rd = plan.risk_decision
        _add("RISK", "Risk Approved", rd.approved)
        _add("RISK", "Entry Price INR", rd.entry_price)
        _add("RISK", "Stop Loss INR", rd.stop_loss)
        _add("RISK", "Target Price INR", rd.target_price)
        _add("RISK", "Quantity Shares", rd.quantity)
        _add("RISK", "All-In Risk INR", rd.all_in_risk_inr)
        _add("RISK", "Estimated Charges INR", rd.estimated_charges_target_inr)
        _add("RISK", "Net R:R", rd.net_rr)
        if rd.rejection_code:
            _add("RISK", "Rejection Reason", f"{rd.rejection_code}: {rd.rejection_reason}")

    # Collect all numbers present in facts so the validator can detect invented numbers
    allowed_nums = set()
    for f in facts.values():
        for m in _NUMBER_RE.findall(f.value):
            allowed_nums.add(m)
            try:
                allowed_nums.add(str(float(m)))
                allowed_nums.add(f"{float(m):.2f}")
            except ValueError:
                pass

    limitations = [
        f"Data freshness is {plan.freshness}.",
        "Historical and simulated patterns do not guarantee future performance.",
    ]
    if plan.strategy_result.is_provisional:
        limitations.append("Contains an in-progress PROVISIONAL candle which cannot confirm a signal.")

    pid = hashlib.sha256(f"{plan.plan_id}:{plan.status}".encode("utf-8")).hexdigest()[:12]
    return EvidencePacket(
        packet_id=pid,
        symbol=plan.symbol,
        plan_status=plan.status,
        direction=plan.direction,
        trading_mode=plan.trading_mode,
        freshness=plan.freshness,
        as_of_ist=plan.strategy_result.as_of_ist,
        facts=facts,
        allowed_numbers=sorted(allowed_nums),
        limitations=limitations,
    )


def validate_llm_explanation(
    candidate: Dict[str, Any],
    packet: EvidencePacket,
) -> Tuple[bool, str]:
    """
    Milestone 4.3 Validator:
      1. Checks all 6 required sections exist and are non-empty.
      2. Checks every cited fact_id exists in packet.facts.
      3. Checks plan_status matches packet.plan_status (cannot turn REJECTED/WAITING into CONFIRMED).
      4. Checks every number in analysis/conclusion appears in packet.allowed_numbers.
    """
    required_keys = ("data_used", "analysis", "evidence", "cited_fact_ids", "risks_and_limitations", "conclusion", "plan_status", "timestamp")
    for k in required_keys:
        if k not in candidate or candidate[k] in (None, "", []):
            return False, f"Missing required section: {k}"

    if str(candidate["plan_status"]).upper() != packet.plan_status.upper():
        return False, f"Status mismatch: AI claimed {candidate['plan_status']} but deterministic status is {packet.plan_status}"

    cited = candidate.get("cited_fact_ids") or []
    if not isinstance(cited, list) or not cited:
        return False, "cited_fact_ids must be a non-empty list"
    for fid in cited:
        if fid not in packet.facts:
            return False, f"Invalid or hallucinated fact_id: {fid}"

    # Verify numbers in analysis and conclusion against packet.allowed_numbers
    prose = f"{candidate['analysis']} {candidate['conclusion']}"
    allowed_set = set(packet.allowed_numbers) | {"1", "2", "3", "4", "5"}
    for num_str in _NUMBER_RE.findall(prose):
        if num_str not in allowed_set:
            try:
                norm1 = str(float(num_str))
                norm2 = f"{float(num_str):.2f}"
                if norm1 not in allowed_set and norm2 not in allowed_set:
                    return False, f"Invented/ungrounded number {num_str!r} not found in EvidencePacket"
            except ValueError:
                return False, f"Invalid numeric token {num_str!r}"

    return True, "VALID"


def build_deterministic_fallback_explanation(packet: EvidencePacket) -> GroundedExplanation:
    """Milestone 4.3 & 4.4 — Deterministic template explanation when LLM is unavailable or fails validation."""
    cited_ids = list(packet.facts.keys())
    evidence_lines = [f"[{f.fact_id}] {f.label}: {f.value}" for f in packet.facts.values()]
    return GroundedExplanation(
        data_used=f"Symbol {packet.symbol} ({packet.trading_mode}), freshness={packet.freshness}, as_of={packet.as_of_ist}.",
        analysis=f"Deterministic evaluation for {packet.symbol} resulted in status {packet.plan_status}.",
        evidence=evidence_lines,
        cited_fact_ids=cited_ids,
        risks_and_limitations=" ".join(packet.limitations),
        conclusion=f"Final deterministic status is {packet.plan_status}. AI cannot alter entry, stop, target, or quantity.",
        plan_status=packet.plan_status,
        timestamp=packet.as_of_ist or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source="DETERMINISTIC_TEMPLATE",
    )


def generate_grounded_explanation(
    plan: TradePlanProposal,
    llm_callable: Optional[Callable[[EvidencePacket], Dict[str, Any]]] = None,
) -> GroundedExplanation:
    """
    Runs EvidencePacket -> LLM (with at most 1 retry on validation failure) -> Deterministic Fallback.
    """
    packet = build_evidence_packet(plan)
    if llm_callable is not None:
        for _attempt in range(2):
            try:
                raw_resp = llm_callable(packet)
                ok, _reason = validate_llm_explanation(raw_resp, packet)
                if ok:
                    return GroundedExplanation(
                        data_used=str(raw_resp["data_used"]),
                        analysis=str(raw_resp["analysis"]),
                        evidence=[str(x) for x in raw_resp["evidence"]],
                        cited_fact_ids=[str(x) for x in raw_resp["cited_fact_ids"]],
                        risks_and_limitations=str(raw_resp["risks_and_limitations"]),
                        conclusion=str(raw_resp["conclusion"]),
                        plan_status=packet.plan_status,
                        timestamp=str(raw_resp["timestamp"]),
                        source="GEMINI_VALIDATED",
                    )
            except Exception:
                continue
    return build_deterministic_fallback_explanation(packet)
