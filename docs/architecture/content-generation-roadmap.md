# Content Generation Roadmap

Target: [Typed Content Generation](content-generation.md).

- [ ] `[must]` CG-01: Durable schema-bound SDK with explicit purpose/input
  separation, model settings, stable request identity, local validation,
  refusal/out-of-scope/incomplete states and no automatic persistence.
- [ ] `[must]` CG-02: Route runtime generation through Root subscription
  admission/accounting. Verify request, model and observed usage without double
  charging; rejected and resumed requests must remain distinguishable.
- [ ] `[must]` CG-03: Restore README Generate/Improve in DEV Builder, review
  result before draft application, reject stale document saves, and qualify a
  real model request through the browser without changing application behavior.
- [ ] `[should]` CG-04: Explicit image input and independently stored image
  output SDK, verified modality support, limits, artifact isolation and usage.
- [ ] `[should]` CG-05: Publish reusable form-generation guidance and SDK/ABI
  discoverability for Builder; test an unrelated schema and out-of-scope prompt
  without injecting application-specific rules into Core.
