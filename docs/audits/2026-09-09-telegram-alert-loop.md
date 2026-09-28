# Repeated Telegram startup-history alerts

## Scope and deployed evidence

The user reported repeated Telegram messages and requested an investigation.
No publication or restart was authorized for this new repair. The VM remains
on f3ab236f472c961cdade34eb765c5d66af03e10d. Earlier startup hardening and
unrelated research edits remain local and must not be bundled implicitly.

A bounded, read-only 8 MiB event-tail inspection at 07:35:11 UTC found
repeated successful notify_sent records with the same text and payload hash:
"ATENCION: no pude revisar todo el intervalo sin conexion de Gold Signals..."
For example: 07:29:12.650, 07:29:33.937 and 07:29:54.049 UTC.
Associated anomalies consistently reported fetched=2000 and limit=2000.
The same tail contained 30 channel_msg anomalies and 28 suppressed critical
notifications, but the direct notification continued every roughly 20-30 s.
This is a notification/recovery loop, not evidence of duplicate trade orders.

## Cause and local repair

listener._poller_initial_scan_channel returned False when startup history did
not reach its previous coverage boundary. The channel remained uninitialized,
so each poll cycle fetched the same bounded history again. The failure path
both called journal.anomaly (already rate-limited) and awaited notify directly
(not rate-limited). Suppression of the former could not suppress the latter.

The local repair removes the second notification path and introduces a
channel-local 300-second retry interval for incomplete startup coverage.
The critical anomaly remains recorded and uses the existing alert cooldown.
Successful coverage clears the interval; another channel is not delayed.
An incomplete scan still cannot mark coverage complete, dispatch historical
messages, or mark the channel initialized. Live event handlers are unchanged.

This is containment of repeated scans and alerts, not reconstruction of the
missing historical interval. Do not advance coverage cursors to hide the gap.

## Verification

Regression first: tests/test_poller_startup_gap.py reproduced two failures
against the original behavior: a direct notification escaped the rate limiter,
and five immediate/repeated polls performed five full scans instead of one.
After the repair, the three new tests pass together with listener helpers and
journal regressions: 116 passed, 36 warnings, 4.00 s.
Full suite: 4,419 passed, 634 warnings, 228.94 s, exit code 0. Command:
`python -m pytest -q --junitxml=runtime_data/telegram_alert_loop_20260909_pytest.xml`.
The focused command was `python -m pytest -q tests/test_poller_startup_gap.py
tests/test_listener_helpers.py tests/test_journal.py`. Diff whitespace check
passed. No test sends real Telegram messages or MT5 orders.

## Separate publication backlog

A read-only VM check at 07:38:21 UTC confirmed the latest automatic publication
attempt at 07:37:20 UTC failed because the isolated publisher repository's
.git/index.lock already existed. That file was empty, modified at 07:17:26 UTC,
and no git.exe process was observed. Last successful publication was
2026-09-08T23:37:36Z; the status reported 17 pending chunks / 34 pending files.
No lock was removed or altered, and no publication was triggered here.
Local recording and previously downloaded native/manual captures are separate
from this failed automatic upload. Its error is not evidence of data loss.

## Activation gate

This repair changes listener.py and adds tests/test_poller_startup_gap.py only,
plus this audit. It is local, uncommitted and unpublished. Until an authorized
deployment, the running process can continue sending the repeated warnings.
Require explicit publication authorization and fresh exposure evidence before
activation. Zero broker positions alone is insufficient while a Gold signal
lifecycle or a pending bot entry is still open.
At 07:40:08 UTC main PID 1908 and watcher PID 10216 remained alive; heartbeat
age was 4.20 seconds, with zero bot positions, one open signal and zero pending
entries, exposure_state=open. The running capture remains active; this check
does not establish that a restart is safe.
