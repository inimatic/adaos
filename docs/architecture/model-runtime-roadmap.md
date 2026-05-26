# Model Runtime Roadmap

This roadmap sequences the model runtime and registry work. The intended order
is practical infrastructure first, then migration of model-backed skills. The
first implementation should solve artifact delivery and shared heavy Python
dependencies before building a broad inference framework.

## Current Estimate

Implementation estimate: **0%** for the shared model runtime. Related pieces
exist in specialized systems:

- Neural NLU already has a service-skill provider boundary and artifact
  lifecycle.
- `new_face_vision_skill` already has a stateful local Torch pipeline, but it
  owns model loading and dependencies directly.
- Skill runtime already supports service skills and service-owned venvs.

## Phase 1: MVP Scope and Contracts

- [ ] Define model vocabulary: `ModelManifest`, `ModelArtifact`,
  `ModelRegistry`, `ArtifactStore`, `DependencyProfile`, `ModelBackend`,
  `Capability`, `ModelRef`, and `ArtifactRef`.
- [ ] Explicitly mark `ctx.models.infer`, `ctx.models.session`, model jobs, and
  external API convergence as target-state items, not first-slice deliverables.
- [ ] Define the first supported capabilities only as metadata:
  `intent-detection` and `image-segmentation`.
- [ ] Define error/readiness shapes for artifact install and dependency
  environment readiness.
- [ ] Define the minimum manifest fields required for install:
  id, capability, owning skill, version, artifacts, checksums, size, backend,
  dependency profile.
- [ ] Define the MVP skill manifest field for a model weight file under
  `models.artifacts.weights`.

## Phase 2: Manifest and Registry MVP

- [ ] Add model manifest schema and validation.
- [ ] Add local registry service under core runtime.
- [ ] Support registry lookup by `model_id`.
- [ ] Support registry lookup by `capability` plus constraints.
- [ ] Support model requirements in skill manifests without forcing immediate
  skill migration.
- [ ] Add local registry state under `.adaos/models/registry`.
- [ ] Add shared/system installed model state under `.adaos/models/installed`.
- [ ] Add shared/system cache state under `.adaos/models/cache`.
- [ ] Keep skill-owned installed artifacts under the skill runtime bucket,
  `data/files/models`, not under the global model cache.
- [ ] Record provenance: source URI, checksum, size, created time, installed
  time, owning skill or system component, and active/candidate status.
- [ ] Add checksum verification for every non-test artifact.
- [ ] Record root observable model version separately from skill-local labels such
  as `current` and `previous`.

## Phase 3: Root-Hosted Artifact Store MVP

- [ ] Allocate a root-server storage location for development and small pilots.
- [ ] Define the root layout as
  `/models/<skill_id>/<label-or-global_model_version>/<artifact>`.
- [ ] Make `skill push` responsible for uploading model artifacts.
- [ ] Make root move labels on accepted upload: old `current` becomes
  `previous`, new artifact becomes `current`.
- [ ] Calculate model rotation hash from the single declared weight file.
- [ ] If the pushed weight-file hash equals root `current`, skip upload and keep
  labels unchanged; upload during `skill push` happens only when the model
  changed.
- [ ] Use root `previous` as the rollback source for skill model artifacts.
- [ ] Add the SDK artifact-control surface for skill-owned model storage:
  upload, update-if-changed, current/previous metadata, current download, and
  previous download without requiring `skill install`.
- [ ] Enforce the MVP retention rule: at most two stored model slots per
  `skill_id`, `current` and `previous`.
- [ ] Ensure root records a globally observable model version id for every
  accepted upload; exact format is implementation-defined.
- [ ] Treat artifacts in a slot as immutable after successful upload.
- [ ] Do not add subnet-specific model variants or per-model ACLs yet.
- [ ] Use existing root backend skill-publication routes for upload/download in
  MVP, for example `/api/skills/{skill_id}/models/...`.
