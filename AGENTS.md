# Repository Instructions

## Temporary Working Files (Required)

- Store agent-created temporary files under the repository-root `.tmp/`
  directory. This includes one-off scripts, scratch notes, diagnostic output,
  temporary screenshots, and intermediate files that are not product artifacts.
- Do not create `.tmp_*`, `tmp-*`, or other scratch files beside tracked source
  files or at the repository root. `.tmp/` is ignored by Git; never force-add
  its contents or include them in commits.
- Keep reusable tests and tooling in their established source directories.
  Keep standard E2E run artifacts in `e2e/artifacts/` and private recovery data
  in the existing private runtime recovery directory, not in source or prompts.
- Before committing, inspect Git status and ensure no temporary working files
  are included. Do not remove unrelated user files as part of cleanup.
- Before creating a worktree or a large build/download, run
  `adaos maintenance workspace-check --reserve-gib <expected-extra-space> --json`.
  The repository policy is `.github/workspace-storage.json`. A failed or partial
  check blocks new bulk scratch allocation; first reclaim eligible data or report
  the blocker. Do not bypass the check by writing elsewhere.
- Use a separate `.tmp/<task>/` directory per task with the existing
  `managed_storage_run` manifest/lease lifecycle. Keep Git worktrees beneath that
  task directory; never put housekeeping manifests inside a worktree. Mark a task
  complete only after its work and recovery needs are resolved. Keep failed runs
  for diagnostics; pin evidence that must survive the normal retention window.
- Run `adaos maintenance workspace-clean --json` for a preview, or add `--apply`
  for bounded cleanup of completed, expired managed runs. Worktree removal uses
  Git without force and requires clean state plus HEAD reachable from local
  `origin/main`. Unknown/legacy data, dirty/unmerged/locked worktrees, active
  leases, runtime state and private recovery are protected. Adopt legacy task
  directories only after checking their contents and dependencies.

## Repository Search (Required)

- Choose the smallest relevant files or directories before searching contents.
  Locate candidate filenames first when possible, then search the owning source
  directory or integration. Expand scope only when the narrower search fails.
- Respect ignore files for ordinary source searches. Do not run unrestricted
  searches from the repository root with `rg -uuu`, `--no-ignore`, or binary
  search flags. If hidden or ignored content is needed, target its specific
  file or component directory and filter by the relevant file extensions.
- Keep runtime state, dependency trees, generated output, and recovery copies
  out of ordinary source searches. In particular, avoid traversing `.adaos/`,
  `.runtime/`, `.tmp/`, `.venv/`, `node_modules/`, `.git/`, `build/`, `site/`,
  `artifacts/`, `e2e/artifacts/`, and `test-results/` unless the task concerns
  that location. Search a named source submodule directly when it owns the code.
- For runtime investigation, first identify the relevant component, task,
  filename, or log. Do not recursively search all of `.adaos/state` or
  `.adaos/workspace` for an identifier. Limit the search to the corresponding
  records or logs, and use `-F` for literal strings.
- On this workstation, use `rg --threads 2` for directory content searches.
  For ordinary source searches, also select file types and use
  `--max-filesize 2M`; inspect a deliberately selected larger file separately
  when necessary. For example:
  `rg -n --threads 2 --max-filesize 2M -g '*.py' -F 'symbol' src/adaos/services tests`.
- Check searches that are still running after 30 seconds. Cancel that specific
  search and retry with a narrower scope unless a longer scan is explicitly
  required by the task. A tool returning a session ID has not stopped the
  command: poll it or cancel it before starting another overlapping search.
  Output limits such as `Select-Object -First` do not bound an unsuccessful
  recursive scan.

## Documentation Boundaries

- `docs-stable/` is the public, versioned documentation surface. The active
  public source for GitHub Pages is `docs-stable/2027/`; it should describe
  implemented, supported, or explicitly beta behavior in user-facing language.
- `docs-development/` is a private documentation repository. It owns platform
  development documents, target architecture, roadmaps, evidence, launch
  planning, and Product Management work. Product folders under
  `docs-development/products/` are for Product Management trajectories, not
  platform implementation detail.
- Do not recreate a root-level `docs/` directory. Legacy public documentation
  belongs under `docs-development/platform/` while the public site is rebuilt
  from `docs-stable/<major-version>/`.

## Development and Delivery Branches

- Use `dev2027` for ordinary development and local VS Code work. `main` is the
  release source; its successful CI delivers to the configured destinations.
- Promote changes through a PR to `main`, or an explicitly authorized release
  bootstrap. Preserve uncommitted work and unrelated local commits when switching.
- Source submodules in a development checkout may track `origin/dev2027` locally.
  Release gitlinks remain exact revisions and `.gitmodules` keeps its main defaults.
- Runtime repositories and recovery/source-checkpoint worktrees are operational
  state, not ordinary development checkouts. Preserve their assigned branch/role.
- See `.github/BRANCHING.md` for qualification and delivery boundaries.
