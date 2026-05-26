# Model Runtime and Registry

This page defines the target architecture for AdaOS model execution, model
artifacts, and model delivery. It covers local neural models, external model
APIs, and service-skill providers such as Neural NLU and vision pipelines.

The goal is to let skills depend on stable capabilities instead of directly
owning heavy Python dependencies, model downloads, device placement, and
artifact lifecycle rules.

## Problem

Some skills need neural or model-backed behavior:

- intent detection and NLU ranking
- text generation, embeddings, reranking, and structured extraction
- OCR, image segmentation, image classification, and video analysis
- speech-to-text and text-to-speech
- local fine-tuning, reindexing, evaluation, and model promotion

Putting every ML library and every model file inside each skill does not scale.
Large artifacts are not suitable for git, and direct dependencies on `torch`,
`transformers`, `onnxruntime`, `faiss`, or provider SDKs make skills hard to
install, update, test, and move between nodes.

AdaOS should provide a small shared model layer that solves two concrete MVP
problems first:

- large model artifacts must live outside git and be installable by a node;
- heavyweight Python dependency stacks must not be duplicated per skill when
  several skills can use the same compatible runtime profile.

Inference APIs, training jobs, external provider convergence, and OCI delivery
remain target-state concerns. They should not make the first implementation
larger than necessary.

## Target Boundary

The model layer should be named broadly, such as `models`, rather than `nn`.
It represents a model execution and artifact control plane. Local neural
runtime and external APIs are both backend implementations behind the same
capability-oriented contract.

Skills should depend on capabilities:

```text
skill
  -> models capability API
      -> model registry
      -> artifact store/cache
      -> dependency environment registry
      -> backend adapter
          -> local torch / onnx / llama.cpp / faiss
          -> service skill provider
          -> external API provider
```

The public skill contract should expose AdaOS model handles and results, not
raw backend objects such as `torch.nn.Module`, Hugging Face tokenizers,
`onnxruntime.InferenceSession`, or provider-specific clients.

The model layer owns:

- model manifests and model identity
- artifact resolution, download, verification, install, prune, and rollback
- shared Python dependency environment planning for heavy model stacks
- backend selection and readiness
- device selection and memory-aware execution policy
- stable inference/session/job contracts
- model usage and diagnostics metadata
- provider-specific escape hatches when a skill explicitly opts in

Skills own:

- user-facing workflow and UI behavior
- domain data preparation and result interpretation
- thresholds that are domain semantics
- stream/projection publishing
- orchestration across multiple model calls and non-model tools

## Core Concepts

`ModelManifest`
: A small YAML or JSON document that can live in git. It declares model id,
  version, capabilities, backend requirements, artifact URIs, checksums, and
  operational metadata.

`ModelArtifact`
: A large file or directory outside git, such as `.pt`, `.onnx`,
  `.safetensors`, `.gguf`, tokenizer files, FAISS indexes, metrics, or training
  reports.

`ModelRegistry`
: A local index that resolves model ids and capabilities to manifests,
  installed artifacts, candidate artifacts, and backend requirements.

`ArtifactStore`
: The physical storage for large model files. During MVP this can be a
  root-hosted file store plus local node cache. The long-term target is an OCI
  registry or another content-addressed artifact backend.

`DependencyProfile`
: A named, lockable Python dependency set such as `torch-cpu-py311` or
  `onnx-cpu-py311`. Profiles are materialized as immutable shared environments
  keyed by platform, Python version, package pins, and backend variant. Skills
  lease compatible environments instead of each creating a private copy of
  `torch`, `tensorflow`, or similar libraries.

`ModelBackend`
: An adapter that knows how to execute a manifest: Torch, ONNX Runtime,
  Transformers, llama.cpp, OpenAI-compatible API, service-skill HTTP provider,
  or a custom package entrypoint.

`ModelSession`
: A stateful loaded model handle owned by AdaOS. It can keep a local model in
  memory, reuse indexes, and expose `infer` without leaking backend objects.

