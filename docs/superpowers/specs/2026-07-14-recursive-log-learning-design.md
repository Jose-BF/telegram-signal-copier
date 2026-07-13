# Recursive Reliability Learning Design

**Date:** 2026-07-14

**Goal:** Turn every trustworthy bot session into cumulative regression
evidence while preserving a strict barrier between offline learning and live
Telegram-to-MT5 execution.

## Problem

The repository captures enough raw Telegram, MT5 and accounting evidence to
find failures, but the review process is still mostly manual. A defect found
today can be described in a report without becoming a permanent machine-check
for tomorrow. At the same time, several current reports overstate what their
data proves:

- a SHA-256 sidecar proves tick-file integrity, but not that broker timestamps
  were converted to UTC correctly;
- `simulation_ready` proves structural replay inputs, but not exact market
  replay;
- repeated MT5 invalid-stop requests can create hundreds of retries and alerts
  for one impossible action;
- raw provider messages can be retained while their actionable semantics are
  still missing from the canonical simulation timeline;
- post-close edits and transient ticket-adoption races inflate anomaly counts;
- legacy daily reports can show incorrect win/loss labels or account currency.

The July 13 session gives concrete examples: 12 formal signals and 57 positions
were captured and reconciled exactly in accounting, but all 12 tick replays
were blocked by a cache whose Vantage server-clock timestamps were labeled as
UTC. One invalid break-even update also generated 1,170 structurally impossible
MT5 requests. Those are reliability patterns, not strategy conclusions.

## Safety Boundary

Learning is controlled and offline:

1. Logs may discover and aggregate a pattern.
2. A recurring or material pattern may become a candidate rule.
3. A candidate must acquire an explicit fixture and regression test.
4. The candidate is evaluated in shadow mode against the complete retained
   trustworthy corpus.
5. It may be promoted only by a reviewed code change with zero hard-gate
   regressions.

No report, frequency threshold, Gemini response or generated candidate may edit
live code, change an order or promote itself. Runtime modules never import the
learning module. This creates compounding coverage without uncontrolled
self-modification.

## Architecture

Add one offline module, `recursive_log_learning.py`, that reads immutable or
regenerated pipeline artifacts and writes two compact JSON documents:

- `data/log_learning_report.json`: current whole-corpus health, hard gates,
  new and recurring patterns, and a single honest simulation verdict;
- `data/log_pattern_registry.json`: cumulative stable pattern identities,
  first/last observation, frequency, affected sessions/signals, impact and
  lifecycle state.

The watcher runs this module only after accounting replay, observed-tick replay
and provider catalog generation. It stages both documents with the session
commit. The registry is rebuilt deterministically from retained logs whenever
possible; manually reviewed lifecycle metadata is preserved separately from
derived counts so reruns do not double-count events.

Pattern fingerprints normalize volatile values such as signal IDs, ticket
numbers, prices and timestamps while retaining channel, event category,
retcode, action type and semantic template. Raw evidence references remain in
the report, so normalization never destroys traceability.

## Recursive Pattern Lifecycle

Each registry row contains:

```json
{
  "pattern_id": "mt5.invalid_stops.modify_sltp",
  "category": "execution",
  "template": "invalid stops while modifying sl/tp",
  "status": "observed",
  "first_seen_utc": "2026-07-13T06:00:00+00:00",
  "last_seen_utc": "2026-07-13T06:04:00+00:00",
  "occurrences": 1,
  "raw_events": 1170,
  "affected_signal_count": 1,
  "affected_channels": ["canal2"],
  "financial_impact": null,
  "severity": "high",
  "candidate_reason": "structural failure repeated without state change",
  "coverage": {
    "rule_version": null,
    "regression_test": null,
    "shadow_corpus_passed": false
  }
}
```

Allowed statuses are `observed`, `candidate`, `covered`, `regressed` and
`dismissed`. Derived evidence cannot directly set `covered` or `dismissed`;
those states require reviewed metadata linked to a rule and regression test.

Priority is based on severity, affected signals/days, repeat count, replay
blockage and measurable financial impact. Frequency alone is never sufficient
to authorize an execution rule.

## Session Health Contract

The learning report exposes independent layers instead of one ambiguous
`ready` label:

- **capture:** formal provider signals and raw messages retained;
- **semantics:** formal signals and management messages have canonical action
  records or an explicit non-action classification;
- **execution:** MT5 requests have terminal outcomes, with structural failures
  coalesced into incidents;
- **accounting:** every executed position is exact or explicitly reconstructed;
- **market replay:** required UTC tick contracts pass semantic time validation
  and every selected baseline trade replays exactly;
- **provenance:** the run identity and artifacts are hash-verifiable;
- **strategy simulation:** conclusions are allowed only when all applicable
  hard gates pass.

Blocked data remains useful for diagnostics and is never deleted. It is labeled
`diagnostic_only`; it cannot enter rankings or policy selection.

## Tick Time Contract V3

Vantage historical ticks expose `time_msc` in broker server-clock epoch. During
the audited summer sessions the server was UTC+3. The cache builder must:

1. request the broker range shifted to server time;
2. derive UTC by subtracting the detected offset;
3. store `source_time_basis`, `utc_offset_seconds`, detection method and
   reference observations in a v3 sidecar;
4. validate at least one known MT5 fill anchor per required day when available;
5. reject v1/v2 contracts for exact simulation and regenerate them;
6. treat offset as day-specific so broker DST changes remain representable.

Hash integrity and semantic time validity are separate fields. Provenance may
say that bytes are intact while strategy simulation remains blocked.

## Runtime Corrections From July 13

### Structurally Invalid Stops

Before submitting an SL/TP modification, validate the requested stop against
position direction, current bid/ask, symbol stop level and the existing safer
SL. A pure TP edit preserves a currently valid protective SL instead of
replacing it with an invalid break-even value.

`TRADE_RETCODE_INVALID_STOPS` is retried only when market movement can make the
same request valid. Identical requests with unchanged structural preconditions
are coalesced and fail fast into one incident. The incident records all affected
tickets and raw attempt count but sends one readable notification.

### Auditor And Notifications

Newly filled bot tickets receive an adoption grace period before orphan alerts.
Notifications use stable incident keys based on channel, provider message,
normalized content and action. Message edits after a signal is closed remain
logged but are aggregated rather than repeatedly sent as critical alerts.

### Provider Semantics

The canonical catalog distinguishes `formal_signal`, `context_setup`,
`daily_summary`, `management_only` and `unknown_candidate`. Only
`formal_signal` participates in signal completeness or strategy denominators.

Management actions preserve these dimensions:

- action: move SL, break even, close, partial close, update TP/SL or progress;
- modality: direct, conditional, optional or informational;
- target: explicit signal, inferred open signal or unresolved;
- source: deterministic parser, reviewed Gemini result or runtime action;
- execution options: one or more policy choices rather than a forced action.

Messages such as `Move SL to 4061`, `Close TP7 when happy` and fused
`TP1 ... / SL ...` updates must survive into the offline provider timeline even
when a second classifier pass labels them informational.

Image-based context is retained with media availability/hash/extraction state.
It cannot become an executable formal signal without explicit parsed levels or
reviewed evidence.

## Honest Reporting

`simulation_run_provenance.py` separates:

- `artifact_integrity_verified`: selected inputs, source and tick bytes match;
- `market_replay_verified`: selected baseline trades replay exactly;
- `conclusions_allowed`: all hard gates pass.

An immutable archive may be published for a blocked diagnostic run, but its
card and latest farm report must both say `diagnostic_only`. A blocked run has
no selected policy and no performance ranking.

Daily analysis distinguishes signal-cohort P&L from MT5 server-calendar P&L and
uses the account currency from evidence. It never calls reconstructed or
unclassified outcomes wins/losses. Legacy reports that cannot meet this
contract are either corrected or explicitly marked historical.

## Recursive Improvement Metrics

The system measures reliability growth rather than promising financial growth:

- percentage of formal signals with complete raw capture;
- percentage of management messages with canonical semantics;
- percentage of MT5 actions with one terminal incident;
- exact accounting rate;
- exact observed-tick replay rate;
- new versus recurring pattern count;
- covered-pattern recurrence and regression count;
- number of strategy trades admitted through all hard gates.

Every fixed defect adds a permanent regression test. Future sessions therefore
increase evidence and can expose new exceptions without erasing old lessons.
This is the intended compounding effect.

## Non-Goals

- No automatic live strategy changes.
- No claim of profitability from July 13 or any blocked replay.
- No deletion of a trade because it is hard to simulate.
- No use of provider pip summaries as account P&L.
- No new database, agent framework or external service.
- No replacement of the existing causal simulator.

## Verification

Tests must prove:

- server-clock tick epochs are converted to UTC and old contracts are rejected;
- a known fill anchor detects a three-hour cache shift;
- integrity success cannot imply market-replay success;
- blocked runs are archived only as diagnostic and cannot rank policies;
- invalid stops are prevalidated, coalesced and do not loop 234 times;
- pure TP changes retain a safer valid SL;
- the orphan grace period suppresses only transient new-ticket alerts;
- provider record types exclude context posts from formal-signal counts;
- deterministic management actions survive classifier disagreement;
- repeated raw event versions produce one normalized pattern with accurate raw
  counts;
- rerunning the learner is deterministic and does not double-count patterns;
- watcher commits the learning report and registry only after their builders
  succeed;
- all existing runtime and replay tests continue to pass.

## Acceptance Criteria

1. Every retained formal signal is either fully simulable or has an explicit,
   evidence-backed blocker; none is silently discarded.
2. A strategy metric is visible only when accounting, market replay and
   provenance gates all pass.
3. The July 13 invalid-stop sequence becomes one incident, not 1,170 repeated
   requests and multiple notifications.
4. The July 13 cache is rejected until regenerated with a semantically valid
   UTC v3 contract.
5. Each newly discovered recurring pattern has a stable identity and can be
   linked to a permanent test before promotion.
6. The live order path cannot import or execute generated learning candidates.
7. A full repository test run passes before publication.
