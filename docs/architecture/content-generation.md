# Typed Content Generation

## Contract

Applications use one SDK for schema-bound content generation, image-assisted
generation and standalone images. README is a consumer, not a Core domain rule.
Application-owned instructions define purpose and out-of-scope behavior; user
requests, current values and images are untrusted inputs, not instruction roles.
No implicit conversation, workspace files or secrets enter the context.

Generation is a durable Root job with a stable request identity. Model, reasoning,
sampling parameters, schema and supplied context are captured before submission.
Retries observe or resume the same job; a new request is an explicit new attempt.
Root remains the subscription admission and accounting authority, including for
runtime content requests. Do not bypass it with direct provider credentials or
report a second token debit at the application layer. Retain actual provider
usage, effective model and request/job correlation in result evidence.

The discoverable facades are `adaos.sdk.llm.content.generate/get`,
`adaos.sdk.llm.media.image_input`, and `adaos.sdk.developer.documents.read/write`.
Schema references are local JSON Pointers; resource IDs and dynamic reference
scopes are rejected rather than changing meaning when wrapped in the result
envelope. Standalone image output must not be advertised as available until Root
admits and accounts for its image model/modality, independently of text usage.

Typed results distinguish completed data, application out-of-scope response,
provider refusal, incomplete output and failure. Validate against JSON Schema
locally as well as requesting provider Structured Outputs. Do not truncate or
salvage invalid output into an apparently valid form. Generation never submits
a business form or saves a file. Review/apply is a separate action, guarded by
the original value or document digest. Discard preserves the user's draft.

Image inputs are explicit bounded media objects; no arbitrary URL fetching or
filesystem access. The combined request budget includes base64 image bytes,
not only text; oversize content is rejected locally without truncation or a
provider attempt. Image output is an independently addressable immutable media
artifact, not embedded Markdown or base64 in application/Yjs state. Its metadata
retains media type, digest, provenance and usage. Unsupported modality/model or
missing pricing/entitlement must be explicit, not silently downgraded to text.

The standalone transport is a durable `images.generate` Root job with an explicit
image model, independent of the text development/runtime profile. Hosted image
tools in text Responses are rejected until their separate model admission and
accounting are supported. A provider timeout must not cause automatic paid replay.
Image request fingerprints include API and subnet scope; another subnet cannot
retrieve the job. The node SDK also scopes drafts to the verified invocation
subject and executing skill, not a configured owner fallback.

`adaos.sdk.llm.images.generate/get` stages one PNG/JPEG/WebP draft with bounded
encoded bytes and decoded pixels. The image is stored by content digest and
published as a browser media descriptor, never as a local path or token-bearing
URL in UI state. The optional `context` is retained locally for review/apply
correlation and is not sent to the image provider. Root counts generated images
and token usage separately, retains missing metering as unknown, and carries
modality through regional relays. This transport is not a permission to save an
icon into an application. Deployment qualification and discoverability are tracked
in CG-04; no application should assume image availability from SDK presence alone.

## Client Pattern

A form may expose Generate/Improve, instruction input and acknowledged model
settings. Pending/error/refusal/out-of-scope states preserve editable values.
Show the proposed result before an explicit Apply; applying updates draft fields,
not persistence. README uses its existing edit/save/digest contract, with no
automatic image generation. Builder may declare application-specific purpose
and schema through this SDK without adding domain examples to Core prompts.

## Engineering Basis

Use provider-native constrained output and explicit refusal handling as described
in [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Keep image understanding and image creation separate capabilities, following
[vision inputs](https://developers.openai.com/api/docs/guides/images-vision) and
[image generation](https://developers.openai.com/api/docs/guides/image-generation).
Provider format conformance does not establish semantic correctness or permission
to persist generated content; application review and authorization still apply.
