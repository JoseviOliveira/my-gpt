# MCP-Based Benchmark Framework — Analysis & Migration Plan

> **Date**: 2026-03-30  
> **Scope**: Refactor the my-gpt LLM benchmark from a monolithic Python runner into modular MCP (Model Context Protocol) servers — a structural decomposition, not an agentic redesign.  
> **Status**: Proposal — open for discussion.

---

## 1. Current Architecture — What We Have

### 1.1 Codebase overview

The benchmark is a **monolithic Python runner** totalling **5,335 lines** across 15 source files, organized via mixins:

| File | Lines | Methods | Role |
|---|---|---|---|
| `runner.py` | 841 | 21 | Orchestration: sequential model→dataset→sample loop, resume, scope building |
| `tasks.py` | 1,019 | 23 | Prompt construction, inline grading pipeline, CoT/format overrides, LLM judge |
| `telemetry.py` | 1,362 | 49 | GPU/CPU/disk sampling, thermal guards, cooldowns, request watchdog, Ollama calls |
| `db.py` | 772 | 34 | SQLite persistence: runs, tasks, samples, state, scope, resume |
| `cli.py` | 122 | 3 | Argument parsing, logging setup |
| `evaluators/` (7 files) | 728 | — | Modular evaluators: MC, exact match, code exec, QA, retrieval, instruction, chat |
| `db_schema.sql` | 254 | — | 12 tables: runs, scope, state, models, tasks, samples, dialogs, turns |

Supporting files: 4 YAML configs, `robustness_defaults.yaml`, shell launcher, 8 JSONL datasets.

### 1.2 Data flow (current)

```
config_full.yaml
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                    BenchmarkRunner (Host)                     │
│                                                              │
│  __init__()  ─── load config, merge defaults, init DB,       │
│                  build scope, check resume, init evaluators   │
│                                                              │
│  run()  ─── for each model:                                  │
│              │  warmup_model()                                │
│              │  for each dataset:                             │
│              │    for each sample:                            │
│              │      ├─ gpu_guard_wait()                       │
│              │      ├─ ResourceSampler.start()                │
│              │      ├─ RequestWatchdog.start()                │
│              │      ├─ call_ollama_with_metrics()  ──► App    │
│              │      ├─ ResourceSampler.stop()                 │
│              │      ├─ evaluate(response, expected)           │
│              │      ├─ record_sample(DB)                      │
│              │      └─ apply_cooldown()                       │
│              │  unload_model()                                │
│              │  inter_model_cooldown()                        │
│              └─ record_run_complete(DB)                       │
│                                                              │
│  Mixins: TaskMixin + TelemetryMixin + DBMixin                │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
   benchmark.db  ──►  Dashboard HTML (via /api/benchmark/*)
```

### 1.3 Identified pain points

| # | Pain point | Where in code | Root cause |
|---|---|---|---|
| P1 | **Server warm-up** is a single hardcoded prompt | `runner.py` `_warmup_model()` | No configurable warm-up protocol, no multi-round or thermal-aware warm-up |
| P2 | **Thermal stability** logic is entangled with the request loop | `telemetry.py` (1,362 lines — largest file) | Guards, cooldowns, and sampling woven into request flow; can't tune independently |
| P3 | **Pre-prompting / CoT handling** uses fragile heuristics | `tasks.py` `_format_override_prompt()` (~60 lines of if/else) | No concept of model capabilities; string-matching for evaluator types |
| P4 | **Mixing CoT & non-CoT models** — static mode override | `tasks.py` `_mode_override_for_task()` | Returns `"fast"` for academic tasks, ignoring whether model actually supports CoT |
| P5 | **Answer evaluation** — hybrid modular + inline | `tasks.py` `_evaluate_with_grading()` (100+ lines) + 7 evaluator classes | Grading rules parsed inline with string slicing; evaluators are modular but disconnected from the grading pipeline |
| P6 | **Report generation** — non-existent in runner | Reports live exclusively in `static/docs/` HTML pages | No programmatic report generation; no scratch-to-report automation |
| P7 | **Fitness formula** — doesn't exist | — | TTFT, throughput, accuracy, temperature are stored in DB but never composed into a single quality score |
| P8 | **Resume logic** — 150+ lines of complex state management | `runner.py` `__init__()` lines 95-200 | State restoration, scope comparison, step offsets — all interleaved with initialization |

