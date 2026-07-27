# MT5 Observed-Entry Replay Proof

**Date:** 2026-07-27

## Purpose

Run one frozen trading day inside MetaTrader 5 Strategy Tester as a third,
independent check of the existing Python candidate and oracle simulations.
The first proof uses the exact entries that MT5 executed and changes only
management and exits.

The day is a machinery test. It must not be used to claim that a policy is
profitable or selected for live deployment.

## Frozen Scope

The initial fixture is 2026-07-27:

- 10 executed signal groups;
- 46 closed XAUUSD positions;
- observed net result of -32.63 EUR;
- no open positions at the freeze time.

The fixture binds to the SHA-256 of the replay source, MT5 history export,
policy, MQL5 source, compiled EX5, tester configuration and result.

## Architecture

### Deterministic exporter

`mt5_replay_bridge.py` reads the current `replay_trades.jsonl` and emits a
semicolon-delimited tester fixture plus a JSON manifest. It never talks to
the live order API.

Each ticket row contains:

- signal, provider, ticket and leg identity;
- direction, volume and raw MT5 server-epoch entry timestamp;
- observed entry price;
- observed close timestamp, price, reason and net EUR result;
- provider SL and TP1/TP2;
- source trade fingerprint.

Rows are stable-sorted by entry timestamp, signal and ticket. Missing TP2,
volume, entry evidence or a closed result blocks policies that require them.

### MQL5 tester expert

`mql5/Experts/TelegramSignalReplayEA.mq5` runs only when
`MQLInfoInteger(MQL_TESTER)` is true. It reads the fixture through
`FILE_COMMON`, creates virtual positions at the exact observed entry facts and
walks the Strategy Tester real-tick stream.

The expert never submits, modifies or closes a live order. It does not import
DLLs or use the network.

The first policy catalog is intentionally small:

1. `observed_close`: close each virtual ticket at its observed MT5 close fact.
2. `all_tp2_keep_be`: every ticket targets provider TP2; TP1 moves remaining
   tickets to their own entry price; provider SL remains active.
3. `all_tp2_no_be`: every ticket targets provider TP2 and keeps provider SL.

BUY exits use Bid and SELL exits use Ask. TP closes at the target. A stop gap
closes at the first executable quote. Same-tick TP/SL ambiguity blocks the
ticket. Unclosed tickets at the frozen horizon are blocked, not silently
valued.

### Result certification

The expert writes a deterministic result CSV through `FILE_COMMON`, including
one line per ticket and a summary. `mt5_replay_bridge.py` parses it and checks:

- the exact ticket set;
- observed entry immutability;
- close reason, timestamp and price;
- per-ticket and total EUR result to the cent;
- zero omitted, duplicated, blocked or still-open tickets.

`observed_close` is the mandatory money baseline. It must equal the 46 observed
tickets and -32.63 EUR before an alternative-policy result is shown.

The alternative policies are diagnostic even if they produce more profit.
They become research evidence only after the same code passes a declared
multi-day in-sample, out-of-sample and hold-out process.

## MT5 Execution

The EA is compiled with the installed MetaEditor build. A dedicated INI and
SET file live under `MQL5/Profiles/Tester`. Runs use:

- XAUUSD;
- M1;
- Model 4, every tick based on real ticks;
- EUR deposit currency and the demo leverage;
- local tester agents only;
- optimization disabled;
- no automatic terminal shutdown.

The native MCP `tester_run_backtest` tool starts one fixed-policy run at a
time. MCP optimization is not assumed because the current server exposes only
single backtest execution and status tools.

## Safety

- No production Python order path imports this module.
- No live strategy, VM code or bot process changes.
- No trade-capable MCP tool is called.
- The EA refuses to initialize outside Strategy Tester.
- Fixture and result files are under a dedicated
  `Common/Files/TelegramSignalReplay` directory.
- Code push and VM deployment require separate explicit user approval.

## Acceptance

The proof is complete only when:

1. exporter tests fail first and then pass;
2. the MQL5 source compiles with zero errors;
3. the observed baseline returns all 46 tickets and -32.63 EUR;
4. a repeated baseline has the same result fingerprint;
5. each alternative accounts for the same 46 tickets;
6. Python candidate, independent oracle and MT5 differences remain visible;
7. no generated tester artifact or credential is committed.

If the current-day broker history is still changing during acquisition, the
fixture uses the last closed deal as an immutable cutoff. A live full-day tick
cache is not declared complete until the day is over.