`ModelJob`
: A long-running operation such as train, fine-tune, reindex, evaluate, export,
  quantize, or promote. Jobs are asynchronous and artifact-producing.

## Capability API

The primary API should be capability-oriented, not framework-oriented.

Examples:

```python
result = ctx.models.parse_intent(
    text=text,
    locale="ru",
    entities=entity_resolution,
    model="default-nlu",
)

mask = ctx.models.segment_image(
    image=frame_ref,
    model="face-defect-segmentation",
    options={"threshold": 0.35},
)

vectors = ctx.models.embed(
    texts=chunks,
    model="default-embedder",
    options={"batch_size": 32},
)
```

The generic request path remains available for capabilities that do not yet
have a typed helper:

```python
result = ctx.models.infer(
    capability="image-segmentation",
    model="face-defect-segmentation",
    input={"image": frame_ref},
    options={"threshold": 0.35},
)
```

Stateful workloads use sessions:

```python
with ctx.models.session(
    capability="image-segmentation",
    model="face-defect-segmentation",
) as session:
    result = session.infer({"image": frame_ref}, options={"threshold": 0.35})
```

Training and model maintenance use jobs:

```python
job = ctx.models.jobs.submit(
    kind="train",
    capability="intent-detection",
    dataset="adaos://state/interpreter/neural_training",
    output="adaos://state/interpreter/neural_candidates",
    options={"epochs": 40, "min_macro_f1": 0.75},
)
```

## Manifests

Model manifests should be small enough to live in skill packages, the core
repository, or a registry index.

Example for a local image segmentation model:

```yaml
id: face-defect-segmentation-deeplabv3-v1
version: 1.0.0
capabilities:
  - image-segmentation
backend:
  type: torch
  dependency_profile: torch-cpu-py311
  package: adaos-models-torchvision
  entrypoint: adaos_models.torchvision.segmentation:load_deeplabv3_binary
architecture:
  name: deeplabv3_resnet50
  params:
    output_channels: 1
artifacts:
  weights:
    uri: adaos-models://vision/face-defect/deeplabv3/v1/model.pt
    sha256: "<sha256>"
    size_bytes: 0
runtime:
  device: auto
  precision: auto
```

In skill manifests, the MVP should declare the model weight file directly under
the skill-owned model requirement. The weight file is the hash boundary used by
`skill push` to decide whether root should rotate `current` and `previous`.

```yaml
models:
  artifacts:
    weights:
      path: models/face-defect/model.pt
      install_path: data/files/models/model.pt
      capability: image-segmentation
      dependency_profile: torch-cpu-py311
```

Example for Neural NLU:

```yaml
id: neural-nlu-default-ru-v1
version: 1.0.0
capabilities:
  - intent-detection
backend:
  type: service-skill
  skill: neural_nlu_service_skill
artifacts:
  model:
    uri: adaos-models://nlu/neural/default/model.pt
    sha256: "<sha256>"
  labels:
    uri: adaos-models://nlu/neural/default/labels.json
  vocab:
    uri: adaos-models://nlu/neural/default/vocab.json
  examples:
    uri: adaos-models://nlu/neural/default/examples_manifest.jsonl
operations:
  reindex: true
  train: true
  evaluate: true
  promote: true
```

## Artifact Storage

Large model artifacts should not be committed to git. Git should contain only:

- manifests
- recipes
- small metadata files
- tiny test fixtures
- checksums and provenance

The MVP storage design is:

```text
Root-hosted artifact store
  /models/<skill_id>/<label-or-global_model_version>/<artifact>

Node-local shared/system cache
  .adaos/models/cache/<sha256-or-model-id>/

Node-local shared/system installed state
  .adaos/models/installed/<model-id>/<version>/

Skill-owned installed state
  skills/.runtime/<skill_id>/<runtime-bucket>/data/files/models/<artifact>

Runtime active/candidate state
  .adaos/state/models/
  .adaos/state/nlu/neural/
  .adaos/state/interpreter/neural_candidates/
```

