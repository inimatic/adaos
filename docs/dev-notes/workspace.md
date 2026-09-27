# Workspace monorepo lifecycle

`GitSkillRepository` and `GitScenarioRepository` share the same sparse-checkout workspace.
The lifecycle is intentionally symmetrical:

1. Ensure the repository exists (clone on demand) and initialise sparse-checkout in *no-cone* mode.
2. Update the sparse pattern file with the requested `skills/<name>` or `scenarios/<name>` entry.
3. Pull the repository and wait for the directory plus manifest to materialise before returning metadata.
4. During uninstall remove the directory, drop the path from the sparse patterns, run `git rm --cached` for
   the sub-tree and reapply sparse checkout. The command is idempotent — calling uninstall twice is safe.
5. Whenever the sparse pattern list becomes empty we keep the workspace clean by reapplying sparse-checkout
   with an empty pattern list.

This flow guarantees that a follow-up `install → uninstall → install` round-trip does not leave untracked
files or stale sparse patterns, which was the root cause of `FileNotFoundError: ... not present after sync`.

## Active package materializations

The registry checkout is only a transport for mutable source. Before sparse
patterns are narrowed, synchronize them with every component in the active
`WorkspaceLock`. After the Git update, restore and verify those exact packages
from the local content-addressed store before reconciling the workspace
database.

The sparse set also retains `projects/<project_id>` for every active lock slot.
These paths contain the Project manifest and bounded public documents used by
the publish-after-promotion flow. They are not executable package authority,
but removing them would destroy publisher source while leaving the release
active.

Do not infer activation state from which paths happen to remain in the sparse
checkout. A missing active path is materialization drift: repair it without
changing the lock digest, or fail closed. Otherwise Home can silently fall back
to its built-in seed while the lock and database still claim that applications
are active.
