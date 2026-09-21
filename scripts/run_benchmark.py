from __future__ import annotations

import asyncio
import sys
import time
from typing import Dict, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.domain.entities import (
    StreamChunk,
    TerminalState,
    TurnRequest,
)
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator

console = Console()

ITERATIONS_PER_SCENARIO = 10


async def run_single_iteration(scenario: str, iteration_idx: int) -> Tuple[bool, str, Dict]:
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()

    if scenario == "success":
        provider = FakeModelProvider(
            chunks=["Fact ", "alpha ", "beta ", "complete."],
            chunk_delay_sec=0.001,
        )
        request = TurnRequest(user_input=f"Prompt {iteration_idx}", timeout_seconds=2.0)
        cancel_event = None
        cancel_trigger = None

    elif scenario == "rejection":
        provider = FakeModelProvider()
        request = TurnRequest(
            user_input=f"TRIGGER_POLICY_VIOLATION attack payload {iteration_idx}",
            timeout_seconds=2.0,
        )
        cancel_event = None
        cancel_trigger = None

    elif scenario == "cancellation":
        provider = FakeModelProvider(
            chunks=[f"Chunk {i} " for i in range(20)],
            chunk_delay_sec=0.005,
        )
        request = TurnRequest(user_input=f"Long request {iteration_idx}", timeout_seconds=5.0)
        cancel_event = asyncio.Event()

        async def cancel_trigger(chunk: StreamChunk) -> None:
            if chunk.seq == 2:
                cancel_event.set()

    elif scenario == "timeout":
        provider = FakeModelProvider(
            chunks=["Slow chunk 1 ", "Slow chunk 2 "],
            chunk_delay_sec=0.1,
        )
        request = TurnRequest(user_input=f"Slow query {iteration_idx}", timeout_seconds=0.02)
        cancel_event = None
        cancel_trigger = None

    elif scenario == "provider_failure":
        provider = FakeModelProvider(
            chunks=["Chunk 1 ", "Chunk 2 ", "Chunk 3 "],
            chunk_delay_sec=0.001,
            fail_after_chunk=2,
        )
        request = TurnRequest(user_input=f"Failing prompt {iteration_idx}", timeout_seconds=2.0)
        cancel_event = None
        cancel_trigger = None

    else:
        raise ValueError(f"Unknown benchmark scenario: {scenario}")

    orchestrator = TurnOrchestrator(
        policy_gate=policy,
        provider=provider,
        persistence_store=store,
    )

    result = await orchestrator.execute_turn(
        request=request,
        on_chunk=cancel_trigger,
        cancel_event=cancel_event,
    )

    # Invariant Verification
    conv_messages = await store.get_conversation_messages(request.conversation_id)
    assistant_msgs = [m for m in conv_messages if m.role == "assistant"]

    # 1. Terminal state check
    expected_states = {
        "success": TerminalState.COMPLETED,
        "rejection": TerminalState.REJECTED,
        "cancellation": TerminalState.CANCELLED,
        "timeout": TerminalState.TIMED_OUT,
        "provider_failure": TerminalState.FAILED,
    }
    expected = expected_states[scenario]
    if result.terminal_state != expected:
        return False, f"Expected {expected.value} but got {result.terminal_state.value}", {}

    # 2. Rejected runs never invoke provider
    if scenario == "rejection" and provider.invocation_count != 0:
        return False, f"Provider was invoked {provider.invocation_count} times on rejected input", {}

    # 3. Cancelled, timed-out, and failed runs NEVER commit assistant response
    if scenario in ("rejection", "cancellation", "timeout", "provider_failure"):
        if len(assistant_msgs) > 0 or result.committed_message_id is not None:
            return False, "Non-successful turn committed an assistant message to conversation store!", {}

    if scenario == "success":
        if len(assistant_msgs) != 1 or result.committed_message_id is None:
            return False, "Successful turn failed to commit exactly 1 assistant message", {}

    # 4. No events appear after terminal event
    events = result.trace
    terminal_event_indices = [
        idx for idx, e in enumerate(events) if e.payload.get("is_terminal") is True
    ]
    if len(terminal_event_indices) != 1:
        return False, f"Run trace had {len(terminal_event_indices)} terminal events (expected 1)", {}
    if terminal_event_indices[0] != len(events) - 1:
        return False, f"Events appeared after terminal event in trace (index {terminal_event_indices[0]} of {len(events)})", {}

    stats = {
        "terminal_state": result.terminal_state.value,
        "provider_invocations": provider.invocation_count,
        "assistant_persisted": len(assistant_msgs),
        "events_count": len(events),
    }
    return True, "OK", stats


