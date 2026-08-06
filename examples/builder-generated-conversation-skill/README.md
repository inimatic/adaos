# Builder-generated conversation skill

This is the public reference output expected from Builder Automation. It keeps
conversation ownership in the skill, reads only a bounded context packet,
proposes reusable memory through a Pending Action, and contributes a browser
widget through `webui.json`. It never stores a private transcript or writes Yjs
state directly.

The two deterministic operations are:

- `chat`: materializes a response through the shared conversation SDK;
- `remember_preference`: creates a consent-gated memory proposal and returns
  its Pending Action receipt.

Validate it with:

```console
adaos dev skill validate examples/builder-generated-conversation-skill
```

The example contains no credentials, external I/O, background service, or
production activation step. Generated variants must retain these boundaries or
declare the new risks and approvals explicitly.
