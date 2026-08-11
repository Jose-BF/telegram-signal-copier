# Runtime Safety and Alert Noise Design

## Goal

Correct the production failures observed on 2026-08-11 without changing the
provider strategy or slowing the Telegram-to-MT5 entry path. Preserve complete
forensic evidence while reducing duplicate human notifications.

## Scope

### Poller survival

- A message handler failure may mark that revision as failed and retryable, but
  it must not terminate either channel's fallback polling.
- Channel failures are isolated per cycle. The other channel continues.
- The top-level poller is supervised and restarted after an unexpected exit.
- Poller failures and recoveries remain visible in the journal.
- Every literal `journal.anomaly` category in production code must be validated
  by an automated static test against `journal.CATEGORIES`.

### Installed TP preservation

- Before applying a new SL/TP generation, inspect the position currently held
  by MT5.
- If the requested TP is already installed on that ticket, preserve it exactly
  and apply only the requested SL change.
- The TP-chase path must never replace a correctly installed target merely
  because the current broker stop distance prevents installing that same value
  again.
- Existing behavior for genuinely missing late targets remains unchanged in
  this patch; it requires separate simulation evidence.

### Explicit duplicate retraction

- Detect deterministic provider corrections such as `This is not a new signal`
  without relying on Gemini.
- Automatic rollback is allowed only when all of these are true:
  - the candidate is the newest open signal in the same channel;
  - it was opened within a short bounded window;
  - an older open signal has the same direction and materially identical entry
    range, TP list and SL;
  - the correction is explicit and unconditional.
- Rollback closes/cancels only the newest duplicate, finalizes it as provider
  retracted, and records the exact evidence and linked original signal.
- If any condition is ambiguous, no order is changed and one human alert is
  emitted.
- A Telegram deletion without explicit text remains alert-only.

### Total-signal basket guard

- Dubai's guard observes account-currency total signal P/L:

  `total_pl = realized_pl + floating_pl`

- Realized P/L includes profit, commission, swap and fee for this signal's
  closed tickets. Open-ticket P/L comes directly from MT5.
- Closed-ticket results are cached after confirmation so history is not queried
  on every tick. Recovery after restart rebuilds the cache from MT5 history.
- Guard decisions run no slower than every 100 ms while positions remain open.
- Existing policy thresholds stay unchanged: loss cap `-50`, arm `+30`, lock
  `+20`.
- Decision events include floating, realized and total values so replay can
  reproduce the trigger.
- If realized P/L cannot be established, the guard does not invent zero. It
  records degraded evidence and continues protecting against the known
  floating loss cap without claiming a verified total-profit decision.

### Human alert noise

- Journal evidence is never suppressed.
- BE/SL incidents for tickets belonging to the same signal and failure reason
  are grouped into one notification even when their exact entry prices differ.
- The grouped message shows ticket count and the relevant price range instead
  of one message per distinct cent value.
- Repeated identical critical anomalies are rate-limited for Telegram only;
  the first alert is immediate and every repetition remains in the journal.
- Broker-money capture interruptions use persistence hysteresis: a transient
  failed sample does not notify. A recovery message is sent only if an
  interruption alert was previously sent.
- Trading, retries and protection are never delayed by alert aggregation.

### Media evidence

- Media capture is asynchronous and cannot block signal execution.
- This patch records a durable capture request/result linked to the Telegram
  message revision, including media type, SHA-256 and storage result.
- OCR and automatic trading from image-only levels remain outside the live
  execution path. Missing image levels must stay explicit rather than being
  guessed.

## Non-goals

- Do not change the number of entries, lot size, TP distribution, BE policy or
  zone-entry strategy from one day's evidence.
- Do not auto-close merely because Telegram deleted a message.
- Do not hide errors from telemetry in order to reduce notifications.
- Do not deploy, restart the VM or push to `main` without explicit approval.

## Verification

Each production failure receives a regression test that fails before its fix.
Focused tests cover each subsystem, followed by the complete pytest suite. The
final diff must contain no generated runtime data and no changes from the
pre-existing dirty worktree.
