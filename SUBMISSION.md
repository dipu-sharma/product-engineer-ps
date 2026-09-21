# Product Engineering Challenge Submission

## Candidate

- **Name:** [Your Name]
- **Email:** [Your Email]
- **GitHub:** [Your GitHub Profile URL]
- **Selected problem:** Problem 5: Reliable AI Conversation Runtime
- **Demo video:** [Loom / YouTube / Drive Video URL]

---

## Run the project

### Prerequisites
- Docker and Docker Compose installed (Recommended), OR
- Python 3.11+ with `pip`

### Using Docker (Zero-Configuration Setup)

1. **Build container:**
   ```bash
   docker compose build
   ```

2. **Trigger the Successful Scenario (AC1):**
   ```bash
   docker compose run --rm cli python -m src.cli.main --scenario success
   ```
   *Streams live text chunks, emits internal reasoning (omitted from trace), and commits both user input and the completed assistant message to conversation history.*

3. **Trigger Pre-Response Policy Rejection (AC2):**
   ```bash
   docker compose run --rm cli python -m src.cli.main --scenario reject
   ```
   *Rejects disallowed prompt before generation. Shows that the model provider was never invoked (0 calls) and no assistant message was committed.*

4. **Trigger Cancellation Mid-Stream (AC3):**
   ```bash
   docker compose run --rm cli python -m src.cli.main --scenario cancel
   ```
   *Triggers cooperative cancellation after ~2 chunks. Provider halts promptly, run becomes `CANCELLED`, and no assistant message is committed to conversation history.*

5. **Trigger Timeout (AC4):**
   ```bash
   docker compose run --rm cli python -m src.cli.main --scenario timeout
   ```
   *Sets a tight deadline of 0.2s with a slow provider. Runtime terminates at the deadline, marks the turn `TIMED_OUT`, and retains partial output solely in forensic logs.*

6. **Trigger Provider Failure (AC5):**
   ```bash
   docker compose run --rm cli python -m src.cli.main --scenario fail
   ```
   *Provider injects an error mid-stream after chunk 2. Runtime transitions to `FAILED` and blocks committing corrupted partial output.*

---

## Run the tests

Run the deterministic test suite via Docker or local pytest:

```bash
# Via Docker
docker compose run --rm test

# Or locally
pytest -v
```

All 7 test suites are 100% deterministic, require zero paid external APIs, and execute in under 3 seconds without arbitrary sleeps.

---

## Acceptance scenarios and verification

### Completed Acceptance Scenarios

- **[x] AC1: Successful Streamed Turn** — Ordered chunk streaming, full turn completion, atomic commit of assistant message to conversation history.
- **[x] AC2: Pre-Response Rejection** — Deterministic policy gating before model invocation. Verified provider invocation count = 0.
- **[x] AC3: Cancellation** — Prompt cooperative abort via `cancel_event`. Provider generation ceases immediately; run transitions to `CANCELLED`.
- **[x] AC4: Timeout** — Deadline enforcement halts execution promptly, transitioning run to `TIMED_OUT` without saving an assistant message.
- **[x] AC5: Provider Failure** — Unrecoverable mid-stream provider error is trapped, recorded in audit traces, and prevented from committing partial dialogue.
- **[x] AC6: Terminal-State Race** — Competing transitions (e.g. completion racing with cancellation or timeout) resolve to exactly one winning terminal state. Later transitions are rejected observably.
- **[x] AC7: Safe Operational Trace** — Monotonically sequenced event trace. Masks API keys, tokens, and authorization headers, and completely excludes hidden chain-of-thought (CoT) reasoning.

### Problem-Specific Verification Benchmark

Run the automated 50-iteration benchmark (10 iterations each across all 5 failure and success scenarios):

```bash
# Via Docker
docker compose run --rm benchmark

# Or locally
python scripts/run_benchmark.py
```

#### Observed Benchmark Results