---

## 2. Why MCP Fits This Problem

### 2.1 Core argument

This is a **modular refactoring** — not an agentic redesign. Every MCP server is a deterministic Python process. No agents, no LLM-driven orchestration, no autonomous planning. The only LLM inference in the system is the existing LLM-as-judge for subjective grading (already implemented in `_llm_judge()`), plus targeted extensions for a few brittle heuristics in the evaluation pipeline (see §4.6).

MCP is the decomposition protocol because the benchmark's problems are **structural coupling problems**:

- **Thermal management** should be an independent service, not a mixin.
- **Evaluation** should be callable from the runner, the report generator, or a CLI — not locked inside the request loop.
- **Fitness scoring** is a new capability that needs composition of existing data — classic tool-use.
- **Report generation** needs access to DB + evaluation + fitness — cross-cutting concern.

MCP provides:
- **Standard tool interface**: servers declare tools, clients call them — replacing mixin inheritance with explicit APIs.
- **Process isolation**: each server is independently testable and deployable.
- **Resource access**: benchmark.db, config files, and datasets become addressable resources.
- **Composability**: the orchestrator script chains tools across servers via a standard protocol.

### 2.2 What MCP is NOT solving here

- MCP doesn't make inference faster — Ollama call latency is the same.
- MCP doesn't replace the need for good evaluation logic — it just makes it accessible.
- MCP doesn't add agentic behavior — every server is a deterministic script.
- MCP is not needed for the sake of "being modern" — the justification is architectural decomposition.

---

## 3. Target Architecture

### 3.1 MCP Server decomposition

```
┌─────────────────────────────────────────────────────────────────┐
│              Benchmark Orchestrator (MCP Host / Client)          │
│                                                                 │
│  Deterministic Python script with sequential model→dataset→     │
│  sample loop — same logic as current runner.py, calling MCP     │
│  tools instead of mixin methods.                                │
└──────┬──────────┬──────────────┬───────────────┬────────────────┘
       │          │              │               │
┌──────▼───────┐ ┌▼────────────┐ ┌▼─────────────┐ ┌▼──────────────┐
│  Inference   │ │  Telemetry  │ │  Evaluation   │ │  Reporter     │
│  MCP Server  │ │  MCP Server │ │  MCP Server   │ │  MCP Server   │
│              │ │             │ │               │ │               │
│ Tools:       │ │ Tools:      │ │ Tools:        │ │ Tools:        │
│ • warmup     │ │ • gpu_temp  │ │ • evaluate    │ │ • fitness     │
│ • infer      │ │ • cpu_util  │ │ • llm_judge   │ │ • report      │
│ • unload     │ │ • disk_io   │ │ • grade       │ │ • compare     │
│ • list_models│ │ • gpu_guard │ │               │ │ • export      │
│ • capabilities││ • thermal_  │ │ Resources:    │ │               │
│              │ │   wait      │ │ • evaluator   │ │ Resources:    │
│              │ │ • sample    │ │   registry    │ │ • benchmark.db│
│              │ │ • watchdog  │ │               │ │ • run configs │
│              │ │             │ │               │ │               │
│ Resources:   │ │ Resources:  │ └───────────────┘ └───────────────┘
│ • ollama     │ │ • hw probes │
│   endpoint   │ │             │
└──────────────┘ └─────────────┘

         ┌──────────────┐
         │  Data         │
         │  MCP Server   │
         │              │
         │ Tools:       │
         │ • load_config│
         │ • load_samples│
         │ • pending_tasks│
         │ • record_result│
         │ • update_state│
         │              │
         │ Resources:   │
         │ • config YAML│
         │ • *.jsonl    │
         │ • benchmark.db│
         └──────────────┘
```

### 3.2 MCP Server inventory

