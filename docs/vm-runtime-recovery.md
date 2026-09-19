# VM runtime recovery

The Windows task runs `tools/start_logged_bot.py`, which records each watcher
attempt in separate output/error logs. It retries a verified watcher reload
(75) and transient transport failure (77), with a capped delay. Configuration,
account and duplicate-instance failures remain blocked; restarting never
bypasses the existing account or Git verification.

`tools/configure_vm_recovery.ps1` backs up the existing task XML before applying
boot, logon and one-minute recovery triggers. It uses the existing `bot` user
with S4U, no embedded password, no execution time limit, and IgnoreNew so a
running task is not duplicated. The watcher also holds an exclusive port lock.
The task can therefore start before interactive logon, subject to actual MT5
startup/IPC availability in that Windows session. Test the complete reboot
before claiming unattended recovery is verified.

Network/DNS failures keep Telegram's reconnect loop alive. A transient initial
connection or MT5 IPC failure exits with 77, so the watcher retries with capped
backoff. Authentication errors and other configuration failures are not
classified as transient. No historical entry-age checks are relaxed.

The journal queue has a 4096-event cap. If disk I/O stalls until the queue is
full, new receipts fail explicitly instead of allocating unlimited memory or
blocking Telegram. Those events are not durable: no caller may interpret a
failed receipt as confirmation. Flush also observes its timeout when the queue
is full. This protection is not a substitute for adequate storage.

Storage health uses only filesystem metadata, not a corpus scan. Below 4 GiB
free the bot emits a rate-limited critical anomaly. Below 2 GiB free the watcher
defers new telemetry copies/checkpoints, preserving original sources and
prioritizing runtime writes. The runtime heartbeat exposes free space and queue
depth. Existing bounded 16 MiB history/checkpoint reads remain in place.

NTFS compression can reduce physical storage without changing raw filenames,
contents, byte offsets, hashes or replay contracts. Mark `runtime_data` for
compression inheritance and compress the large existing JSONL/console files
once at low priority. Never truncate or delete raw evidence as log maintenance.
Compression does not provide unlimited retention: act on the disk warning by
expanding storage or arranging a verified archival migration.

Validation must cover cable-loss recovery, DNS/routing errors, fatal auth errors,
extended outages, saturated journal queues, disk reserve, launcher exit policy,
and the existing execution/replay tests. VM checks include deployed revision,
fresh progressing heartbeat, active Telegram poller, verified MT5 account,
positions/orders, free disk and actual task principal/triggers.
