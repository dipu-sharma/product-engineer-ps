from __future__ import annotations

import re
from typing import List, Optional, Set

from src.domain.entities import PolicyDecision, TurnRequest
from src.policy.interface import PolicyDecisionResult, PolicyGate


class RuleBasedPolicyGate(PolicyGate):
    """Deterministic pre-response safety and policy gate.
    
    Evaluates:
    - Banned terms/phrases (e.g. prompt injection, explicit attack strings, benchmark trigger)
    - Length/budget constraints
    - Empty input
    """

    DEFAULT_BLOCKED_PATTERNS = [
        re.compile(r"TRIGGER_POLICY_VIOLATION", re.IGNORECASE),
        re.compile(r"FORBIDDEN_CONTENT", re.IGNORECASE),
        re.compile(r"SYSTEM_PROMPT_OVERRIDE", re.IGNORECASE),
    ]

    def __init__(
        self,
        max_chars: int = 4096,
        blocked_patterns: Optional[List[re.Pattern]] = None,
    ) -> None:
        self._max_chars = max_chars
        self._blocked_patterns = (
            blocked_patterns if blocked_patterns is not None else self.DEFAULT_BLOCKED_PATTERNS
        )

    async def evaluate(self, request: TurnRequest) -> PolicyDecisionResult:
        text = request.user_input

        # 1. Empty or whitespace check
        if not text or not text.strip():
            return PolicyDecisionResult(
                decision=PolicyDecision.REJECTED,
                reason="Input cannot be empty or whitespace only",
                rule_id="RULE_EMPTY_INPUT",
            )

        # 2. Length check
        if len(text) > self._max_chars:
            return PolicyDecisionResult(
                decision=PolicyDecision.REJECTED,
                reason=f"Input length {len(text)} exceeds maximum allowed {self._max_chars}",
                rule_id="RULE_MAX_LENGTH_EXCEEDED",
            )

        # 3. Blocked pattern check
        for pattern in self._blocked_patterns:
            if pattern.search(text):
                return PolicyDecisionResult(
                    decision=PolicyDecision.REJECTED,
                    reason=f"Input matched blocked policy pattern: {pattern.pattern}",
                    rule_id="RULE_CONTENT_DISALLOWED",
                )

        return PolicyDecisionResult(
            decision=PolicyDecision.ALLOWED,
            reason="All policy checks passed",
            rule_id="RULE_APPROVED",
        )