| Server | Tools | Resources | Replaces |
|---|---|---|---|
| **Inference** | `warmup_model`, `infer`, `infer_streaming`, `unload_model`, `list_models`, `get_model_capabilities` | Ollama endpoint config | `_call_ollama*`, `_warmup_model`, `_unload_model` (runner.py) |
| **Telemetry** | `read_gpu_temp`, `read_cpu_util`, `read_disk_io`, `gpu_guard_wait`, `thermal_wait`, `sample_start`, `sample_stop`, `watchdog_start`, `watchdog_check`, `apply_cooldown` | Hardware probe endpoints | `TelemetryMixin` (1,362 lines), `ResourceSampler`, `RequestWatchdog` |
| **Evaluation** | `evaluate(type, response, expected, grading?)`, `llm_judge(question, response, expected, grading)`, `list_evaluators` | Evaluator registry | `evaluators/` (728 lines) + `_evaluate_with_grading` (tasks.py) |
| **Data** | `load_config`, `load_dataset`, `get_pending_tasks`, `record_sample`, `record_task`, `record_run_start`, `record_run_complete`, `update_run_state`, `get_run_state` | config YAML, JSONL datasets, benchmark.db | `DBMixin` (772 lines), config/dataset loading (runner.py) |
| **Reporter** | `compute_fitness`, `generate_report`, `compare_runs`, `export_csv`, `get_leaderboard` | benchmark.db (read-only) | **New** — does not exist today |

### 3.3 Example orchestration flow (pseudocode)

```python
# Phase 1: Setup
config = data.load_config("benchmark/config_full.yaml")
run_id = data.record_run_start(config)

for model in config.models:
    # Phase 2: Warm-up
    caps = inference.get_model_capabilities(model.name)
    telemetry.thermal_wait(target_temp=50)
    inference.warmup_model(model.name, strategy=caps.warmup_strategy)

    for dataset in config.datasets:
        samples = data.load_dataset(dataset.path)
        pending = data.get_pending_tasks(run_id, model.name, dataset.name)

        for sample in pending:
            # Phase 3: Pre-inference thermal guard
            telemetry.gpu_guard_wait(threshold=config.thermal.gpu_guard)

            # Phase 4: Inference with telemetry
            telemetry_session = telemetry.sample_start(interval=3.0)
            prompt = build_prompt(sample, caps)  # CoT-aware
            response, metrics = inference.infer(model.name, prompt, config.decoding)
            resource_stats = telemetry.sample_stop(telemetry_session)

            # Phase 5: Evaluation
            result = evaluation.evaluate(
                type=dataset.evaluator,
                response=response,
                expected=sample.expected,
                grading=sample.grading
            )

            # Phase 6: Record
            data.record_sample(run_id, model.name, sample, response, metrics, result, resource_stats)

            # Phase 7: Thermal cooldown
            telemetry.apply_cooldown(hardness=sample.hardness, tokens=metrics.output_tokens)

    inference.unload_model(model.name)
    telemetry.thermal_wait(target_temp=45)  # inter-model cooldown

# Phase 8: Scoring & Reporting
data.record_run_complete(run_id)
fitness = reporter.compute_fitness(run_id, weights=config.fitness_weights)
reporter.generate_report(run_id, format="html", output="static/docs/")
```

---

## 4. Design Decisions — Open for Discussion

### 4.1 Orchestrator design: Deterministic script ✓

**Decision: Script orchestrator only. No LLM agent orchestrator.**

Code analysis confirms that the orchestration loop (model → dataset → sample) is 100% deterministic:
- Task order comes from config YAML
- Resume state is exact tuple matching in the DB
- Thermal guards are threshold-based polling loops
- No step in the flow requires judgment, planning, or reasoning

An LLM agent orchestrator would add latency (~100-500ms per step), non-determinism, and debugging opacity — with zero benefit, since there are no decisions to make. The "cool factor" of asking an LLM to drive a benchmark loop does not justify the cost.

> **Note**: If ad-hoc queries ("which model had the best TTFT on logic_deduction?") are needed in the future, they can be built as a separate CLI/chat tool that reads from benchmark.db — not as an agentic orchestrator.

### 4.2 Transport: stdio vs. SSE (HTTP)

