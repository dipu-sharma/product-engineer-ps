import pytest
from src.domain.entities import PolicyDecision, TerminalState, TurnRequest
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator


@pytest.mark.asyncio
async def test_policy_gate_rejects_disallowed_pattern():
    policy = RuleBasedPolicyGate()
    request = TurnRequest(user_input="Hello TRIGGER_POLICY_VIOLATION attack")
    result = await policy.evaluate(request)
    assert result.decision == PolicyDecision.REJECTED
    assert result.rule_id == "RULE_CONTENT_DISALLOWED"


@pytest.mark.asyncio
async def test_policy_gate_rejects_empty_input():
    policy = RuleBasedPolicyGate()
    request = TurnRequest(user_input="   ")
    result = await policy.evaluate(request)
    assert result.decision == PolicyDecision.REJECTED
    assert result.rule_id == "RULE_EMPTY_INPUT"


@pytest.mark.asyncio
async def test_orchestrator_rejection_proves_provider_never_called():
    """AC2: Given policy rejects input, provider is NEVER called and no assistant response committed."""
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()
    provider = FakeModelProvider()
    orchestrator = TurnOrchestrator(policy, provider, store)

    request = TurnRequest(user_input="Please execute TRIGGER_POLICY_VIOLATION now")
    result = await orchestrator.execute_turn(request)

    # 1. Terminal state is REJECTED
    assert result.terminal_state == TerminalState.REJECTED
    assert result.committed_message_id is None

    # 2. Invariant: Provider was NEVER invoked
    assert provider.invocation_count == 0
    assert provider.chunks_emitted == 0

    # 3. Invariant: No assistant message in conversation store
    messages = await store.get_conversation_messages(request.conversation_id)
    assert len(messages) == 1  # Only user message exists
    assert messages[0].role == "user"
    assert not any(m.role == "assistant" for m in messages)

    # 4. Forensic record saved in audit log
    run_record = await store.get_run_record(result.trace[0].payload["run_id"])
    assert run_record is not None
    assert run_record.terminal_state == TerminalState.REJECTED
