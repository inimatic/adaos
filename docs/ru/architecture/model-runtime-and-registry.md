# Model Runtime and Registry

Этот документ фиксирует целевую архитектуру слоя `models` в AdaOS: реестр
моделей, хранение артефактов, доставка весов, общие Python-окружения для
тяжелых ML-библиотек и будущий путь к model execution.

Первый MVP должен решать прикладные задачи, а не строить универсальный
ML-фреймворк:

- большие model artifacts нельзя хранить в git;
- узел должен уметь установить модель из root-hosted хранилища;
- несколько NN-навыков не должны ставить отдельную копию `torch`,
  `tensorflow`, `onnxruntime` или похожих библиотек в каждое окружение;
- NLU и face vision должны получить управляемую доставку артефактов и
  зависимостей без немедленной миграции всей логики inference.

Inference API, sessions, jobs, external API и OCI остаются целевыми слоями, но
не должны раздувать первый implementation slice.

## Граница ответственности

`models` - это не новый ML-фреймворк. Это control plane для моделей и тяжелых
model runtime ресурсов.

```text
skill
  -> models registry / readiness
      -> artifact store/cache
      -> dependency environment registry
      -> backend adapter
          -> local torch / onnx / faiss
          -> service skill provider
          -> external API provider
```

Слой `models` отвечает за:

- model manifests и идентичность модели;
- загрузку, проверку, установку, cache, prune и rollback артефактов;
- root-hosted хранение моделей для dev и малой апробации;
- общие Python dependency profiles для тяжелых ML-стеков;
- readiness backend-а и metadata;
- в целевом состоянии - inference/session/job контракты.

Навык отвечает за:

- прикладной workflow и UI;
- подготовку входных данных;
- интерпретацию результата;
- доменные thresholds;
- stream/projection publishing;
- orchestration между моделями и обычными инструментами.

Навык не должен получать наружу `torch.nn.Module`, Hugging Face tokenizer,
`onnxruntime.InferenceSession` или SDK-клиент провайдера как стабильный
публичный контракт.

## Базовые сущности

`ModelManifest`
: Небольшой YAML/JSON, который можно хранить в git. Описывает id, capability,
  owning skill, версию, backend, dependency profile, artifacts, checksums,
  размеры и provenance.

`ModelArtifact`
: Большой файл или директория вне git: `.pt`, `.onnx`, `.safetensors`, `.gguf`,
  tokenizer files, FAISS indexes, metrics, training reports.

`ModelRegistry`
: Локальный индекс, который знает, какие модели доступны, установлены,
  проверены и к какому skill/provider они относятся.

`ArtifactStore`
: Физическое хранилище больших файлов. Для MVP это root-hosted file store плюс
  локальный cache узла. Долгосрочная цель - OCI registry.

`DependencyProfile`
: Именованный набор тяжелых Python-зависимостей, например `torch-cpu-py311` или
  `onnx-cpu-py311`. Профиль материализуется как immutable shared environment по
  hash от Python version, OS, architecture, CPU/GPU/CUDA variant и package lock.

`ModelBackend`
: Адаптер, который умеет проверить готовность и, в целевом состоянии,
  исполнять модель: Torch, ONNX Runtime, service-skill HTTP provider, external
  API provider или custom entrypoint.

`ModelSession`
: Целевой stateful handle загруженной модели. Не является требованием первого
  MVP.

`ModelJob`
: Целевая долгая операция: train, reindex, evaluate, promote, rollback. Не
  является требованием первого MVP.

## Manifest

Пример model manifest для face vision:

```yaml
id: face-defect-segmentation-deeplabv3
version: 12
skill_id: new_face_vision_skill
capabilities:
  - image-segmentation
backend:
  type: torch
  dependency_profile: torch-cpu-py311
  package: adaos-models-torchvision
  entrypoint: adaos_models.torchvision.segmentation:load_deeplabv3_binary
artifacts:
  weights:
    uri: adaos-models://new_face_vision_skill/current/model.pt
    sha256: "<sha256>"
    size_bytes: 0
runtime:
  device: auto
  precision: auto
```

В skill manifest MVP модельный weight-файл объявляется прямо в секции
модельного артефакта навыка. Именно этот один файл является границей hash:
`skill push` по нему решает, нужно ли двигать `current` и `previous`.

```yaml
models:
  private: true
  artifacts:
    weights:
      path: models/face-defect/model.pt
      capability: image-segmentation
      dependency_profile: torch-cpu-py311
```

`models.private: true` marks model artifacts as private by default. Private
artifacts remain installable: install can copy a local source file into the
skill data area, or download the last Root-published version when only Root
metadata is available. The flag only affects publication: `adaos skill push`
does not upload private model changes to Root unless it is run with
`--publish-private-models`. A specific artifact can opt out with
`models.artifacts.<key>.private: false`.

