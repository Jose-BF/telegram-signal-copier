# Startup investigation, September 9

## Fresh evidence

- At 06:56:08 UTC (08:56 Madrid), the VM had no Python process. The heartbeat
  still identified PID 6052 at 2026-09-08T23:47:54.275 UTC. Both known bot
  scheduled tasks were subsequently observed Ready. Scheduled task state alone
  is not bot liveness, particularly with the persistent cmd /k launcher.
- VM main, HEAD and GitHub refs/heads/main all resolved to
  fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd. The checkout was clean.
  Git blob checks matched HEAD for main.py, management_decision_evidence.py,
  runtime_paths.py, tools/run_bot_watch.py, pending_actions.py and
  position_lifecycle_monitor.py. Raw cross-machine SHA differences on some
  checkout files were line-ending differences, not evidence of a stale version.
- That commit installs causal management input capture. The separate uncommitted
  local simulator/comparator work is offline tooling, not missing live telemetry.
- Read-only native checkpoint completed at 06:57:36 UTC. Both position and
  pending snapshots were empty; native history returned zero deals and orders.
  No new event bytes or Telegram rows were available for the morning window.
  XAUUSD and EURUSD ticks were captured, but ticks alone cannot replace missing
  messages, decisions and executions.

Checkpoint: `runtime_data/causal_capture_today_20260909/sep9_check_20260909t065654z_e327301d/`.
Manifest SHA-256: 0658f8a457a7b57dca72559bcff6c1a2d1ed117c97434b446eb90bea5040b1da.
Archive SHA-256: 976c6ec7e6c1b6188e7cf9b94deb8060a466bf7ec390738963817f37ea6f053c.
The collector retained runtime_heartbeat_pid_missing and runtime_heartbeat_stale.
The temporary capture task completed and was removed by its dispatcher.

## Failure evidence and limits

The 08:26:59 Madrid start ended at 08:39:15 with recovery_io_failed,
[Errno 22] Invalid argument, and exit 76. An additional 08:40:09 start marker
is present, but no current Python process or fresh heartbeat followed it in
the inspected evidence. Its initiator and exact termination are not established.
Do not describe this later attempt as a successful restart.

Windows System event 2004 documented low virtual memory during recovery:

- At 06:29:53 UTC, python.exe PID 5748 used 3,006,709,760 bytes and
  powershell.exe PID 9988 used 1,452,265,472 bytes.
- At 06:34:49 UTC, powershell.exe PID 6236 used 2,119,348,224 bytes and
  python.exe PID 5748 used 1,461,833,728 bytes.

This confirms memory exhaustion pressure, including substantial concurrent
PowerShell consumption during diagnosis. Rising CPU alone did not prove useful
recovery progress. The error text does not establish which I/O instruction raised
Errno 22, and there is no retained traceback. Do not claim a proven one-to-one
causal mapping between that errno and a particular allocation.

The source has an unbounded startup validation path:

- runtime_paths.py:226 reads the entire existing stream into bytes.
- runtime_paths.py:140 splits the complete payload into all lines at once.
- tools/run_bot_watch.py:274 calls initialize_runtime_store before recovery.
- tools/runtime_recovery.py:391 calls initialize_runtime_store again.
- tools/runtime_recovery.py:481 reduces the caught exception to an error string.

The .runtime-store.json manifest was successfully written during the attempt
and records the full stream hash below. Therefore it is incorrect to conclude
the entire attempt made no progress; the repeated validation remains material.

## Read-only streaming verification

A separate Python process read the production event file one bounded line at
a time, checked JSON syntax, hashed the unchanged bytes and counted event types.
It did not import the live runtime, start MT5, send orders or modify evidence.

- Size: 1,430,844,898 bytes; 913,579 records; maximum line: 5,296 bytes.
- Invalid JSON lines: zero. File size and modification time stayed unchanged.
- SHA-256: 8de1fb9bb3d06109a64d9a29f162f14c5d74d83a81aec75b16fbbdbac147b26b.
  This equals the startup manifest's recorded hash.
- Corrected event-name count pass: 17.94 seconds, peak working set 17,293,312 bytes.
  An earlier pass read the wrong event-name key; its event counts were discarded.
  Its independent byte hash and JSON validation agreed.
- Historical telegram_raw: 23,569; mt5_position_snapshot: 12,221.
- management_decision_inputs_v1: 328,596 bot_internal_decision_started records
  and 328,596 bot_internal_decision records. The deployed capture was exercised
  historically; this is not a complete per-operation coverage certification.
- Last record timestamp: 2026-09-08T23:47:21.528+00:00.

This verifies syntactic readability and agreement with the saved hash, not the
economic validity or causal completeness of every historical record.

## Concrete repair direction

Replace whole-file startup validation with bounded streaming validation and
incremental hashing, preserving partial-tail archival, CSV validation, durable
writes and existing manifest semantics. Avoid redundant full validation within
one startup. Retain a useful traceback or operation/path context on recovery
failure. Verify valid large inputs, malformed records, incomplete tails and
failure preservation before publishing or activating a repair.

No bot source edit, publication or restart was performed in this investigation.
Earlier documentation saying restart authorization had never been given is
historical: an authorized attempt already happened, as recorded above. A repair
publication is a separate operation under the project's deployment safeguards.
Existing frozen simulator results were not rewritten. Any future live commit
change needs an explicitly updated capture/protocol identity, preserving the
failed morning interval rather than relabeling it as complete evidence.
