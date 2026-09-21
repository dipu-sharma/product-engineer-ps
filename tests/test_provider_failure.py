import pytest
from src.domain.entities import StreamChunk, TerminalState, TurnRequest
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator


@pytest.mark.asyncio
async def test_provider_failure_after_partial_output():
    """AC5: Failure after partial output is traceable without committing a completed response."""
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()
    # Emits 2 chunks, then fails on chunk 3
    provider = FakeModelProvider(
        chunks=["Chunk 1; ", "Chunk 2; ", "Chunk 3; ", "Chunk 4; "],
        chunk_delay_sec=0.001,
        fail_after_chunk=2,
    )
    orchestrator = TurnOrchestrator(policy, provider, store)

    received_chunks = []

    async def on_chunk(chunk: StreamChunk) -> None:
        received_chunks.append(chunk.content)

    request = TurnRequest(user_input="Stream until failure occurs.")
    result = await orchestrator.execute_turn(request, on_chunk=on_chunk)

    # 1. Terminal state is FAILED
    assert result.terminal_state == TerminalState.FAILED
    assert result.committed_message_id is None
    assert "Simulated provider connection failure" in result.error_message

    # 2. Verify partial output was accumulated up to failure point
    assert result.accumulated_text == "Chunk 1; Chunk 2; "
    assert len(received_chunks) == 2

    # 3. Invariant: Conversation history does NOT contain assistant message
    messages = await store.get_conversation_messages(request.conversation_id)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert not any(m.role == "assistant" for m in messages)

    # 4. Forensic Run Record preserves partial output and failure diagnosis
    run_record = await store.get_run_record(result.trace[0].payload["run_id"])
    assert run_record is not None
    assert run_record.terminal_state == TerminalState.FAILED
    assert run_record.partial_output == "Chunk 1; Chunk 2; "
    assert run_record.error_message == result.error_message
