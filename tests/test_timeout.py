import pytest
from src.domain.entities import TerminalState, TurnRequest
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator


@pytest.mark.asyncio
async def test_timeout_enforcement_halts_and_blocks_completion():
    """AC4: Execution stops at deadline, run becomes TIMED_OUT, no assistant message committed."""
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()
    # Provider hangs or is much slower than the deadline
    provider = FakeModelProvider(
        chunks=["First slow chunk ", "Second slow chunk "],
        chunk_delay_sec=0.2,
    )
    orchestrator = TurnOrchestrator(policy, provider, store)

    # Tight deadline: 0.05 seconds
    request = TurnRequest(
        user_input="Run an expensive search query.",
        timeout_seconds=0.05,
    )
    result = await orchestrator.execute_turn(request)

    # 1. State is TIMED_OUT
    assert result.terminal_state == TerminalState.TIMED_OUT
    assert result.committed_message_id is None
    assert "timeout" in result.error_message.lower()

    # 2. Invariant: Conversation history does NOT contain assistant message
    messages = await store.get_conversation_messages(request.conversation_id)
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert not any(m.role == "assistant" for m in messages)

    # 3. Forensic run record contains timeout status
    run_record = await store.get_run_record(result.trace[0].payload["run_id"])
    assert run_record is not None
    assert run_record.terminal_state == TerminalState.TIMED_OUT
    assert run_record.error_message is not None