| Aspect | **stdio** | **SSE (Streamable HTTP)** |
|---|---|---|
| **Latency** | Lowest (~sub-ms IPC) | ~1-5ms per call (HTTP overhead) |
| **Deployment** | Single machine only; servers are child processes | Can run servers on different machines |
| **Concurrency** | One client per server instance | Multiple clients can connect |
| **Dashboard integration** | Needs a bridge to expose metrics to web UI | Dashboard can call MCP servers directly via HTTP |
| **Debugging** | Harder to inspect wire protocol | Easy — standard HTTP tools (curl, browser) |
| **MCP SDK support** | Full support in Python SDK | Full support in Python SDK |

**Recommendation**: **stdio for Phase 1-2** (simpler, no port management). **Migrate Telemetry and Reporter to SSE in Phase 3** when the live dashboard needs direct access.

> ⚠️ **Open question**: The current dashboard reads from `/api/benchmark/status` (Flask endpoint). Should the dashboard talk directly to MCP servers via SSE, or should we keep the Flask API as a thin proxy over MCP? The proxy approach is simpler but adds a layer; direct SSE is cleaner but requires dashboard changes.

### 4.3 Server granularity: 5 servers vs. fewer

| Aspect | **5 servers** (as proposed in §3.2) | **3 servers** (merged) | **2 servers** (minimal) |
|---|---|---|---|
| **Split** | Inference / Telemetry / Evaluation / Data / Reporter | Inference+Telemetry / Evaluation+Data / Reporter | Engine (Inference+Telemetry+Data) / Analysis (Evaluation+Reporter) |
| **Isolation** | Maximum — each concern independent | Thermal & inference tightly coupled (natural) | Minimal separation |
| **Process count** | 5 child processes + orchestrator | 3 + orchestrator | 2 + orchestrator |
| **Complexity** | More config, more IPC | Balanced | Simplest to deploy |
| **Independent scaling** | Can upgrade telemetry without touching inference | Partial | Almost none |

**Recommendation**: **Start with 3 servers** (merged variant), then split Inference from Telemetry and Data from Reporter when the coupling becomes a pain point.

> ⚠️ **Open question**: Should the LLM judge live in the Evaluation server or the Inference server? It uses inference (Ollama call) but serves evaluation. Putting it in Evaluation keeps the Inference server "pure" (only benchmarked models). Putting it in Inference avoids duplicating Ollama call logic.

### 4.4 Fitness formula design

The fitness formula is entirely new functionality. Key design questions:

```
fitness(model, run_id) = Σ wᵢ · normalize(metricᵢ)
```

| Metric | Source | Normalization | Suggested default weight |
|---|---|---|---|
| **Accuracy** | evaluation results | % correct across all tasks | 0.35 |
| **TTFT** | inference metrics | Inverse-normalized: lower is better; ms scale | 0.15 |
| **Throughput** (tok/s) | inference metrics | Linear; higher is better | 0.15 |
| **GPU temperature** | telemetry | Inverse-normalized: lower is better | 0.10 |
| **Task versatility** | evaluation results | Std dev across categories (lower = more versatile) | 0.10 |
| **Instruction compliance** | evaluation results | % of constraint-following tasks passed | 0.10 |
| **Thermal stability** | telemetry | Temp variance during run (lower = more stable) | 0.05 |

> ⚠️ **Open questions**:
> - Should fitness be a single scalar, or a radar chart with per-dimension scores?
> - Should weights be configurable per-run (in YAML), or fixed defaults with CLI overrides?
> - Should we include model size as a normalizing factor (e.g., fitness-per-billion-params) for fair comparison across weight classes?

### 4.5 CoT / non-CoT model handling

| Aspect | **Option A: Capability declaration in config** | **Option B: Auto-detection via probe** |
|---|---|---|
| **How it works** | Config YAML adds `thinking: true/false` per model | Inference server sends a test prompt and detects `<think>` tokens |
| **Accuracy** | 100% if config is correct | May miss unusual CoT formats |
| **Maintenance** | Must update config when adding models | Zero config per model |
| **Latency** | None | One extra inference call per model during warm-up |

**Recommendation**: **Option A as primary** (explicit is better than implicit), with **Option B as fallback** for models not declared in config. The `get_model_capabilities` tool returns the merged result.