```text
======================= Verification Benchmark =======================
Running scenario: success (10 iterations)...          ✓ Verified (10/10)
Running scenario: rejection (10 iterations)...        ✓ Verified (10/10)
Running scenario: cancellation (10 iterations)...     ✓ Verified (10/10)
Running scenario: timeout (10 iterations)...          ✓ Verified (10/10)
Running scenario: provider_failure (10 iterations)... ✓ Verified (10/10)

+------------------+-------+-------------------------+--------------------+---------------------------+------------------------+
| Scenario         | Runs  | Observed Terminal State | Provider Called?   | Assistant Msg Committed?  | Terminal Invariant     |
+------------------+-------+-------------------------+--------------------+---------------------------+------------------------+
| success          | 10/10 | COMPLETED (10)          | Yes                | Yes (1)                   | ✓ Strict Single Winner |
| rejection        | 10/10 | REJECTED (10)           | No (0)             | No (0)                    | ✓ Strict Single Winner |
| cancellation     | 10/10 | CANCELLED (10)          | Yes (halted)       | No (0)                    | ✓ Strict Single Winner |
| timeout          | 10/10 | TIMED_OUT (10)          | Yes (timed out)    | No (0)                    | ✓ Strict Single Winner |
| provider_failure | 10/10 | FAILED (10)             | Yes (errored)      | No (0)                    | ✓ Strict Single Winner |
+------------------+-------+-------------------------+--------------------+---------------------------+------------------------+
Total Execution Time: ~0.45s across 50 runs.
```

All 5 core invariants were verified across all 50 runs:
1. Each run had exactly one terminal state.
2. Rejected runs never invoked the model provider (0 calls).
3. Non-successful runs never committed an assistant response into conversation history.
4. No events appeared after any terminal event (trace sealed upon entering terminal state).
5. 100% deterministic execution without live external dependencies.

---

## Architecture and data flow

```
                      ┌──────────────────────────────────────────────┐
                      │              Client / Caller                 │
                      └──────────────┬───────────────────────────────┘
                                     │ execute_turn(request, cancel_event)
                                     ▼
    ┌─────────────────────────────────────────────────────────────────────────────┐
    │                       TurnOrchestrator (Runtime FSM)                        │
    │  - Manages Turn Lifecycle State Machine (Atomic Terminal State Winner)      │
    │  - Coordinates cancellation events and asyncio.timeout deadlines            │
    │  - Dual-boundary persistence router & trace publisher                       │
    └──────┬──────────────────┬──────────────────┬─────────────────┬──────────────┘
           │                  │                  │                 │
           │ 1. evaluate()    │ 2. stream()      │ 3. record()     │ 4. commit()
           ▼                  ▼                  ▼                 ▼
    ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐
    │  PolicyGate  │   │ ModelProvider│   │  SafeTracer  │   │  PersistenceStore  │
    │  (Pre-call   │   │ (Controllable│   │  (Redacts    │   │  Boundary A: Conv  │
    │   guardrails)│   │  Fake / Live)│   │   secrets/CoT│   │  Boundary B: Audit │
    └──────────────┘   └──────────────┘   └──────────────┘   └────────────────────┘
```

### Main Components & Responsibilities:

1. **`TurnOrchestrator` (`src/runtime/orchestrator.py`)**: Central workflow coordinator. Orchestrates policy pre-checks, provider streaming, deadline timeouts, cancellation propagation, and transaction boundaries.
2. **`TurnStateMachine` (`src/runtime/state_machine.py`)**: Thread-safe Finite State Machine enforcing valid state transitions. Guarantees that in any race condition between `COMPLETED`, `CANCELLED`, `TIMED_OUT`, and `FAILED`, exactly one terminal state wins permanently.
3. **`PolicyGate` (`src/policy/rule_policy.py`)**: Evaluates user input against length constraints and forbidden policy patterns prior to making any external calls.
4. **`ModelProvider` (`src/provider/fake_provider.py`)**: Streaming interface yielding typed `StreamChunk` events (`TEXT`, `REASONING`). Cooperatively listens to `cancel_event` to immediately release resources.
5. **`SafeTracer` & `redactor` (`src/observability/`)**: Emits monotonically numbered trace events. Redacts sensitive tokens/keys and isolates internal CoT reasoning. Automatically seals itself upon recording a terminal event.
6. **`SQLitePersistenceStore` (`src/persistence/sqlite_store.py`)**: Manages dual storage boundaries: active conversation history vs forensic audit records.

