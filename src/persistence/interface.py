from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.domain.entities import ConversationMessage, RunRecord


class PersistenceStore(ABC):
    """Abstract interface for dual-boundary persistence.
    
    Boundary A: Conversation History (user and committed assistant messages).
    Boundary B: Run Audit Record (forensic trace of turn execution and partial output).
    """

    @abstractmethod
    async def save_user_message(
        self,
        conversation_id: str,
        turn_id: str,
        content: str,
    ) -> ConversationMessage:
        """Persist a user message initiating a conversational turn."""
        pass

    @abstractmethod
    async def commit_assistant_message(
        self,
        conversation_id: str,
        turn_id: str,
        content: str,
    ) -> ConversationMessage:
        """Atomically commit an assistant response upon successful turn completion.
        
        Must NOT be called on rejected, cancelled, timed-out, or failed runs.
        """
        pass

    @abstractmethod
    async def save_run_record(self, record: RunRecord) -> None:
        """Persist operational run audit record for any terminal state."""
        pass

    @abstractmethod
    async def get_conversation_messages(
        self,
        conversation_id: str,
    ) -> List[ConversationMessage]:
        """Retrieve committed conversation history for a given conversation."""
        pass

    @abstractmethod
    async def get_run_record(self, run_id: str) -> Optional[RunRecord]:
        """Retrieve audit record for a specific run."""
        pass
