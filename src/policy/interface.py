from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from src.domain.entities import PolicyDecision, TurnRequest


@dataclass(frozen=True)
class PolicyDecisionResult:
    decision: PolicyDecision
    reason: Optional[str] = None
    rule_id: Optional[str] = None

    @property
    def is_allowed(self) -> bool:
        return self.decision == PolicyDecision.ALLOWED


class PolicyGate(ABC):
    """Abstract interface for pre-response safety and policy checks."""

    @abstractmethod
    async def evaluate(self, request: TurnRequest) -> PolicyDecisionResult:
        """Evaluate whether a conversational turn is permitted before invoking provider."""
        pass