The root-hosted store is a pragmatic development and small-pilot mechanism. In
the MVP, model delivery is part of skill publication rather than subnet-local
state:

- `skill push` owns model upload and label management;
- storage is keyed by `skill_id`, not by `subnet_id`;
- each skill may keep up to two root-hosted model slots: `current` and
  `previous`;
- `skill push` calculates the model hash from the single declared weight file;
- when that weight-file hash equals root `current`, `skill push` skips model
  upload and leaves root labels unchanged;
- when that weight-file hash differs from root `current`, root moves the old
  `current` to `previous` and writes the new artifact as `current`;
- rollback uses the root-hosted `previous` slot as the source artifact set;
- every accepted model upload receives a globally observable root version id;
  the exact format is not important for MVP as long as it supports audit,
  diagnostics, and rollback inspection;
- artifacts in a slot are treated as immutable after successful upload; a
  changed artifact is a new accepted upload and slot rotation;
- MVP does not add subnet-specific model variants or per-model ACLs;
- failed or partial downloads are treated as unsuccessful installs and the
  temporary file is deleted.

This store should be exposed through the existing root backend routes for MVP,
under the skill publication surface, for example
`/api/skills/{skill_id}/models/...`. A separate static/object-storage facade
remains a future delivery optimization. The store must provide stable download
URLs, checksum verification, and enough metadata for reproducible installs. It
is not the final distribution mechanism.

The long-term target is an OCI artifact registry, so the MVP registry should
avoid assumptions that only work for a simple filesystem or static HTTP layout.
URIs should be abstracted behind resolvers such as:

```text
adaos-models://...
https://...
file://...
oci://...
```

The node-local `.adaos/models/cache` and `.adaos/models/installed` directories
are reserved for core, system, or explicitly shared registry-managed models.
They must not become the default storage for private skill artifacts. A skill
owns its runtime data, including model weights, FAISS indexes, and model-side
metadata, through its own runtime bucket under `data/files/models`. Shared cache
entries require an explicit sharing/lease policy; otherwise the install path is
skill-local.

### Skill SDK Surface

Skills can also manage model artifacts directly through `adaos.sdk.data.models`
when the model is produced outside `skill push`, for example after manual
fine-tuning or an operator upload in a skill UI. This path uses the same Root
slots and retention rules as `skill push`, but it does not require
`skill install`.

The MVP SDK surface is intentionally small and LLM-friendly:

```python
from adaos.sdk.data.models import (
    current_model_info,
    previous_model_info,
    update_model_if_changed,
    upload_model,
    download_model,
    download_previous_model,
)

status = current_model_info("new_face_vision_skill")

published = update_model_if_changed(
    "data/files/uploads/models/best_full_finetune_v2.pt",
    skill_id="new_face_vision_skill",
    metadata={"source": "operator_upload"},
)

restored = download_previous_model(
    "data/files/models",
    skill_id="new_face_vision_skill",
)
```

Operations:

- `upload_model(path, skill_id=..., artifact=..., skip_if_same=True)` uploads a
  file to Root and rotates `current` only when the content hash differs from the
  current Root manifest.
- `update_model_if_changed(...)` is the default helper for skill UIs and
  fine-tuning jobs. It is an upload with `skip_if_same=True`.
- `current_model_info(...)` and `previous_model_info(...)` return Root manifest
  metadata for the active and rollback slots.
- `download_model(dest, label="current", ...)` downloads a slot without
  installing the skill.
- `download_previous_model(dest, ...)` downloads the rollback slot and verifies
  the checksum when Root exposes one.

Skill-owned models downloaded through the SDK should land under the skill
runtime bucket, normally `data/files/models`. UI uploads for model files should
use `data/files/uploads/models` as the transient upload purpose. The singular
`uploads/model` path is legacy-only and should be read only for migration.

