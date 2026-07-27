# MT5 Observed-Entry Replay Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a frozen executed-MT5 day through an isolated MQL5 virtual-position replay and certify its baseline before showing TP2 alternatives.

**Architecture:** A pure Python bridge exports deterministic ticket fixtures from `replay_trades.jsonl` and certifies tester output. A tester-only MQL5 EA reads the fixture from `FILE_COMMON`, walks real XAUUSD ticks, and writes one deterministic result per ticket. The live bot and all trade-capable paths remain untouched.

**Tech Stack:** Python 3.11+, pytest, MQL5 build 6061, MetaTrader 5 Strategy Tester Model 4, native MT5 MCP backtest/status tools.

---

### Task 1: Deterministic tester fixture

**Files:**
- Create: `mt5_tester_replay.py`
- Create: `tests/test_mt5_tester_replay.py`

- [ ] **Step 1: Write failing fixture tests**

Add tests that call:

```python
rows, manifest = build_fixture(
    replay_rows=[trade],
    day=date(2026, 7, 27),
    observed_history=history,
)
```

Assert exact ticket ordering, server-epoch millisecond timestamps, immutable
entry price/volume, provider SL/TP1/TP2, observed P&L, 46-ticket universe
accounting, and blockers for missing TP2, duplicate ticket, open ticket,
history mismatch and non-EUR totals.

- [ ] **Step 2: Verify the tests fail for the missing module**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py -q
```

Expected: collection fails because `mt5_tester_replay` does not exist.

- [ ] **Step 3: Implement the minimal fixture API**

Implement these public functions:

```python
def build_fixture(
    *,
    replay_rows: Iterable[dict],
    day: date,
    observed_history: Iterable[dict],
) -> tuple[list[dict], dict]: ...

def write_fixture(
    rows: Iterable[dict],
    manifest: dict,
    *,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path]: ...
```

Use canonical JSON with sorted keys and compact separators for fingerprints.
Write CSV atomically with `;` delimiter and the fixed column order declared in
the design. Never infer a missing volume, TP2 or observed close.

- [ ] **Step 4: Verify fixture tests pass**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py -q
```

Expected: all fixture tests pass.

- [ ] **Step 5: Commit**

```powershell
git add mt5_tester_replay.py tests/test_mt5_tester_replay.py
git commit -m "feat: export deterministic MT5 replay fixtures"
```

### Task 2: Fail-closed result certification

**Files:**
- Modify: `mt5_tester_replay.py`
- Modify: `tests/test_mt5_tester_replay.py`

- [ ] **Step 1: Write failing certificate tests**

Exercise:

```python
certificate = certify_result(
    fixture_rows=rows,
    fixture_manifest=manifest,
    policy_id="observed_close",
    result_rows=result_rows,
)
```

Require `status == "certified"`, 46 expected and checked tickets, exact ticket
set, zero blockers, total observed and replay P&L of `Decimal("-32.63")`, and
a stable SHA-256. Mutate one ticket, entry, close, cent, source fingerprint and
policy; each mutation must produce `status == "blocked"`.

- [ ] **Step 2: Verify certificate tests fail**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py -q
```

Expected: failures because `certify_result` is absent.

- [ ] **Step 3: Implement the certificate**

Add:

```python
def read_result(path: Path) -> list[dict]: ...

def certify_result(
    *,
    fixture_rows: Iterable[dict],
    fixture_manifest: dict,
    policy_id: str,
    result_rows: Iterable[dict],
) -> dict: ...
```

Use `Decimal` with `ROUND_HALF_UP`. Baseline price/timestamp comparisons are
exact fixture facts; alternative results retain the first-touch tick evidence.
Do not return aggregate policy metrics when any ticket is missing, duplicated,
blocked or open.

- [ ] **Step 4: Verify certificate tests pass**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py -q
```

Expected: all result and mutation tests pass.

- [ ] **Step 5: Commit**

```powershell
git add mt5_tester_replay.py tests/test_mt5_tester_replay.py
git commit -m "feat: certify MT5 tester replay results"
```

### Task 3: Tester-only MQL5 virtual replay

**Files:**
- Create: `mql5/Experts/TelegramSignalReplayEA.mq5`
- Create: `tests/test_mt5_replay_ea_contract.py`

- [ ] **Step 1: Write failing source-contract tests**

Assert the source exists and contains:

```text
MQLInfoInteger(MQL_TESTER)
FILE_COMMON
OrderCalcProfit
observed_close
all_tp2_keep_be
all_tp2_no_be
```

Also assert it contains no `OrderSend`, `CTrade`, `WebRequest`, DLL import or
live-position mutation API.

