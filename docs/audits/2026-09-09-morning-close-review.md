# Morning close review: incomplete capture

## Timing and scope

The scheduled 10:30 Madrid review was delivered at 08:31:56.688 UTC on
September 9. The first explicit clock check returned 08:32:09 UTC, after the
08:32:00 final-capture deadline. No final collector was launched. This is a
missed capture deadline, not a successful final collection or an extension of
the original 06:30-08:30 UTC cohort. No time, source hash or frozen protocol
was changed to admit a late capture.

Read-only inspection at 08:34 UTC found no remote final-capture artifacts and
no remaining Codex-Morning-Capture tasks. Local receipts likewise contained
no final invocation. The last completed checkpoint remains
sep9_check_20260909t081022z_609f8eea, with event cutoff
08:10:26.969880 UTC and completion 08:10:30.647226 UTC. Its eleven listed
artifact hashes were rechecked successfully. It was captured without runtime
blockers, but it does not close the remaining 19 minutes 33 seconds of the
morning window. Ordinary runtime logs may still contain later evidence; that
is not a substitute for claiming the prescribed final capture completed.

## Observed live health

At 08:33:33 UTC the VM checkout was clean at
ed9ede1f1f8d228791e09d5b10bbdd2645c1793c. The heartbeat was approximately
12 seconds old, with main PID 9412, zero bot positions, zero open signals and
zero pending entries. Both main PID 9412 and supervisor PID 1464 were present
at 08:34 UTC, with their existing startup times.

Telemetry publication succeeded at 08:32:39 UTC, commit
e47a540d5503a52feb28833a14ed12a574bc7b06, with zero pending chunks/files,
no error and no publisher index.lock. This inspection did not publish data,
restart a process, change a setting or send an order or Telegram message.

The bounded 2 MiB event-tail inspection at 08:33:51 UTC still included the
current session startup and both completed startup scans. For ed9ede1f1 it
contained exactly two notify_sent events: startup and the known isolated
historical reply-association warning. It showed six recovered raw messages
with six processing confirmations, two startup scans and ten poll-coverage
events; no new execution events or renewed notification loop were observed
in this inspected tail. These are scoped observations, not an unbounded
whole-log audit or native proof of current account-wide exposure.

## Evidence and next review

The latest checkpoint retains two native deals for one position, associated
with canal2_2659: one entry and one exit, not two independent trades. This
does not satisfy the runbook requirement for at least two signals and
sufficient operations. The last checkpoint reports sixteen cumulative raw
message rows with no missing causal fields or revision conflicts in that
capture; tick continuity and full live parity remain uncertified.

The original failures for revisions 642 and 1366, their later equivalent
processed edits, and unresolved historical replies 2654, 2655 and 2658 remain
in evidence. The version-bound market_forward_v1 freeze was already invalid
as intact prospective validation after the authorized operational repairs.
The missed final deadline is an additional independent limitation. No
simulator validation or strategy-performance conclusion is claimed.

The existing automation remains active for the user-requested September 9
14:00 Europe/Madrid read-only review. That review must distinguish ordinary
later runtime evidence from the incomplete morning capture, report its result
even if healthy, and then remove the one-day automation. The bot stays running.
This report is local documentation only; no code commit or push was made.