- [ ] Keep a future static/object-storage facade as a delivery optimization.
- [ ] Add URI resolver for `adaos-models://`.
- [ ] Add resolver support for `https://` and `file://`.
- [ ] Keep URI resolver design compatible with future `oci://`.
- [ ] On partial or failed download, mark install unsuccessful and delete the
  temporary file.
- [ ] Add artifact lock files so concurrent installs do not corrupt
  `data/files/models`.
- [ ] Add disk quota and pruning policy for node-local shared cache separately
  from skill-owned runtime data.
- [ ] Add CLI commands:
  `adaos models list`, `search`, `show`, `install`, `verify`, `prune`.

## Phase 4: Shared Python Dependency Environments

- [ ] Add dependency profile schema for heavy model stacks.
- [ ] Compute immutable environment keys from Python version, OS, architecture,
  CPU/GPU/CUDA variant, and resolved package pins.
- [ ] Add shared environment state under `.adaos/runtimes/python/envs`.
- [ ] Add lock metadata under `.adaos/runtimes/python/locks`.
- [ ] Add wheel/download cache under `.adaos/runtimes/python/wheels`.
- [ ] Add lease records under `.adaos/runtimes/python/leases`.
- [ ] Let service skills request a dependency profile instead of owning a
  unique physical venv when the profile matches an existing environment.
- [ ] Never mutate an active shared environment; create a new environment when
  dependencies change.
- [ ] Add prune logic for unused environments after leases are released.
- [ ] Add readiness diagnostics for dependency profile availability.

## Phase 5: Minimal Backend Adapter Rails

- [ ] Define the minimal backend adapter interface for artifact readiness and
  metadata.
- [ ] Add mock backend for tests and skill development.
- [ ] Add service-skill backend adapter for providers such as Neural NLU.
- [ ] Add local Torch readiness adapter sufficient for face vision artifact and
  dependency validation.
- [ ] Add backend readiness diagnostics: dependencies, model artifacts, device,
  memory hints, and health.
- [ ] Defer generalized `infer`/`session` execution until after registry and
  dependency environments are useful.

## Phase 6: Skill Manifest and CLI Integration

- [ ] Add `models.artifacts.weights` or equivalent section to skill manifests.
- [ ] Support `install_path` under `models.artifacts.weights`, defaulting to
  `data/files/models/<declared-file-name>`.
- [ ] Add dependency profile requirements to skill manifests.
- [ ] Add install-time planning for model artifacts and dependency profiles.
- [ ] Support optional model requirements and degraded skill mode.
- [ ] Add CLI output that shows model artifact status and dependency
  environment status together.
- [ ] Add test helpers for fake installed models and fake dependency profiles.

## Phase 7: Jobs and ModelOps Target State

- [ ] Add job persistence for model operations.
- [ ] Add shared artifact-producing job result shape.
- [ ] Add evaluate/report job type.
- [ ] Add promote/rollback primitives for active model layouts.
- [ ] Add quality gate fields: accuracy, macro-F1, abstain rate, latency, and
  custom metric names.
- [ ] Add event publishing for job lifecycle.
- [ ] Add CLI commands for job status and promotion.
- [ ] Map Neural NLU rebuild/reindex/promote flows onto shared job primitives
  without breaking existing commands.

## Phase 8: Migrate Neural NLU

- [ ] Register Neural NLU artifacts through model registry while preserving the
  existing service-owned layout.
- [ ] Move Neural NLU heavy dependencies toward a shared dependency profile when
  compatible with service-skill isolation.
- [ ] Represent `/parse` as `intent-detection`.
- [ ] Represent `/reindex` as a model job.
- [ ] Represent curated rebuild as a train job.
- [ ] Represent golden evaluation as an evaluate job.
- [ ] Represent promotion and rollback through shared primitives.
- [ ] Keep existing CLI aliases until the shared `adaos models` commands are
  stable.

## Phase 9: Migrate Face Vision

- [ ] Add model manifest for the current DeepLabV3 binary segmentation model.
- [ ] Register uploaded `.pt` files as local model artifacts with checksum and
  provenance.
