# Authoritative Shadow Settlement Design

## Goal

Make the three frozen strategies for each provider comparable at the end of
every day or week even if the live observer was restarted, disconnected or
temporarily unable to read historical ticks. The live shadow remains a useful
preview. A deterministic post-session settlement over the complete broker
history becomes the authoritative result.

This system compares hypothetical policies. It never places orders, changes a
live strategy or promotes a candidate automatically.

## Accepted Architecture

The evidence path has two independent layers:

1. **Live preview.** Every eligible Telegram signal is registered before its
   outcome. The observer advances the frozen candidates while the bot runs and
   records state transitions for immediate diagnostics.
2. **Post-session settlement.** A standalone process rebuilds every registered
   candidate from its original registration time using the complete XAUUSD and
   account-currency tick caches. It consumes the same resolved provider
   management timeline and the same pure strategy engine as the live preview.

The settlement does not trust a live cursor or a live checkpoint as market
evidence. Those records are diagnostics and causal proof; broker tick caches
are the authoritative price path.

## Complete Matrix Contract

For each eligible signal there must be exactly three rows belonging to the
frozen catalog of its channel. A row may be a completed result or an explicit
blocked result, but it may never disappear.

The report publishes, per channel and candidate:

- eligible signals;
- settled signals;
- blocked signals;
- still-open signals;
- entries, net EUR, MFE and MAE;
- the exact blocker for every non-settled signal.

An arithmetic comparison is allowed only when every eligible signal has all
three terminal, complete candidate rows with valid fingerprints, causal
registration time, complete tick coverage and verified money conversion. A
production adoption claim has stronger gates: control calibration, minimum
sample size and manual review. No report can change the live configuration.

Any position crossing broker midnight remains blocked until swap is modeled
inside every basket valuation and guard decision. A terminal-only swap
adjustment is not exact because it can change the causal exit tick.

Actual MT5 results and virtual candidate results stay separate. The report may
show their difference, but a virtual fill mismatch is not silently rewritten
to match the broker fill.

## Live Cursor Rules

The live cursor belongs to the currently active cohort, not to the process:

- with no active shadow signals, the observer clears its cursor and performs
  no history catch-up;
- an active state with processed ticks resumes after its own last identity;
- a newly registered state starts from its `registered_tick_msc`;
- if old and new states overlap, the earliest requirement is used and every
  state independently ignores ticks preceding its registration;
- archive-tail delay is retried and reported without blocking Telegram or live
  MT5 order handling.

Continuity errors expose a structured reason in the journal. A missing currency
quote does not discard an otherwise valid XAUUSD price path: the tick is kept,
money evidence is marked unavailable and only the affected result is blocked.

## Durable Hash Chain

Every persisted state event points to the last state hash previously written
for that signal and candidate. Tick-only state changes may therefore be
checkpointed without falsely linking the new state to itself.

Recovery validates the recorded state hash and the previous persisted hash.
Legacy self-linked checkpoints created before this correction are accepted
only as a migration case when their serialized state recomputes to the same
hash. New malformed chains remain blocked as `journal_hash_mismatch`.

## Settlement Inputs

The standalone settlement accepts:

- causal shadow registration records with frozen fingerprints;
- normalized provider-management events addressed to the signal;
- complete XAUUSD broker ticks for the requested UTC window;
- complete money-contract/conversion evidence for monetary results;
- actual replay/ledger rows as a separate calibration view.

It must never import the live order executor or call an MT5 order function.
Missing registration, management lineage, tick coverage, conversion evidence
or policy fingerprint produces an explicit blocked row.

## Operation

The on-demand command accepts `--since` and `--until`, writes one deterministic
JSON report and prints a short human-readable summary. It is suitable for a
day or a week. A repeated run over unchanged inputs must produce the same
result and report hash.

The watcher may invoke settlement after a session backup, but settlement
failure is non-fatal and must never delay or restart the live bot. It is not an
expensive startup prerequisite. Automatic publication ends at the last fully
closed UTC calendar day so an intraday backup never requests future ticks.

## Verification

Required regression coverage:

- idle observer followed by a new signal starts at that signal, not yesterday;
- two overlapping signals keep independent registration boundaries;
- checkpoint recovery survives tick-only state changes;
- legacy self-linked checkpoints migrate without hiding real corruption;
- stale conversion evidence preserves the price sequence and blocks money only;
- every eligible signal produces three candidate rows;
- one missing input produces three visible blocked rows, never an omission;
- repeated settlement is byte-for-byte deterministic;
- the real 27-28 August cohort settles the full registered matrix;
- the full automated test suite remains green.