> ⚠️ **Open question**: For CoT models, should the benchmark strip thinking tokens before evaluation (current behavior with `_strip_thinking` heuristics), or should evaluation be thinking-aware (score the final answer only, but log thinking for analysis)?

### 4.6 LLM-enhanced evaluation: extending the existing judge pattern

Code analysis confirms that **no MCP server needs to be an agent**. However, the Evaluation server contains 5 heuristics that are brittle enough to benefit from targeted LLM calls — extending the `_llm_judge()` pattern that already exists:

| Heuristic | Current approach | Fragility | LLM improvement |
|---|---|---|---|
| **Tone detection** (`chat.py`) | Keyword lists (`"please"` → formal, `"cool"` → friendly) | 🔴 High | LLM classifies tone semantically in one call |
| **Language detection** (`chat.py`) | Stopword frequency (Spanish: `el, la, de, que, y`) | 🔴 High | LLM identifies language reliably |
| **Fact matching** (`retrieval.py`) | `expected.lower() in response.lower()` — fails on paraphrases | 🔴 High | LLM checks semantic entailment |
| **Task intent classification** (`tasks.py`) | Keyword scan (`"how can"` → procedure task) | ⚠️ Moderate | LLM reads full prompt, classifies intent |
| **Number extraction** (`tasks.py`, `exact_match.py`) | Regex chain with last-number fallback | ⚠️ Moderate | LLM identifies the final answer number |

**Implementation**: These are **not agents**. They are LLM tool calls within the deterministic Evaluation server — same pattern as `_llm_judge()`. The server remains a script; it routes to LLM inference only for the specific sub-tasks where regex/keyword heuristics fail.

**Already correct**: The existing `_llm_judge()` in `tasks.py` properly delegates subjective grading to an LLM. The 5 heuristics above should follow the same pattern — deterministic pipeline with LLM fallback where heuristics are insufficient.

---

## 5. Migration Plan

### 5.0 Prerequisites

- Python MCP SDK: `pip install mcp` (requires Python 3.10+; current venv uses 3.11 ✓)
- No changes to Ollama, Flask app, or dashboard needed until Phase 3
- All phases are backward-compatible: the old `run_benchmark.py` CLI keeps working until explicitly retired

### Phase 1 — Reporter MCP Server (New functionality, zero migration risk)

**Goal**: Build the fitness formula and report generation as a standalone MCP server. Immediate value, no existing code changes.

**Scope**:
| Deliverable | Description |
|---|---|
| `benchmark/mcp/reporter_server.py` | MCP server exposing `compute_fitness`, `generate_report`, `compare_runs`, `export_csv` |
| `benchmark/mcp/reporter_tools.py` | Tool implementations reading from benchmark.db |
| Fitness formula | Configurable weighted composite score (see §4.4) |
| Report templates | HTML + Markdown report generators |
| CLI entry point | `python -m benchmark.mcp.reporter --run-id <ID>` for standalone use |

**MCP Tool definitions**:
```python
@server.tool()
async def compute_fitness(
    run_id: str,
    weights: dict | None = None,  # Override default weights
    normalize: str = "minmax",     # "minmax" | "zscore" | "rank"
) -> dict:
    """Compute composite fitness score for a benchmark run."""

@server.tool()
async def generate_report(
    run_id: str,
    format: str = "html",          # "html" | "markdown" | "json"
    include_fitness: bool = True,
    compare_with: list[str] | None = None,  # Other run_ids for comparison
) -> str:
    """Generate a full benchmark report."""

@server.tool()
async def compare_runs(
    run_ids: list[str],
    metrics: list[str] | None = None,  # Specific metrics to compare
) -> dict:
    """Compare multiple benchmark runs side-by-side."""

@server.tool()
async def export_csv(
    run_id: str,
    scope: str = "summary",        # "summary" | "samples" | "telemetry"
) -> str:
    """Export benchmark data as CSV."""
```

**What changes in the existing codebase**: Nothing. The reporter server reads from benchmark.db (read-only) and is purely additive.

**Acceptance criteria**:
- [ ] `compute_fitness` returns a per-model score for any completed run
- [ ] `generate_report` produces an HTML report comparable to current `benchmark_guided.html`
- [ ] Reporter works standalone via CLI and via MCP protocol (stdio)

