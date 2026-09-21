from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional

from src.domain.entities import ChunkType, StreamChunk
from src.domain.exceptions import ProviderExecutionError
from src.provider.interface import ModelProvider


class FakeModelProvider(ModelProvider):
    """Deterministic, controllable fake model provider for tests and benchmarks.
    
    Supports:
    - Custom text chunks and emission delays
    - Configurable mid-stream failure injection
    - Simulated internal reasoning chunks (CoT)
    - Metadata with simulated secrets for redaction verification
    - Immediate reaction to cancellation events
    """

    DEFAULT_CHUNKS = [
        "The ",
        "quick ",
        "brown ",
        "fox ",
        "jumps ",
        "over ",
        "the ",
        "lazy ",
        "dog.",
    ]

    def __init__(
        self,
        chunks: Optional[List[str]] = None,
        chunk_delay_sec: float = 0.01,
        fail_after_chunk: Optional[int] = None,
        emit_reasoning: bool = False,
        hang: bool = False,
        secret_metadata_value: Optional[str] = None,
    ) -> None:
        self.chunks = chunks if chunks is not None else list(self.DEFAULT_CHUNKS)
        self.chunk_delay_sec = chunk_delay_sec
        self.fail_after_chunk = fail_after_chunk
        self.emit_reasoning = emit_reasoning
        self.hang = hang
        self.secret_metadata_value = secret_metadata_value

        # Observability / inspection counters
        self.invocation_count = 0
        self.chunks_emitted = 0
        self.was_cancelled = False

    async def stream(
        self,
        prompt: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[StreamChunk]:
        self.invocation_count += 1
        seq = 0

        # Optional simulated internal reasoning (CoT)
        if self.emit_reasoning:
            seq += 1
            yield StreamChunk(
                seq=seq,
                chunk_type=ChunkType.REASONING,
                content="Internal reasoning: analyzing input tokens and planning output...",
                metadata={"internal": True},
            )

        if self.hang:
            # Hang until cancelled
            while not cancel_event.is_set():
                await asyncio.sleep(0.01)
            self.was_cancelled = True
            return

        for chunk_text in self.chunks:
            if cancel_event.is_set():
                self.was_cancelled = True
                return

            if self.chunk_delay_sec > 0:
                try:
                    await asyncio.wait_for(
                        cancel_event.wait(),
                        timeout=self.chunk_delay_sec,
                    )
                    # If wait() completed without timeout, cancellation was triggered!
                    self.was_cancelled = True
                    return
                except asyncio.TimeoutError:
                    # Delay elapsed normally
                    pass

            seq += 1
            self.chunks_emitted += 1

            # Check if mid-stream failure should be injected
            if self.fail_after_chunk is not None and self.chunks_emitted > self.fail_after_chunk:
                raise ProviderExecutionError(
                    f"Simulated provider connection failure after emitting {self.fail_after_chunk} chunks"
                )

            metadata = {}
            if self.secret_metadata_value:
                metadata["api_key"] = self.secret_metadata_value

            yield StreamChunk(
                seq=seq,
                chunk_type=ChunkType.TEXT,
                content=chunk_text,
                metadata=metadata,
            )
