# VM Git Self-Healing Design

## Objective

Make every production restart deterministic: the VM either launches the newest
safe `origin/main` code or stops with an explicit preserved recovery state. A
code update must never silently launch from detached HEAD, a stale rebase, or an
older commit.

## Chosen Approach

Use one Git state machine in `tools/run_bot_watch.py`. Both the long-running
watcher and the final backup call this implementation. `run_bot.bat` must not
perform its own pull/rebase/push workflow.

Alternatives rejected:

- Only change pushes to `HEAD:main`: fixes one symptom but leaves detached HEAD,
  stale rebases, and incorrect ahead/behind detection.
- Add an external deployment service: useful at larger scale, but unnecessary
  for one VM and another component that can fail independently.

## Repository State Contract

After fetching `origin/main`, classify local HEAD using ancestry, not SHA
inequality:

- `equal`: attach `main` if necessary and continue.
- `local_ahead`: attach `main` to HEAD and publish with
  `git push origin HEAD:main`.
- `remote_ahead`: attach `main` and fast-forward to `origin/main`.
- `diverged`: create a timestamped `vm-rescue-*` branch preserving local HEAD,
  then attach `main` to `origin/main`. Never discard the rescue branch.

If stale rebase metadata exists, first preserve HEAD in a rescue branch and
quit the abandoned rebase. No automatic conflict resolution is allowed.

The bot may launch only when the resulting active branch is `main`, local HEAD
equals `origin/main`, and no rebase remains active. Failure returns a non-zero
preflight result and does not spawn `main.py`.

## Session Data Publication

Session builders commit data normally, then call the same synchronizer. Pushes
always target `HEAD:main`; no command may push a stale local branch name.

If remote code advanced while local session data was being built, preserve the
data commit, update safely through the state machine, and retry only when the
relationship is fast-forwardable. Divergence is rescued and surfaced rather
than hidden.

The final batch backup delegates to a watcher CLI operation. It does not run a
second independent `git pull --rebase` sequence.

## Remote Confirmation

After MT5 and Telegram connect, the bot sends one concise startup notification
containing:

- active short commit;
- active branch;
- whether the worktree is clean;
- Git synchronization result;
- confirmation that MT5 and both Telegram channels are active.

The same fields are recorded in the journal, so delivery failures remain
auditable.

## Safety And Tests

Tests must reproduce the observed failures before implementation:

- detached HEAD with remote as ancestor;
- stale rebase blocking branch attachment;
- local-ahead publication using `HEAD:main`;
- remote-ahead fast-forward;
- divergence creating a rescue branch;
- unsafe preflight preventing `main.py` launch;
- `run_bot.bat` containing no independent pull/rebase/push;
- startup notification exposing the active commit and branch.

The complete test suite must pass before publication. The production update is
published only after the current live trading process can restart safely.