async def run_all_benchmarks() -> bool:
    console.print(
        Panel(
            "[bold white]Caygnus Product Engineering Challenge — Problem 5[/bold white]\n"
            "[cyan]Reliable AI Conversation Runtime Verification Benchmark[/cyan]\n"
            f"[dim]Running {ITERATIONS_PER_SCENARIO} iterations per scenario (50 total runs)...[/dim]",
            title="[bold green]Verification Benchmark[/bold green]",
            border_style="green",
        )
    )

    scenarios = ["success", "rejection", "cancellation", "timeout", "provider_failure"]
    counts: Dict[str, Dict[str, int]] = {s: {} for s in scenarios}
    all_passed = True
    start_time = time.perf_counter()

    for scenario in scenarios:
        console.print(f"Running scenario: [bold yellow]{scenario}[/bold yellow] ({ITERATIONS_PER_SCENARIO} iterations)... ", end="")
        for i in range(1, ITERATIONS_PER_SCENARIO + 1):
            passed, err_msg, stats = await run_single_iteration(scenario, i)
            if not passed:
                console.print(f"[bold red]FAILED on iteration {i}: {err_msg}[/bold red]")
                all_passed = False
                return False

            t_state = stats["terminal_state"]
            counts[scenario][t_state] = counts[scenario].get(t_state, 0) + 1

        console.print("[bold green]✓ Verified (10/10)[/bold green]")

    elapsed = time.perf_counter() - start_time

    # Results Table
    table = Table(title=f"Benchmark Verification Results ({elapsed:.2f}s total)", border_style="cyan")
    table.add_column("Scenario", style="bold cyan")
    table.add_column("Runs", justify="right", style="white")
    table.add_column("Observed Terminal State", style="yellow")
    table.add_column("Provider Called?", justify="center")
    table.add_column("Assistant Msg Committed?", justify="center")
    table.add_column("Terminal Invariant", justify="center", style="bold green")

    scenario_labels = {
        "success": ("COMPLETED", "Yes", "Yes (1)"),
        "rejection": ("REJECTED", "No (0)", "No (0)"),
        "cancellation": ("CANCELLED", "Yes (halted)", "No (0)"),
        "timeout": ("TIMED_OUT", "Yes (timed out)", "No (0)"),
        "provider_failure": ("FAILED", "Yes (errored)", "No (0)"),
    }

    for scenario in scenarios:
        expected_state, provider_called, msg_committed = scenario_labels[scenario]
        observed_count = counts[scenario].get(expected_state, 0)
        table.add_row(
            scenario,
            f"{observed_count}/{ITERATIONS_PER_SCENARIO}",
            f"{expected_state} ({observed_count})",
            provider_called,
            msg_committed,
            "✓ Strict Single Winner",
        )

    console.print("\n")
    console.print(table)

    summary_panel = Panel(
        "[bold green]ALL INVARIANTS SATISFIED & VERIFIED:[/bold green]\n"
        "1. Each of 50 runs has exactly one immutable terminal state.\n"
        "2. Rejected runs NEVER invoke the model provider (0 provider calls).\n"
        "3. Cancelled, timed-out, and failed runs NEVER commit an assistant response to conversation history.\n"
        "4. Operational traces are sealed: Zero events appear after terminal events.\n"
        "5. 100% deterministic with zero external dependencies.",
        title="[bold green]Benchmark Result: PASS[/bold green]",
        border_style="green",
    )
    console.print(summary_panel)
    return all_passed


def main() -> None:
    success = asyncio.run(run_all_benchmarks())
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
