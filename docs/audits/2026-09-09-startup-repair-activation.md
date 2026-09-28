# Startup repair and active capture

## Current verified deployment

User authorized restoring operation while retaining the signal-data capture.
Published and installed commit: f3ab236f472c961cdade34eb765c5d66af03e10d.
Its parent is fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd, the causal management
capture commit. That capture and the trading policy are retained.

The published repair changes runtime-store validation to per-record processing
with incremental hashes and bounded prefix copies, retains partial-tail archival
and durable replacement, removes duplicate startup validation and preserves
recovery tracebacks. Six files were committed; offline research changes were not
included. The VM fast-forward completed at 07:14:17 UTC with a clean checkout.

Verification of this published revision:

- Regression first: both 16 MiB stream cases exceeded the 8 MiB allocation
  budget under the old code; both pass with streaming validation.
- Initial focused recovery/startup/synchronization set: 115 passed.
- Full suite after additional regressions: 4,414 passed, 633 warnings, 206.96 s.
  XML: runtime_data/startup_repair_20260909_pytest.xml.
- The exact new inspection helper was tested read-only on the VM's 1,430,844,898
  byte event file: 15.92 s, peak working set 20,172,800 bytes, no partial tail,
  source unchanged, SHA-256 8de1fb9bb3d06109a64d9a29f162f14c5d74d83a81aec75b16fbbdbac147b26b.

## Activation and liveness

The normal launcher was started at 07:16:37 UTC after native snapshots of zero
positions/pending orders, age 9.995 seconds, no Python process, clean expected
HEAD and runtime_head_is_safe=True. The launcher task was Codex-Start-Signal-Copier.
Capture anchor: sep9_check_20260909t071619z_f707657b.
An earlier activation check refused a 46.5-second-old snapshot; no start occurred
from that check. The successful attempt used a new snapshot.

Watcher PID 10216 completed recovery and launched main PID 6480. That first
main process connected MT5 and recorded its live capture contract, but exceeded
the existing 180-second startup watchdog and was restarted by the supervisor.
The second main process, PID 1908, completed startup:

- 07:22:24.922 UTC: startup_version_confirmed, commit f3ab236f..., both
  telegram_connected and mt5_connected true, money_capture_ready true.
- 07:22:25 UTC: independent connection-change events confirmed both connections.
- 07:23:15.374 UTC: canal2_2659 signal_received and gold_555_entry_watch_started.
- 07:24:07 UTC health check: PID 1908 alive, heartbeat age 11.11 seconds,
  bot_position_count=0, open_signal_count=0, pending_entry_count=1.
  The heartbeat conservatively calls this exposure_state=open. A pending bot
  entry is not a broker order or an executed fill.

The active live_strategy_contract records management_decision_inputs_v1 and
all four supported management kinds with no-action evaluations enabled.

## Captures and follow-up

Use the original verified collector and wrapper through the additional local
dispatcher, which explicitly supplies the expected deployed commit:

```powershell
& ./runtime_data/causal_capture_today_20260909/operations/run_repaired_capture.ps1 -Phase checkpoint -ExpectedCommit f3ab236f472c961cdade34eb765c5d66af03e10d -AnchorLabel <last-completed-label>
```

The original dispatcher and frozen evidence were not rewritten. An initial
syntax error in the additional dispatcher was corrected and its PowerShell
parse verified before its first successful execution.

First healthy capture: sep9_check_20260909t072308z_840abb53, completed
07:23:37 UTC, status captured, no runtime blockers. It retained 371 event rows,
including five telegram_raw rows, a received signal and the startup connection
confirmation. Native snapshots/history showed zero positions, pending orders,
deals and orders at that capture. Later receipts may supersede it; use the
latest completed manifest as the next anchor and preserve every delta.

The captured source flags a repeated Telegram revision with differing complete
record fingerprints. This remains an input-quality question, not proof of a
broker execution error. Retain it for review; do not silently discard it.

The 06:30-08:30 UTC window contains an outage and a deployed-commit transition.
market_forward_v1 remains frozen to the old commit/source identity and is not
an intact prospective validation of the repaired deployment. Preserve that
limitation while collecting actual new messages, quotes, decisions and fills.
The existing 09:30/10:30 Madrid follow-up automation was updated to this
dispatcher and version, preserving its original date/window and quiet policy.

Second healthy capture: sep9_check_20260909t072441z_14f5d837, completed
07:24:48 UTC, status captured, no runtime blockers. It adds 14 entry-watch state
records and another telegram_raw row, with no missing causal fields on the new
row. Native exposure and native executed history remained zero. At 07:27:01
UTC the same main PID 1908 was alive, heartbeat age 21.42 seconds and one bot
entry still pending.

Two operational warnings remain distinct from live capture: automatic telemetry
publication reports a publisher-repo index.lock and retained outbox backlog;
Canal 2 startup catch-up reached its 2,000-message cap before the previous
coverage boundary. listener.py:11728 returns without marking historical catch-up
complete in that case, while live Telegram events remain enabled. The latter
means the backup poller has not completed initialization for that channel.
Do not certify uninterrupted historical coverage or a healthy Git telemetry
publication path from these healthy native/manual captures. No lock deletion,
cursor rewrite or policy change was performed.

## First observed execution

Capture sep9_check_20260909t072836z_bd00ef88 completed at 07:28:44 UTC,
status captured, without runtime blockers. Native history contains two deals
for one position, 1967261312, belonging to canal2_2659: a 0.04-lot XAUUSD buy
at 4407.27 at 07:27:08.815 UTC and its exit at 4407.77 at 07:27:40.308 UTC.
The exit carries a TP comment. These are an entry and exit, not two trades.
Native positions and pending orders were zero in both capture snapshots.

The event delta retains 405 management-decision starts and 405 decisions,
eight position snapshots, the first-leg fill and entry-watch confirmation,
the order request/result, and eight confirmed protection modifications.
This proves actual new execution and management data are being captured; it
does not resolve the historical coverage, publisher or comparator limitations.

At 07:31:22 UTC the same main PID 1908 and watcher PID 10216 were alive;
the runtime heartbeat was 1.07 seconds old. It still reported one open signal,
zero bot positions and zero pending entries, exposure_state=open. A closed
first leg is not permission to restart while the signal lifecycle remains open.
No further deployment or restart was performed.

## Additional local hardening, not deployed

After the first main process hit the startup watchdog, two further local fixes
were prepared: stream the orphan-finalizer's remaining whole-history read, and
give initial recovery a separate 600-second watchdog budget while retaining the
180-second limit for an existing runtime heartbeat. Main stdout is unbuffered
in that local launcher change. Focused verification: 108 passed. Its full suite
passed 4,416 tests with 634 warnings in 205.30 seconds, recorded separately in
runtime_data/startup_repair_20260909_final_pytest.xml.

These additional edits are NOT in the running f3ab236f commit. Once the bot
completed startup and acquired a pending natural entry, no further publication,
stop or restart was performed. Retain this distinction for any later maintenance;
do not treat a local test pass as activation or bypass pending-entry safeguards.
