# Project entry point (Claude)

**Continuing from the claude.ai chat of 27/09/2026?** Read `docs/development/traspaso-claude-code.md` FIRST: setup steps for this laptop (ticks rebuild, smoke test) and the exact point where the work stopped.

Read first: `docs/development/estado-y-plan.md`. It is the short, current
state of the project: goal, what works, what fails, the plan, VM operation
and open questions. Update it at the end of every work block so another
session (possibly a lower-effort model) can continue without re-reading
the history.

Then, only as needed:
- `AGENTS.md`: operating rules and evidence contracts (shared with Codex).
- `docs/development/2026-09-24-simulator-goal-handoff.md`: Codex technical handoff.
- `docs/development/2026-09-24-claude-transfer-prompt.md`: Jose's original request.
- `docs/development/2026-09-08-simulation-foundation-readiness.md`: full
  history. Very long: search by date, do not read it whole.

Working with Jose:
- Spanish, short and simple. He cannot follow long technical reports; keep
  the detail in the docs, not in chat.
- He plans with a high-effort model and implements with a lower-effort one.
  Keep the plan document precise enough for that.
- Claude owns VM operations: push/pull and verifying that a change really
  runs on the VM. Every deployment still needs his explicit OK for that
  change, flat exposure before any restart and a post-deploy check
  (plan document, section 6). A push of this branch deploys (upstream is
  origin/main, watched by the VM).
- No massive strategy search without joint review. No live policy change,
  restart or order without specific authorization.
- The week 14-18/09 is not representative (heavy logging bug, stuck
  restarts): do not calibrate or draw conclusions from it.

Project facts:
- The active project is this directory, a linked Git worktree (`.git`
  points to the sibling `telegram-signal-copier` repository). It holds
  substantial uncommitted work: preserve it. Do not infer live deployment
  state from local test results.
- Pre-Claude frozen copy: `docs/development/2026-09-24-project-restore-point.md`.
  Never work inside it; a rollback must first preserve newer work elsewhere.