- [ ] **Step 2: Verify source-contract tests fail**

Run:

```powershell
python -m pytest tests/test_mt5_replay_ea_contract.py -q
```

Expected: failure because the MQL5 source is absent.

- [ ] **Step 3: Implement the EA**

The EA inputs are:

```cpp
input string InpFixtureFile = "TelegramSignalReplay\\fixture.csv";
input string InpResultFile  = "TelegramSignalReplay\\result.csv";
input string InpPolicy      = "observed_close";
input string InpFixtureSha256 = "";
```

`OnInit` refuses non-tester execution, validates every row and opens the output
file. `OnTick` uses `MqlTick.time_msc`, Bid for BUY exits and Ask for SELL
exits. `observed_close` calls `OrderCalcProfit` at the observed close fact.
TP2 policies use first-touch TP/SL logic; `all_tp2_keep_be` activates BE only
after a TP1 tick. `OnDeinit` writes exactly one row per ticket and a summary.

- [ ] **Step 4: Run source tests and compile**

Run:

```powershell
python -m pytest tests/test_mt5_replay_ea_contract.py -q
& "C:\Program Files\MetaTrader 5\metaeditor64.exe" /compile:"C:\Users\josea\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts\Research\TelegramSignalReplayEA.mq5" /log:"C:\Users\josea\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Logs\telegram-signal-replay-compile.log"
```

Expected: pytest passes and the compiler log reports 0 errors.

- [ ] **Step 5: Commit**

```powershell
git add mql5/Experts/TelegramSignalReplayEA.mq5 tests/test_mt5_replay_ea_contract.py
git commit -m "feat: add tester-only MT5 signal replay EA"
```

### Task 4: Prepare the frozen 2026-07-27 run

**Files:**
- Modify: `mt5_tester_replay.py`
- Modify: `tests/test_mt5_tester_replay.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI/profile tests**

Test `main(["prepare", "--date", "2026-07-27", ...])` with a temporary MT5
data directory. Assert it writes fixture and manifest under
`Common/Files/TelegramSignalReplay`, copies only the compiled research EA, and
writes three INI/SET pairs under `MQL5/Profiles/Tester` with Model 4,
optimization off, local agents only and `ShutdownTerminal=0`.

- [ ] **Step 2: Verify CLI/profile tests fail**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py -q
```

Expected: failure because the `prepare` command is absent.

- [ ] **Step 3: Implement prepare and certify commands**

Add CLI commands:

```text
python mt5_tester_replay.py prepare --date 2026-07-27
python mt5_tester_replay.py certify --run-dir runtime_data\mt5_tester_runs\2026-07-27
```

The prepare command reads only MT5 history and replay artifacts. It requires
10 signals, 46 tickets, no open ticket and observed net -32.63 EUR for this
frozen proof. Generic dates derive their own expected totals without embedding
the July result in production logic.

- [ ] **Step 4: Verify tests and document the workflow**

Run:

```powershell
python -m pytest tests/test_mt5_tester_replay.py tests/test_mt5_replay_ea_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add mt5_tester_replay.py tests/test_mt5_tester_replay.py README.md
git commit -m "feat: prepare isolated MT5 replay runs"
```

### Task 5: Execute and compare the proof

**Files:**
- Generated only: `runtime_data/mt5_tester_runs/2026-07-27/**`
- Generated only: MT5 `Common/Files/TelegramSignalReplay/**`
- Generated only: MT5 `MQL5/Profiles/Tester/telegram-replay-*.ini`

- [ ] **Step 1: Rebuild current runtime evidence**

Run telemetry materialization, ledger reconciliation, replay build and
accounting audit. Confirm the selected day contains 10 trades, 46 tickets and
-32.63 EUR.

- [ ] **Step 2: Prepare and compile**

Run the prepare command and compile the installed EA. Record source, EX5,
fixture, manifest, INI and SET hashes in the run card.

- [ ] **Step 3: Run the observed baseline twice**

Use MCP `tester_run_backtest` with the observed-close INI/SET, wait with
`tester_get_status`, certify the output, repeat, and require identical result
and certificate fingerprints.

- [ ] **Step 4: Run the two TP2 policies**

Run `all_tp2_keep_be` and `all_tp2_no_be` one at a time. Require the same 46
tickets and no blocked/open rows. Keep their results labelled diagnostic.

- [ ] **Step 5: Run repository verification**

Run:

```powershell
python -m pytest -q
git diff --check
git status -sb
```

Expected: full suite passes, no generated runtime or MT5 artifacts are staged,
and no push or VM restart has occurred.
