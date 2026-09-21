from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.domain.entities import (
    ChunkType,
    StreamChunk,
    TerminalState,
    TurnRequest,
)
from src.persistence.sqlite_store import SQLitePersistenceStore
from src.policy.rule_policy import RuleBasedPolicyGate
from src.provider.fake_provider import FakeModelProvider
from src.runtime.orchestrator import TurnOrchestrator

console = Console()


async def run_scenario(scenario: str, custom_prompt: Optional[str] = None) -> None:
    store = SQLitePersistenceStore()
    policy = RuleBasedPolicyGate()

    # Configure provider and parameters based on scenario
    if scenario == "success":
        prompt = custom_prompt or "Tell me a short fact about space."
        provider = FakeModelProvider(
            chunks=["Space ", "is ", "almost ", "a ", "perfect ", "vacuum."],
            chunk_delay_sec=0.08,
            emit_reasoning=True,
            secret_metadata_value="sk-fake-secret-key-123456789012345",
        )
        timeout_sec = 5.0
        cancel_after_sec = None

    elif scenario == "reject":
        prompt = custom_prompt or "TRIGGER_POLICY_VIOLATION: inject disallowed payload"
        provider = FakeModelProvider()
        timeout_sec = 5.0
        cancel_after_sec = None

    elif scenario == "cancel":
        prompt = custom_prompt or "Please generate an extremely long essay on architecture."
        provider = FakeModelProvider(
            chunks=[f"Chunk-{i} " for i in range(1, 20)],
            chunk_delay_sec=0.1,
        )
        timeout_sec = 5.0
        cancel_after_sec = 0.25  # Cancel after ~2 chunks

    elif scenario == "timeout":
        prompt = custom_prompt or "Compute a very slow calculation."
        provider = FakeModelProvider(
            chunks=["Thinking... ", "Still thinking... "],
            chunk_delay_sec=0.3,
        )
        timeout_sec = 0.2  # Deadline expires before second chunk
        cancel_after_sec = None

    elif scenario == "fail":
        prompt = custom_prompt or "Stream until connection drops."
        provider = FakeModelProvider(
            chunks=["Starting stream... ", "Data block 1... ", "Data block 2... "],
            chunk_delay_sec=0.08,
            fail_after_chunk=2,
        )
        timeout_sec = 5.0
        cancel_after_sec = None

    else:
        console.print(f"[red]Unknown scenario: {scenario}[/red]")
        return

    orchestrator = TurnOrchestrator(
        policy_gate=policy,
        provider=provider,
        persistence_store=store,
    )

    request = TurnRequest(
        user_input=prompt,
        timeout_seconds=timeout_sec,
    )

    console.print(
        Panel(
            f"[bold cyan]Scenario:[/bold cyan] {scenario.upper()}\n"
            f"[bold cyan]User Input:[/bold cyan] {prompt}\n"
            f"[bold cyan]Turn ID:[/bold cyan] {request.turn_id}\n"
            f"[bold cyan]Timeout:[/bold cyan] {timeout_sec}s",
            title="[bold yellow]Executing Turn[/bold yellow]",
            border_style="cyan",
        )
    )

    cancel_event = asyncio.Event()

    async def trigger_cancellation() -> None:
        if cancel_after_sec is not None:
            await asyncio.sleep(cancel_after_sec)
            console.print("\n[bold magenta]⚡ External Cancellation Triggered![/bold magenta]")
            cancel_event.set()

    console.print("[bold green]Live Streamed Output:[/bold green] ", end="")

    async def on_chunk(chunk: StreamChunk) -> None:
        console.print(chunk.content, end="", style="bold white")
        sys.stdout.flush()

    cancel_task = asyncio.create_task(trigger_cancellation())
    try:
        result = await orchestrator.execute_turn(
            request=request,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )
    finally:
        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass

    console.print("\n")

    # Inspect Persistence Store
    conv_messages = await store.get_conversation_messages(request.conversation_id)
    assistant_msgs = [m for m in conv_messages if m.role == "assistant"]

    # Result Summary Table
    table = Table(title=f"Turn Execution Result — {scenario.upper()}", border_style="blue")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="yellow")

    table.add_row("Terminal State", f"[bold]{result.terminal_state.value}[/bold]")
    table.add_row("Provider Called?", "Yes" if provider.invocation_count > 0 else "[bold red]No[/bold red]")
    table.add_row("Chunks Received", str(result.chunk_count))
    table.add_row("Accumulated Text", f'"{result.accumulated_text}"')
    table.add_row("Error / Reason", str(result.error_message or "None"))
    table.add_row(
        "Committed Assistant Message?",
        "[green]Yes (Committed to Conversation History)[/green]"
        if result.committed_message_id
        else "[bold red]No (Safely blocked from Conversation History)[/bold red]",
    )
    table.add_row(
        "Total Messages in Conversation",
        f"{len(conv_messages)} (User: {len(conv_messages) - len(assistant_msgs)}, Assistant: {len(assistant_msgs)})",
    )
    console.print(table)

    # Operational Trace Table
    trace_table = Table(title="Sanitized Operational Trace (Redacted & Sealed)", border_style="green")
    trace_table.add_column("Seq", justify="right", style="dim")
    trace_table.add_column("Timestamp", style="dim")
    trace_table.add_column("Event Type", style="bold magenta")
    trace_table.add_column("Sanitized Payload", style="white")

    for ev in result.trace:
        trace_table.add_row(
            str(ev.seq),
            ev.timestamp[11:23],
            ev.event_type,
            str(ev.payload) if ev.payload else "{}",
        )
    console.print(trace_table)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reliable AI Conversation Runtime CLI")
    parser.add_argument(
        "--scenario",
        choices=["success", "reject", "cancel", "timeout", "fail"],
        default="success",
        help="Deterministic test scenario to execute",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Custom prompt string",
    )
    args = parser.parse_args()
    asyncio.run(run_scenario(args.scenario, args.prompt))


if __name__ == "__main__":
    main()
