# Content Generation Roadmap

Target: [Typed Content Generation](content-generation.md).

- [x] `[must]` CG-01: Durable schema-bound SDK with explicit purpose/input
  separation, model settings, stable request identity, local validation,
  refusal/out-of-scope/incomplete states and no automatic persistence.
  Verified local contract tests cover owner isolation, Root identity reuse,
  Unicode, schema/refusal admission and malformed model bindings.
- [ ] `[must]` CG-02: Route runtime generation through Root subscription
  admission/accounting. Verify request, model and observed usage without double
  charging; rejected and resumed requests must remain distinguishable.
  Partial: real GPT-5 README, unrelated typed form and out-of-scope jobs retain
  effective model/usage and each have one Root execution. Exact SDK retries and
  terminal reads reuse their result. Crash/restart accounting and ambiguous
  submission recovery through reopened browser UI still require qualification.
- [x] `[must]` CG-03: Restore README Generate/Improve in DEV Builder, review
  result before draft application, reject stale document saves, and qualify a
  real model request through the browser without changing application behavior.
  `refinement-review-08` qualifies the enriched manifest packet, generation and
  explicit reviewed save through the browser. Schema/digest tests remain the
  guard for stale saves; interactive conflict-resolution UX is separate BIP-02 work.
- [ ] `[should]` CG-04: Explicit image input and independently stored image
  output SDK, verified modality support, limits, artifact isolation and usage.
  Partial: standalone image draft service/SDK and explicit Root Images transport
  have local contract coverage for owner isolation, digest-bound media, byte/pixel
  bounds, stable retries, invalid output and missing usage. Root modality, scoped
  image fingerprints and regional relay accounting have local tests. Remaining:
  concurrent/restart Root admission, subscription presentation and About browser
  round trip. Deployed `gpt-image-1` qualified through verified Builder ingress and
  exact Pending Actions browser approval: one 1024x1024 PNG, reported modality
  usage, identical terminal reads/retry with no new generation. Public SDK discovery
  now advertises the draft contract, not general model availability or asset apply.
- [x] `[should]` CG-05: Publish reusable form-generation guidance and SDK/ABI
  discoverability for Builder; test an unrelated schema and out-of-scope prompt
  without injecting application-specific rules into Core.
  Public SDK metadata exports content generation/polling, explicit image input,
  and text-document CAS. `content-generation-20260915-01` verifies a checklist
  schema and out-of-scope request on GPT-5 low with exact retry/terminal reads.
- [ ] `[must]` CG-06: Complete Application About using the co-owned public README,
  verified existing publisher identity, explicit icon upload/generation and a
  generic Client crop/position/zoom surface. Qualify cancel, stale apply, portable
  resource packaging and DEV-to-Beta-to-Stable round trips. Existing image drafts
  and README generation are prerequisites, not closure of this integration.
