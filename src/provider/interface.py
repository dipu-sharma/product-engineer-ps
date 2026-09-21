from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import AsyncIterator

from src.domain.entities import StreamChunk


class ModelProvider(ABC):
    """Abstract interface for streaming model generation."""

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[StreamChunk]:
        """Stream generated response chunks.
        
        Must observe cancel_event and abort promptly without continuing to consume compute.
        """
        pass