---

### Phase 2 — Telemetry MCP Server (Extract the messiest coupling)

**Goal**: Extract `TelemetryMixin` (1,362 lines) + `ResourceSampler` + `RequestWatchdog` into a standalone MCP server.

**Scope**:
| Deliverable | Description |
|---|---|
| `benchmark/mcp/telemetry_server.py` | MCP server exposing thermal management and resource sampling tools |
| `benchmark/mcp/telemetry_tools.py` | Tool implementations (extracted from `telemetry.py`) |
| Thin adapter in `runner.py` | Replace mixin calls with MCP client calls |

**MCP Tool definitions**:
```python
@server.tool()
async def read_gpu_temp() -> dict:
    """Read current GPU temperature in °C."""

@server.tool()
async def read_cpu_util() -> dict:
    """Read current CPU utilization %."""

@server.tool()
async def read_disk_io() -> dict:
    """Read current disk I/O in MB/s."""

@server.tool()
async def gpu_guard_wait(
    threshold: float = 10.0,
    timeout_sec: int = 240,
    poll_sec: int = 3,
) -> dict:
    """Wait until GPU utilization drops below threshold."""

@server.tool()
async def thermal_wait(
    target_temp: float = 50.0,
    timeout_sec: int = 120,
) -> dict:
    """Wait until GPU temperature drops below target."""

@server.tool()
async def sample_start(
    interval_sec: float = 3.0,
) -> str:
    """Start resource sampling session. Returns session_id."""

@server.tool()
async def sample_stop(
    session_id: str,
) -> dict:
    """Stop sampling session and return aggregated resource stats."""

@server.tool()
async def apply_cooldown(
    seconds: float,
    reason: str = "",
) -> dict:
    """Apply a cooldown pause (thermal management)."""
```

**Migration steps**:
1. Create `benchmark/mcp/telemetry_server.py` that wraps existing `TelemetryMixin` methods
2. Add MCP client initialization to `BenchmarkRunner.__init__()`
3. Replace `self._wait_for_gpu_ready()` → `telemetry_client.call_tool("gpu_guard_wait", ...)`
4. Replace `ResourceSampler` start/stop → `telemetry_client.call_tool("sample_start/stop", ...)`
5. Replace `self._apply_cooldown()` → `telemetry_client.call_tool("apply_cooldown", ...)`
6. Remove `TelemetryMixin` inheritance from `BenchmarkRunner`
7. Delete `telemetry.py` (or keep as legacy fallback behind flag)

**What breaks**: Nothing externally. The runner still runs the same way; internal calls are redirected through MCP.

**Acceptance criteria**:
- [ ] All thermal management works through MCP tool calls
- [ ] ResourceSampler equivalent works via `sample_start`/`sample_stop`
- [ ] GPU guard + cooldowns produce identical behavior to current mixin
- [ ] `telemetry.py` can be deleted without affecting benchmark runs
- [ ] Existing benchmark runs are reproducible (same results, same thermal behavior)

---

### Phase 3 — Evaluation + Data MCP Servers (Complete decomposition)

**Goal**: Extract evaluation logic and data/DB access into MCP servers. The runner becomes a thin orchestration loop.

**Scope**:
| Deliverable | Description |
|---|---|
| `benchmark/mcp/evaluation_server.py` | MCP server for all evaluation tools (7 evaluators + grading + LLM judge) |
| `benchmark/mcp/data_server.py` | MCP server for config, datasets, DB reads/writes, resume state |
| Refactored `runner.py` | Thin orchestrator calling MCP tools (~150 lines vs. current 841) |

**Evaluation server tools**:
```python
@server.tool()
async def evaluate(
    evaluator_type: str,     # "multiple_choice" | "exact_match" | "code_execution" | ...
    response: str,
    expected: str | None,
    grading: str | None = None,
    sample: dict | None = None,  # Full sample for grading rules
) -> dict:
    """Unified evaluation entry point. Routes to the appropriate evaluator."""

@server.tool()
async def llm_judge(
    question: str,
    response: str,
    expected: str | None,
    grading: str,
    model: str = "gemma3:4b",
) -> dict:
    """LLM-as-judge evaluation for complex grading rules."""

@server.tool()
async def list_evaluators() -> list[dict]:
    """List available evaluators with their descriptions and grading support."""
```

