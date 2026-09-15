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