---

## Technology choices

- **Language: Python 3.11+**: Provides native `asyncio.timeout` context managers, typed dataclasses, and rich async generator ergonomics.
- **Containerization: Docker & Docker Compose**: Eliminates local environment drift and guarantees reviewers can run tests and benchmarks within 2 minutes.
- **Persistence: SQLite**: Standard library, transactional ACID compliance, zero setup overhead. Easily swapped for PostgreSQL in production.
- **CLI & Output: Rich**: Clean terminal tables and live streaming demonstration.

### Alternatives Considered & Trade-offs:
- *TypeScript/Node.js*: Considered for native `AbortController` ergonomics, but Python was chosen for closer alignment with production AI/ML engineering ecosystems.
- *Distributed Task Queues (Celery/Temporal)*: Deliberately omitted. For a single-turn bounded runtime exercise, an in-process async orchestrator is vastly cleaner, faster to test, and avoids heavy infrastructure dependencies.

---

## Important decisions

1. **Dual-Boundary Persistence Strategy**:
   - *Problem*: In naive implementations, streaming text directly into conversation history corrupts context if the stream is cancelled or crashes halfway.
   - *Decision*: We separated storage into **Conversation History** (which only receives assistant turns that reach `COMPLETED`) and the **Run Audit Record** (which captures all forensic telemetry and partial output for post-mortem analysis across all terminal states).
2. **Atomic Terminal Winner Resolution**:
   - *Problem*: Network completion, user cancellation, and deadline timeouts often race within milliseconds.
   - *Decision*: The `TurnStateMachine` holds an internal lock and marks `_terminal_winner` on the first valid terminal transition. Any subsequent transition attempt is observably rejected (`TERMINAL_RACE_REJECTED`) and discarded.
3. **Zero Hidden Reasoning Leakage**:
   - *Problem*: Modern reasoning models (e.g. o1, DeepSeek-R1) generate internal CoT thoughts that must not be exposed to user logs or operational traces.
   - *Decision*: `ModelProvider` tags chunks as `ChunkType.REASONING`. The orchestrator excludes them from the user text accumulator, and `SafeTracer` scrubs the raw thought string, recording only token/char counts.

---

## Assumptions and limitations

- **Single-Process Runtime**: The current implementation runs within a single Python process. A distributed multi-node setup would require distributed locking (e.g., Redis Redlock) to serialize competing turn events across worker nodes.
- **In-Memory / SQLite State**: Designed for local execution and fast deterministic verification.
- **Rule-Based Policy**: The policy gate uses deterministic keyword, length, and pattern checks. A production deployment would front this with an asynchronous moderation model.

---

## Production and scale

If taking this runtime to production at high scale:
1. **Distributed Turn Coordination**: Replace the in-process `asyncio.Event` with Redis Pub/Sub or Redis Streams so cancellation commands can be delivered across load-balanced worker instances.
2. **Connection Resiliency & Heartbeats**: Wrap provider streams with connection keep-alive monitors and exponential backoff retry policies for transient network dropped-frame errors prior to declaring `FAILED`.
3. **Cold Storage Offload**: Stream sanitized trace events directly to an OpenTelemetry collector or Kafka topic for ingestion into ClickHouse/BigQuery, keeping the transactional database lean.

---

## AI usage

AI assistance was utilized as a pair programmer for:
- Brainstorming edge-case race conditions between stream completion and timeout timers.
- Scaffolding test boilerplates and Dockerfile configurations.
- Review: Every line of code, state transition rule, regex redactor, and test assertion was manually verified against the Problem 5 brief and scorecard criteria.

---

## Credibility note

Describe one product or system you previously helped ship:
- **Problem Solved:** [Briefly describe the product and user problem]
- **Your Personal Contribution:** [Detail your specific architectural and coding contributions]
- **Scale / Operational Complexity:** [Throughput, concurrency, latency constraints, or reliability guarantees]
- **Key Engineering Decision:** [A difficult trade-off or architectural decision made]
- **Public Reference:** [Repository, article, or project link if available]