- [ ] Use `data/files/uploads/models` for transient model uploads and keep
  `data/files/uploads/model` only as a legacy migration fallback.
- [ ] Move Torch/TorchVision dependency from skill-level dependency to a shared
  dependency profile where feasible.
- [ ] Replace direct model materialization in the skill engine with
  `ctx.models.session(...)` only after the registry and dependency environment
  layers are stable.
- [ ] Keep frame playback, previews, streams, thresholds, cache UI, dice/IoU,
  and domain workflow in the skill.
- [ ] Add model readiness and device diagnostics from shared model metadata.
- [ ] Preserve existing tool names and UI behavior during migration.

## Phase 10: Low-Priority Rasa NLU Provider Integration

Rasa is already isolated as `rasa_nlu_service_skill` and should remain a
service-skill provider. This phase is intentionally lower priority than the
artifact/dependency MVP and the Neural NLU / face vision pilots.

- [ ] Describe Rasa as an `intent-detection` service-skill provider in the
  model registry.
- [ ] Add a `rasa-nlu-py311` dependency profile for `rasa-port` / Rasa NLU
  service dependencies, keeping upstream Rasa out of the hub root venv.
- [ ] Track the trained Rasa model as a local model artifact with
  `current`/`previous` state, without publishing node-trained models through
  `skill push` by default.
- [ ] Record the Rasa training fingerprint: skill examples, scenario examples,
  system-action examples, lookup tables, and relevant config inputs.
- [ ] Expose Rasa readiness/freshness diagnostics: service installed, service
  healthy, model trained, model stale, train failed, parse timeout.
- [ ] Surface Rasa provider status in model/NLU CLI output together with Neural
  NLU status.
- [ ] Keep Rasa `/parse` and `/train` implementation inside the service skill;
  core only manages provider metadata, dependency profile, artifacts, and
  diagnostics.
- [ ] Consider shared ModelOps jobs for Rasa training only after Neural NLU jobs
  have proven the shared job primitives.

## Phase 11: External Provider Convergence Target State

- [ ] Add provider registry for external APIs.
- [ ] Add shared contracts for chat/text generation and embeddings.
- [ ] Add token counting and chunking service where provider support is
  available.
- [ ] Add provider-specific option escape hatches.
- [ ] Add fallback policy between local and external providers.
- [ ] Add privacy and data-routing flags so skills can require local-only
  execution.

## Phase 12: OCI Registry Target

- [ ] Define OCI artifact layout for model manifests, weights, indexes, and
  metadata.
- [ ] Add `oci://` resolver.
- [ ] Add push/pull commands for authorized maintainers.
- [ ] Add signature or attestation verification.
- [ ] Migrate root-hosted static artifacts to OCI without changing skill
  manifests.

## MVP Acceptance Criteria

- [ ] A skill can declare a model requirement without placing weights in git.
- [ ] `skill push` uploads changed model artifacts to root under `skill_id`.
- [ ] Model rotation hash is based on the declared weight file only.
- [ ] If the declared weight-file hash did not change, `skill push` does not
  re-upload the model.
- [ ] Installed skill-owned artifacts land in
  `skills/.runtime/<skill_id>/<bucket>/data/files/models`.
- [ ] Installed artifacts are checksummed and listed by CLI.
- [ ] Partial downloads leave no installed artifact behind.
- [ ] Root keeps only `current` and `previous` model versions per
  `skill_id`.
- [ ] A skill can declare a heavy dependency profile without forcing a private
  copy of Torch/TensorFlow in its own environment.
- [ ] Neural NLU can be described as an `intent-detection` model provider.
- [ ] Face vision can register an uploaded model artifact.
- [ ] The design remains compatible with later OCI-backed model distribution.

## Migration Rule

Do not migrate skills first. First land only the registry, root artifact store,
manifest, shared dependency environment, and minimal backend readiness rails in
core. Then migrate Neural NLU and face vision as pilots. General inference,
session, jobs, external APIs, and OCI should follow only when a pilot needs
them.
