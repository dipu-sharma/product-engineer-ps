import pytest
from src.domain.entities import ChunkType, StreamChunk, TerminalState, TurnRequest
from src.observability.redactor import redact_secrets, REDACTED_PLACEHOLDER
from src.observability.tracer import SafeTracer
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator


def test_redact_secrets_keys_and_patterns():
    payload = {
        "api_key": "secret-12345",
        "nested": {
            "token": "tok-abcdef",
            "safe_field": "public_data",
            "auth_header": "Bearer abc123def456ghi789jkl012",
        },
        "user_secret_input": "sk-123456789012345678901234",
    }
    redacted = redact_secrets(payload)
    assert redacted["api_key"] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["token"] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["safe_field"] == "public_data"
    assert redacted["nested"]["auth_header"] == REDACTED_PLACEHOLDER
    assert REDACTED_PLACEHOLDER in redacted["user_secret_input"]


def test_tracer_terminal_sealing_blocks_post_terminal_events():
    tracer = SafeTracer()
    ev1 = tracer.record_event("RUN_STARTED", {"key": "val"})
    assert ev1 is not None
    assert ev1.seq == 1

    # Record terminal event
    ev_term = tracer.record_event("STATE_COMPLETED", {"status": "done"}, is_terminal=True)
    assert ev_term is not None
    assert ev_term.seq == 2
    assert tracer.is_sealed is True

    # Any attempt to record an event after sealing must return None and NOT append
    ev_post = tracer.record_event("POST_TERMINAL_LEAK", {"data": "should_not_exist"})
    assert ev_post is None

    events = tracer.get_events()
    assert len(events) == 2
    assert events[-1].event_type == "STATE_COMPLETED"


@pytest.mark.asyncio
async def test_trace_omits_hidden_chain_of_thought_reasoning():
    """AC7: Safe operational trace omits internal model reasoning and redacts metadata secrets."""
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()
    secret_key = "sk-super-secret-production-key-9999"
    provider = FakeModelProvider(
        chunks=["Final answer."],
        emit_reasoning=True,
        secret_metadata_value=secret_key,
    )
    orchestrator = TurnOrchestrator(policy, provider, store)

    request = TurnRequest(user_input="Explain quantum physics with hidden thoughts.")
    result = await orchestrator.execute_turn(request)

    assert result.terminal_state == TerminalState.COMPLETED
    
    # 1. Verify secrets are NOT leaked in trace
    for event in result.trace:
        serialized = str(event.payload)
        assert secret_key not in serialized

    # 2. Verify hidden reasoning raw text is NEVER in the trace
    for event in result.trace:
        if "reasoning_chunk" in event.event_type.lower():
            assert "reasoning_content" not in event.payload
            assert event.payload.get("has_internal_reasoning") is True
            assert event.payload.get("reasoning_char_count") > 0
            # Raw string should not be present
            assert "Internal reasoning:" not in str(event.payload)

    # 3. Verify monotonic sequence ordering
    seqs = [e.seq for e in result.trace]
    assert seqs == list(range(1, len(result.trace) + 1))