This SDK is artifact lifecycle control, not the final inference API. The
long-term `ctx.models.infer` and `ctx.models.session` APIs still own portable
model execution once the shared model runtime is ready.

## Python Dependency Environments

The artifact registry solves model weights. It does not solve runtime bloat
from repeated Python ML libraries. AdaOS therefore needs a small shared
dependency environment registry for heavyweight model stacks.

The unit of reuse is a dependency profile, not a skill venv:

```yaml
dependency_profiles:
  torch-cpu-py311:
    python: "3.11"
    packages:
      - torch==2.2.*
      - torchvision==0.17.*
      - numpy>=1.24
```

AdaOS computes an environment key from Python version, OS, architecture,
CPU/GPU/CUDA variant, and resolved package lock. The environment is immutable:
if requirements change, a new environment is created. Active skills and service
skills hold leases on shared environments, and unused environments can be
pruned.

MVP layout:

```text
.adaos/runtimes/python/envs/<env-hash>/
.adaos/runtimes/python/locks/<env-hash>.json
.adaos/runtimes/python/wheels/
.adaos/runtimes/python/leases/<owner>.json
```

This keeps process isolation for service skills while avoiding one physical
copy of `torch` or `tensorflow` per skill when profiles are compatible.

## External APIs and Local Models

External APIs and local models should share capability contracts where their
behavior overlaps:

- chat and text generation
- embeddings
- reranking
- classification
- structured extraction
- image generation
- OCR and image understanding
- speech-to-text and text-to-speech

Provider-specific options are allowed only as explicit escape hatches:

```python
ctx.models.generate(
    messages=messages,
    model="assistant-default",
    options={"temperature": 0.2, "max_output_tokens": 800},
    provider_options={
        "openai": {"reasoning_effort": "low"},
        "llama_cpp": {"n_gpu_layers": 32},
    },
)
```

This keeps normal skills portable while still allowing advanced skills to bind
to a backend when they need backend-specific behavior.

## NLU Fit

The Neural NLU provider already follows the intended direction:

- it runs as a service skill with a service-owned venv
- `torch`, `numpy`, and `faiss-cpu` stay out of the hub root venv
- `/parse` exposes a frozen provider contract
- artifacts live in service-owned runtime state
- training and rebuild flows produce candidate artifacts
- promotion is explicit and rollback-aware

In the model architecture, Neural NLU becomes an `intent-detection` backend with
reindex, train, evaluate, promote, and rollback jobs.

## Vision Skill Fit

`new_face_vision_skill` represents a stateful vision/video workload. Its UI and
workflow include uploads, frame playback, masks, previews, stream publishing,
thresholds, cache keys, and dice/IoU metrics.

Those domain and UI concerns should remain skill-owned. The part that should
move into `models` is model resource handling:

- load and validate model artifacts
- materialize the segmentation model
- select CPU/GPU
- run frame inference
- expose device and latency metadata
- manage model cache and release behavior

This requires `ModelSession` in addition to simple stateless inference.

## Non-Goals

The model layer should not become a new ML framework. It should not expose a
public API for defining arbitrary layers, optimizers, dataloaders, or tensors.

Architecture details live in backend adapters or model packages. Training code
can use PyTorch, Transformers, ONNX tooling, or other libraries internally, but
skills should depend on AdaOS model contracts.

## Open Decisions

- Exact manifest schema and validation location.
- Whether model registry state belongs under `src/adaos/services/models` or a
  lower-level domain/port split.
- Exact root version id format; only observability is required for MVP.
- Exact root backend upload/download route names; MVP should use the existing
  skill publication API surface.
- Disk pruning policy for node-local `.adaos/models`.
- Default naming for SDK surface: `ctx.models` is the current recommendation.
- How much of Neural NLU promotion state should remain NLU-specific versus move
  into shared model job/promotion primitives.
