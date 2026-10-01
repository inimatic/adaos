# Documentation Structure

Status: public documentation information architecture proposal.

The public documentation follows a versioned, task-first structure:

```text
docs-stable/
  2027/
    product/
    media-center/
    builder/
    onboarding/
    operations/
    architecture/
    reference/
```

## Design Rules

- **Versioned by major release**: public links can remain stable while future
  releases get their own track.
- **Task-first navigation**: start, operate, connect, and understand before
  deep reference.
- **Product pages before platform detail**: users should see outcomes first.
- **Explicit maturity**: beta and experimental pages say so at the top.
- **No private dependency**: a public user should not need
  `docs-development` to understand supported behavior.
- **No public roadmap dumping**: planned work belongs in the private
  development repository until it becomes a supported public claim.

## Suggested Next Iteration

- Split Media Center into installation, library import, playback, and beta
  feedback pages when the beta package is ready.
- Add screenshots or short videos after the UI stabilizes.
- Add a release-notes section once the first 2027 public release is cut.
- Add Russian translations only after the English public meaning stabilizes.