Runtime helpers use the same default. `adaos.sdk.data.models.upload_model()`
and `update_model_if_changed()` skip Root upload for a private skill manifest
unless they are called with `publish_private=True`.

Пример для Neural NLU:

```yaml
id: neural-nlu-default-ru
version: 27
skill_id: neural_nlu_service_skill
capabilities:
  - intent-detection
backend:
  type: service-skill
  skill: neural_nlu_service_skill
  dependency_profile: torch-faiss-cpu-py311
artifacts:
  model:
    uri: adaos-models://neural_nlu_service_skill/current/model.pt
    sha256: "<sha256>"
  labels:
    uri: adaos-models://neural_nlu_service_skill/current/labels.json
  vocab:
    uri: adaos-models://neural_nlu_service_skill/current/vocab.json
```

## Root-Hosted Artifact Store MVP

Большие модели не кладем в git. В git должны быть только manifests, recipes,
маленькие metadata, checksums и tiny test fixtures.

MVP layout:

```text
Root-hosted artifact store
  /models/<skill_id>/<label-or-global_model_version>/<artifact>

Node-local cache
  .adaos/models/cache/<sha256-or-model-id>/

Node-local installed state
  .adaos/models/installed/<model-id>/<version>/

Runtime provider state
  .adaos/state/<provider>/
```

Правила MVP:

- `skill push` отвечает за upload model artifacts и управление labels;
- хранилище разложено по `skill_id`, без вариаций по подсетям;
- каждый skill может хранить на root до двух model slots: `current` и
  `previous`;
- `skill push` считает hash модели по одному объявленному weight-файлу;
- если hash weight-файла отличается от `current`, root переносит старый
  `current` в `previous`, а новую модель пишет в `current`;
- если hash совпадает с `current`, upload пропускается, labels не меняются;
- каждая принятая модель получает глобально наблюдаемую root version; формат
  не принципиален, главное - audit/diagnostics/rollback inspection;
- artifact в slot считается immutable после успешного upload;
- MVP не вводит subnet-specific model variants и per-model ACL;
- partial/failed download считается unsuccessful install, временный файл
  удаляется;
- checksum и size обязательны для не-тестовых artifacts;
- физически upload/download идут через существующий root backend route в
  поверхности публикации навыка, например `/api/skills/{skill_id}/models/...`;
- отдельный static/object-storage facade остается перспективной оптимизацией.

URI должны идти через resolver, чтобы позже перейти к OCI без переписывания
skill manifests:

```text
adaos-models://...
https://...
file://...
oci://...
```

## Shared Python Dependency Environments

Model registry решает веса, но не решает распухание runtime из-за повторной
установки тяжелых библиотек. Поэтому нужен минимальный registry общих Python
окружений.

Переиспользуемая единица - dependency profile, а не skill venv:

```yaml
dependency_profiles:
  torch-cpu-py311:
    python: "3.11"
    packages:
      - torch==2.2.*
      - torchvision==0.17.*
      - numpy>=1.24
```

AdaOS вычисляет env key из Python version, OS, architecture, CPU/GPU/CUDA
variant и resolved package lock. Окружение immutable: если зависимости
поменялись, создается новое окружение. Навыки и service skills получают lease
на совместимое окружение. Неиспользуемые окружения можно pruning-ить.

MVP layout:

```text
.adaos/runtimes/python/envs/<env-hash>/
.adaos/runtimes/python/locks/<env-hash>.json
.adaos/runtimes/python/wheels/
.adaos/runtimes/python/leases/<owner>.json
```

Это сохраняет process isolation для service skills, но не заставляет каждый
навык держать отдельную физическую копию `torch` или `tensorflow`.

## NLU

Neural NLU уже близок к целевой границе:

- работает как service skill;
- имеет отдельные artifacts;
- `/parse` возвращает стабильный контракт;
- rebuild создает candidate model;
- promotion явный и rollback-aware.

Первый шаг миграции - зарегистрировать artifacts и dependency profile. Общие
jobs и `ctx.models.parse_intent` можно вводить позже.

## Face Vision

`new_face_vision_skill` - stateful vision/video workload. В навыке должны
остаться upload, playback, masks, preview streams, thresholds, dice/IoU и UI.

В первый MVP переносим только:

- регистрацию uploaded `.pt` как model artifact;
- checksum/provenance;
- dependency profile для Torch/TorchVision;
- readiness/device metadata.

`ctx.models.session(...)` для frame inference стоит делать только после того,
как registry и shared dependency environments уже полезны сами по себе.

## Не-цели первого MVP

- Не строить универсальный inference API сразу.
- Не переносить training/reindex/promote NLU в общий job system сразу.
- Не делать subnet-specific variants и per-model ACL.
- Не делать OCI первым хранилищем.
- Не мигрировать всю face vision логику в ядро.