**Data server tools**:
```python
@server.tool()
async def load_config(config_path: str) -> dict:
    """Load and validate a benchmark config YAML (with robustness defaults merged)."""

@server.tool()
async def load_dataset(dataset_path: str, light_mode: bool = False) -> list[dict]:
    """Load samples from a JSONL dataset file."""

@server.tool()
async def get_pending_tasks(run_id: str, model_name: str | None = None) -> list[dict]:
    """Get tasks that still need execution (resume support)."""

@server.tool()
async def record_sample(run_id: str, data: dict) -> dict:
    """Record a completed sample result to the database."""

@server.tool()
async def record_run_start(config: dict) -> str:
    """Initialize a new benchmark run. Returns run_id."""

@server.tool()
async def record_run_complete(run_id: str) -> dict:
    """Mark run as completed."""

@server.tool()
async def update_run_state(run_id: str, state: dict) -> dict:
    """Update live run state for dashboard monitoring."""
```

**Migration steps**:
1. Build `evaluation_server.py` wrapping existing evaluator classes + `_evaluate_with_grading()`
2. Build `data_server.py` wrapping existing `DBMixin` + config/dataset loading
3. Refactor `runner.py` to use MCP clients for evaluation and data access
4. Remove `TaskMixin` and `DBMixin` inheritance
5. Runner becomes: loop → call inference → call telemetry → call evaluation → call data

**Resulting runner shape** (~150 lines):
```python
class BenchmarkRunner:
    def __init__(self, config_path, **options):
        self.inference = MCPClient("benchmark-inference")
        self.telemetry = MCPClient("benchmark-telemetry")
        self.evaluation = MCPClient("benchmark-evaluation")
        self.data = MCPClient("benchmark-data")
        self.config = self.data.call("load_config", config_path=config_path)

    def run(self):
        run_id = self.data.call("record_run_start", config=self.config)
        for model in self.config["models"]:
            self.telemetry.call("thermal_wait", target_temp=50)
            self.inference.call("warmup_model", model=model["name"])
            for dataset in self.config["datasets"]:
                pending = self.data.call("get_pending_tasks", run_id=run_id, model_name=model["name"])
                for task in pending:
                    self.telemetry.call("gpu_guard_wait")
                    session = self.telemetry.call("sample_start")
                    response, metrics = self.inference.call("infer", model=model["name"], prompt=task["prompt"])
                    resources = self.telemetry.call("sample_stop", session_id=session)
                    result = self.evaluation.call("evaluate", type=dataset["evaluator"], response=response, expected=task["expected"])
                    self.data.call("record_sample", run_id=run_id, data={**task, **metrics, **result, **resources})
                    self.telemetry.call("apply_cooldown", seconds=compute_cooldown(metrics))
            self.inference.call("unload_model", model=model["name"])
        self.data.call("record_run_complete", run_id=run_id)
```

**Acceptance criteria**:
- [ ] Full benchmark run via MCP-decomposed runner produces identical results to monolithic runner
- [ ] Resume works via `get_pending_tasks` (no more in-memory state management)
- [ ] Dashboard continues working (data server writes same DB schema)
- [ ] Old `run_benchmark.py` CLI still works (now thin wrapper over MCP runner)

---

### Phase 4 — Inference MCP Server (Final decomposition)

**Goal**: Extract inference logic into an MCP server, completing the modular decomposition.

**Scope**:
| Deliverable | Description |
|---|---|
| `benchmark/mcp/inference_server.py` | MCP server wrapping Ollama calls, warm-up, model management |
| `benchmark/mcp/config.json` | MCP host configuration declaring all servers |

**Acceptance criteria**:
- [ ] All Ollama calls go through the Inference MCP server
- [ ] Runner is a thin ~150-line orchestration script calling 4-5 MCP servers
- [ ] All MCP servers are independently deployable and testable
- [ ] Full benchmark run produces identical results to the original monolithic runner

---

