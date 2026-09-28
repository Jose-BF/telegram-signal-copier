# Operational recovery repairs

## Authorization and scope

The user explicitly requested implementing the corrections after being asked
to authorize publication and activation when positions, orders and signals
were clear. Preserve that condition. The code base at the start of this work
was f3ab236f472c961cdade34eb765c5d66af03e10d. Unrelated local research and
AGENTS.md changes are excluded from the repair commit.

The intended production changes are listener.py, main.py,
tools/run_bot_watch.py and tools/runtime_telemetry.py, with their focused
regressions. No trading policy, position sizing or strategy parameters change.

## Root causes and evidence

1. Startup validation and orphan recovery had whole-file reads against a
   roughly 1.43 GB runtime history. The first repair streamed validation; the
   remaining orphan scan now also streams. Startup gets a separate 600-second
   watchdog budget, while an established heartbeat retains its 180-second
   timeout. Console output is unbuffered for startup diagnostics.
2. Gold startup recovery repeatedly stopped at 2,000 messages and bypassed the
   critical-notification cooldown with a second, direct notify call. The direct
   call is removed; incomplete coverage has a channel-local 300-second retry
   interval. Loading history now runs outside the Telegram event-loop thread.
3. The persistent old recovery boundary was caused by two earlier revisions:
   canal2_642/new was deferred for restart on July 29; canal2_1366/new failed
   processing on August 11. Both had later edits with identical text and reply
   context and explicit telegram_processed records. The loader treated those
   old revisions as permanently retryable despite the confirmed equivalent
   current contents.
4. Telemetry publication left an index lock after an interrupted Git commit.
   At 07:56:15 UTC the stale, empty lock was preserved as
   index.lock.recovered.2d3125b32904499b83713f334a24d318 under the isolated
   publisher's .git directory, after taking publish.lock and verifying no Git
   processes were present. The next ordinary attempt demonstrated the remaining
   cause: at 07:57:37 UTC, committing 58 files hit the watcher's 15-second Git
   timeout and left another lock. The publisher now has a separate 120-second
   Git-command budget and a 600-second process limit. Ordinary code-update Git
   checks retain 15 seconds.

## Historical evidence is not rewritten

An earlier revision is excluded from the live recovery rewind only if a later
edit of that same channel/message has an explicit processing confirmation and
identical fully captured text-only content, original date and reply context.
Media, changed content/replies, missing fields, conflicting raw captures,
unconfirmed edits and older edits cannot establish equivalence.

The original remains in unprocessed_revisions, never processed_revisions.
equivalent_processed_revisions records the link, also included in the startup
scan event. This proves equivalent current content was handled, not that the
earlier attempt succeeded or that all historical actions are certified.
No old Telegram messages were dispatched by the diagnostic.

Read-only evaluation of the exact corrected loader against the VM history at
07:54:38 UTC took 15.94 seconds: both original revisions remained unprocessed,
both had proven equivalent processed edits, and the recovery cutoff became
2026-09-08T23:41:55.050042Z instead of 2026-07-29T20:47:33Z. No event file or
coverage cursor was edited. The helper copy stayed outside the active checkout.

## Publisher recovery safety

Automatic index-lock recovery runs only inside the isolated publisher checkout
while holding publish.lock. A recent lock, active/unknown Git process, failed
process inspection, missing inspection dependency, symlink or changed file
identity defers recovery. An old abandoned lock is atomically archived, not
deleted. Outbox cleanup still occurs only after a successful push.

At 08:00:19 UTC a one-off operator recovery identified the second lock against
the exact failed publication timestamp and timeout status, took publish.lock,
rechecked processes and preserved it as
index.lock.recovered.87e9ef0811f24f17a9a5c255a2360c84. That confirmed failed
attempt was retried through the existing publisher with a 120-second command
budget. This operator action does not relax the automatic stale-lock threshold.
The retry completed successfully at 08:00:43 UTC in 23.78 seconds, publishing
58 files to telemetry commit 9b189bafc5220bdb59dd8b1aeefa93bc6d7ad47c,
without restarting or modifying the trading process.

## Verification checkpoints

- Alert loop: two failing regressions reproduced direct notification bypass and
  immediate repeated full scans before the initial fix.
- Equivalent edit: a failing regression reproduced the July cutoff even after
  confirmed identical content; conservative negative cases remain covered.
- Thread isolation: regression showed history loading on the Telegram thread
  before the fix; it now runs on a worker thread.
- Publisher: local bare-remote test reproduced index.lock failure, then verified
  successful publication, preserved lock bytes and unchanged source HEAD.
- Additional lock guards cover recent locks, active/unknown processes, changing
  locks, inspection failure and an unavailable inspection dependency.
