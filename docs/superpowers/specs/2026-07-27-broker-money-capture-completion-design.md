# Broker Money Capture Completion

**Date:** 2026-07-27

## Goal

Collect enough native MT5 evidence during the official research window to
price every observed-entry replay in the account currency, including positions
that cross broker rollover. Missing evidence must block a result instead of
silently assuming zero cost.

This phase improves replay fidelity. It does not change live order behavior or
prove that a strategy is profitable.

## Native Evidence

`BrokerMoneySnapshotService.mq5` is a read-only MT5 service. It runs in its own
terminal thread and writes one small atomic CSV snapshot every 60 seconds under
that terminal's own `MQL5/Files` directory. Separate terminal installations
cannot race on, overwrite or impersonate the same evidence file.

The snapshot contains:

- native broker server time and GMT time;
- the latest server tick and its age;
- account server and instrument identity;
- swap mode, long and short rates;
- all seven weekday multipliers and the triple-swap day;
- point size, contract size and profit currency;
- terminal build.

The service has no order, position-modification, network or DLL path. Its
algorithmic trading permission remains disabled.

Python accepts the file only when it is fresh, internally consistent and
matches the values exposed independently by the MT5 Python API. Both sides use
the XAUUSD tick specifically; a newer Python tick is accepted only within the
age of the native snapshot. This independently checks the normalized broker
clock without confusing another Market Watch symbol with gold. The native GMT
timestamp, not the Python wall clock, identifies the snapshot.

## Runtime Collection

The bot checks the native snapshot every five minutes. This is a local,
lightweight read; it does not download ticks, run Git or delay Telegram
processing.

Snapshots are retained when:

- the bot starts;
- the broker specification changes;
- the server UTC offset changes;
- the capture lies within 15 minutes before or after broker midnight.

The JSON contract is rebuildable from the append-only event stream. Losing the
derived contract file therefore does not lose its historical snapshots.
Recovery accepts only intact journal payloads tied to the same anonymous
account fingerprint, server, symbol and native evidence source.

The startup Telegram message reports either:

- `Registro simulacion: activo`; or
- `Registro simulacion: INCOMPLETO`.

A later transition to unavailable or recovered produces one notification. A
capture or notification failure never stops trading. Periodic capture runs
outside the Telegram event loop, and startup resynchronizes open MT5 positions
before scanning historical evidence.

## Swap Calculation

For a normal rollover, the simulator requires matching native snapshots within
15 minutes on both sides. It applies the captured weekday multiplier, converts
the swap through verified historical EURUSD Bid/Ask ticks and rounds each
position to account cents.

Saturday and Sunday multipliers are zero for the current broker contract.
Because the market can be closed and no fresh server tick exists, those zero
rollovers may use matching pre-close and post-open specifications within 72
hours. A changed specification blocks the result. A non-zero multiplier never
uses this wider closure rule, and a zero multiplier on a weekday never receives
the weekend exception.

Observed commission or fee values that are not zero remain unsupported and
block certification. Broker UTC-offset transitions also remain fail-closed
until evidence proves the transition; no such transition occurs in the July
research window.

## Installation

The repository provides one deterministic command:

```powershell
python tools\install_broker_money_snapshot_service.py
```

It copies the tracked source into the active MT5 data directory, compiles it
in staging and requires a zero-error, zero-warning compiler report plus a new
EX5 file. A failed compile leaves the previous working source and EX5 intact.
The installer records source and EX5 hashes; `--verify-only` checks both hashes,
fresh native evidence and the complete fail-closed money contract.

One visible MT5 step remains:

1. Open `Navigator > Services > TelegramSignalCopier`.
2. Add `BrokerMoneySnapshotService`.
3. Leave algorithmic trading permission disabled.

Then verify:

```powershell
python tools\install_broker_money_snapshot_service.py --verify-only
```

## Weekly Acceptance

The official collection week is usable only when:

1. every bot startup reports active simulation recording;
2. any recording interruption and recovery remain visible;
3. Telegram, action, attempt, deal and position evidence stays complete;
4. required XAUUSD and EURUSD tick days have valid frozen contracts;
5. the observed MT5 baseline reconciles every ticket to the cent;
6. every alternative policy accounts for the full selected ticket universe;
7. overnight alternatives have verified rollover evidence;
8. candidate, independent oracle and MT5 tester agree or expose an explicit
   blocker.

The first daily runs are machinery checks. Strategy selection still requires
a fixed in-sample period, an untouched out-of-sample period and a permanent
hold-out sample.

## First Live Proof

At the 2026-07-27 21:00 UTC broker rollover, two open XAUUSD SELL positions
of 0.01 lot each received `+0.24 EUR` swap in MT5. The replay calculation used
the native snapshots from 20:55:11 and 21:00:11 UTC, the captured short rate
of 27.41 points and the causal EURUSD Ask of 1.13731. It independently returned
`+0.24 EUR` for each position. This evidence is frozen as a permanent
regression test.