## 6. File Structure (Target)

```
benchmark/
├── mcp/
│   ├── __init__.py
│   ├── inference_server.py      # MCP server: Ollama calls, warm-up, model mgmt
│   ├── telemetry_server.py      # MCP server: GPU/CPU/disk, thermal, cooldowns
│   ├── evaluation_server.py     # MCP server: 7 evaluators + grading + LLM judge
│   ├── data_server.py           # MCP server: config, datasets, DB, resume
│   ├── reporter_server.py       # MCP server: fitness, reports, comparisons
│   ├── config.json              # MCP host config (server declarations)
│   └── client.py                # Thin MCP client wrapper for orchestrator script
├── evaluators/                   # Kept as-is (imported by evaluation_server)
│   ├── __init__.py
│   ├── chat.py
│   ├── code_execution.py
│   ├── exact_match.py
│   ├── extractive_qa.py
│   ├── instruction.py
│   ├── multiple_choice.py
│   └── retrieval.py
├── datasets/                     # Unchanged
├── runner.py                     # Thin orchestrator (~150 lines)
├── cli.py                        # Unchanged CLI entry point
├── run_benchmark.py              # Unchanged entry point
├── db_schema.sql                 # Unchanged (+ optional fitness tables)
├── config_full.yaml              # Unchanged (+ optional fitness_weights section)
├── robustness_defaults.yaml      # Unchanged
├── benchmark_MCP.md              # This document
└── READY_TO_RUN.md               # Updated with MCP instructions
```

## 7. Risks & Mitigations

| Risk | Severity | Probability | Mitigation |
|---|---|---|---|
| **Over-engineering** — 5 MCP servers for a single-user tool | Medium | Medium | Start with 3 servers (Phase 1-2), split only when needed |
| **MCP overhead on tight loops** | Low | Low | stdio transport is sub-ms; benchmark requests take seconds |
| **Python MCP SDK bugs** | Low | Low | SDK is stable (Anthropic-maintained, widely used). Pin version |
| **Breaking existing runs** | High | Low | Each phase preserves backward compatibility. Old CLI always works |
| **Debugging distributed tools** | Medium | Medium | Structured logging per-server. Each server independently testable |
| **Session state across tool calls** | Medium | Medium | Resource sampling sessions need server-side state → use session IDs |
| **Migration effort** | High | High | Phased approach: each phase is independently valuable and deployable |

## 8. Out of Scope (Future Work)

| Feature | Why deferred |
|---|---|
| **A2A (Agent-to-Agent)** | No agents exist in this architecture; not applicable |
| **LLM agent orchestrator** | Evaluated and rejected (§4.1): adds latency and non-determinism with no benefit — the orchestration loop is fully deterministic |
| **Remote MCP servers** | Useful for cloud benchmarking. Requires SSE transport. Defer until single-machine MCP is stable |
| **MCP Sampling** | Protocol feature where server asks host to perform LLM inference. Could replace LLM judge plumbing but adds complexity |
| **CI/CD integration** | Automated benchmark runs on push. Build on Phase 3 once script orchestrator is stable |
| **Benchmark-as-a-service** | Expose benchmark capability as an MCP server itself (other apps can trigger benchmarks). Cool but premature |

---

## 9. Summary

| Phase | Effort | Value | Risk | Dependency |
|---|---|---|---|---|
| **Phase 1 — Reporter** | Low | High (fitness formula + reports = new capability) | None | None |
| **Phase 2 — Telemetry** | Medium | High (untangles messiest coupling in codebase) | Low | Phase 1 (for report integration) |
| **Phase 3 — Evaluation + Data** | Medium-High | High (runner becomes thin; resume simplified) | Medium | Phase 2 |
| **Phase 4 — Inference** | Medium | Medium (architectural completion — full decomposition) | Medium | Phase 3 |

**The key insight**: every phase delivers standalone value. Phase 1 can ship today and improve the benchmark without touching a single line of existing code. This is not a rewrite and not an agentic redesign — it's a progressive modular refactoring where MCP provides the protocol for clean service boundaries. The only LLM inference is the existing LLM-as-judge, extended to cover a handful of brittle heuristics in evaluation.