- Timeout separation: regression captured 15 seconds where 120 was required;
  the corrected watcher passes a distinct publisher timeout.
- Focused combined set before the final timeout change: 278 passed in 72.75 s.
- Full set before that final timeout change: 4,435 passed, 634 warnings in
  210.85 s; runtime_data/operational_repairs_20260909_pytest.xml.
- After timeout separation: 114 focused watcher/recovery/activation tests passed
  in 13.82 s. Final full suite: 4,436 passed, 634 warnings, 205.79 s, exit 0;
  runtime_data/operational_repairs_20260909_final_pytest.xml.

## Deployment checkpoint

Before publication, capture sep9_check_20260909t075730z_e0eb1a06 retained the
event delta and native broker evidence from the preceding capture, with status
captured and no blockers. At 07:56:59 and 07:58:20 UTC the running f3ab236f
checkout was clean and the heartbeat reported zero bot positions, zero open
signals and zero pending entries. Recheck freshness before activation.

Repair commit ed9ede1f1f8d228791e09d5b10bbdd2645c1793c contains exactly nine
code/test files. Whitespace checks passed; unrelated research remained local.
The final pre-publication capture was sep9_check_20260909t080423z_2b645dc5,
status captured with no blockers. Native snapshot 08:04:30.896 UTC contained
zero positions and orders. At 08:04:49 UTC, the clean f3ab236f VM heartbeat
was 13.55 seconds old and reported zero positions, open signals and pending
entries. Publication refused stale/non-flat native evidence by an explicit
45-second age gate; that check passed and the normal fast-forward push succeeded.
GitHub main was independently confirmed at ed9ede1f1 at 08:05 UTC.
The existing supervisor retains its exposure recheck and post-pause heartbeat
gate before activation. No forced process stop or manual order was used.

The ordinary publisher also succeeded at 08:02:25 UTC, telemetry commit
4c14d2fbef901d38d3cdf7af80e71286a053758c, with zero pending chunks/files and
no index lock on subsequent checks. The trading process remained on f3ab236f
at 08:05:23 UTC awaiting the normal supervisor update. Record actual activation
and fresh capture evidence below rather than equating the push with deployment.

At 08:06:28 UTC the old supervisor had cleanly stopped main PID 1908 after its
normal update gate and was checkpointing. By 08:06:59 UTC the VM had
fast-forwarded to ed9ede1f1 with a clean checkout. The batch launcher reloaded
the changed supervisor as PID 1464. New main PID 9412 started at approximately
08:07:14 UTC; session_started at 08:07:24.960 and mt5_account_connected at
08:07:30.269 both carried ed9ede1f1. This is startup progress, not yet the final
healthy confirmation.

## Verified live outcome

At 08:08:48.248 UTC startup_version_confirmed recorded ed9ede1f1 with
telegram_connected=true, mt5_connected=true and money_capture_ready=true.
The live management_decision_inputs_v1 capture contract retains all four kinds
and no-action evaluations.

Gold poller_startup_scan completed at 08:09:43.014 UTC: 200 messages, 194 seen,
five new and one edit. It recorded both equivalent-revision links explicitly.
Canal 1 completed at 08:10:00.850 UTC: 200 seen, no new messages or edits.
Both wrote fresh telegram_poll_coverage and the poller entered its active loop.
Six recovered messages have telegram_processed records; no old message was
silently marked processed to make the scan succeed.

Recovery retained three historical reply-association anomalies for canal2_2654,
canal2_2655 and canal2_2658. One generated an isolated Telegram notification.
At 08:11:34 UTC the new session still had exactly two notifications: startup and
that isolated warning. There were no repeated cap warnings, repeated startup
scans or additional notifications during the follow-up interval. Do not confuse
these retained historical incidents with the repaired alert loop or erase them.

Post-deployment capture sep9_check_20260909t081022z_609f8eea succeeded with no
runtime blockers, using the explicit new commit and preceding capture anchor.
At 08:10:56 UTC the VM checkout was clean at ed9ede1f1, and publication status
showed another ordinary success at 08:07:34 UTC, telemetry commit
394cf2c1b1f0b966466d7badecaee14d8042581e, zero pending files/chunks and no
index lock. At 08:11:34 UTC main PID 9412 had a heartbeat age below one second,
zero positions, zero open signals and zero pending entries. The bot stays running.

The existing capture follow-up was updated to ed9ede1f1, this audit and the new
capture anchor, retaining its original end time and quiet notification policy.
The old frozen comparison protocol remains version-bound and is not relabeled
as intact prospective validation. Audit notes and unrelated research remain
local; all nine operational code/test files are committed, published and active.
