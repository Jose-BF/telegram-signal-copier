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
- `local_ahead`: publish only when every local-only commit starts with `data:`
  and its changed paths are exclusively under `data/`. Otherwise preserve the
  local HEAD in `vm-rescue-*` and activate the remote code without publishing
  VM-authored source changes.
- `remote_ahead`: attach `main` and fast-forward to `origin/main`.
- `diverged`: create a timestamped `vm-rescue-*` branch. Data-only local
  commits may rebase onto the remote; any conflict aborts and blocks startup.
  Non-data divergence activates the remote only after preserving the rescue.

If stale rebase metadata exists, first preserve HEAD in a rescue branch and
quit the abandoned rebase. No automatic conflict resolution is allowed. A
dirty worktree is never stashed or moved: startup is blocked so the shared
final-backup path can commit session data without hiding local files.

The bot may launch only when the active branch is `main`, local HEAD equals
`origin/main`, the worktree is clean, and no rebase remains active. Failure
returns a non-zero preflight result and does not spawn `main.py`.

## Session Data Publication

Session builders commit data normally, then call the same synchronizer. Pushes
always target `HEAD:main`; no command may push a stale local branch name.

If remote code advanced while local session data was being built, preserve the
data commit and reconcile it through the same state machine. A clean data-only
divergence may rebase after creating a rescue branch. A conflict remains on the
local data history and blocks production until it is resolved; it is never hidden.

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
