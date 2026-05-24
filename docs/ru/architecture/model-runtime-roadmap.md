# Model Runtime Roadmap

Дорожная карта реализации `models`. Порядок важен: сначала прикладная
инфраструктура для artifacts и тяжелых Python-зависимостей, затем миграция
навыков. Не усложняем ядро до появления реальной потребности.

## Текущая оценка

Оценка общего model runtime: **0%**.

Связанные части уже есть, но они специализированы:

- Neural NLU имеет service-skill boundary и artifact lifecycle.
- `new_face_vision_skill` имеет stateful Torch pipeline, но сам владеет
  загрузкой модели и зависимостями.
- Skill runtime уже поддерживает service skills и отдельные venv.

## Phase 1: MVP Scope and Contracts

- [ ] Описать сущности: `ModelManifest`, `ModelArtifact`, `ModelRegistry`,
  `ArtifactStore`, `DependencyProfile`, `ModelBackend`, `Capability`,
  `ModelRef`, `ArtifactRef`.
- [ ] Явно пометить `ctx.models.infer`, `ctx.models.session`, model jobs,
  external API и OCI как target-state, не как первый slice.
- [ ] Зафиксировать первые capabilities только как metadata:
  `intent-detection` и `image-segmentation`.
- [ ] Описать error/readiness shape для artifact install и dependency profile
  readiness.
- [ ] Зафиксировать минимальные поля manifest: id, capability, owning skill,
  version, artifacts, checksums, size, backend, dependency profile.
- [ ] Зафиксировать MVP-поле skill manifest для weight-файла:
  `models.artifacts.weights`.

## Phase 2: Manifest and Local Registry MVP

- [ ] Добавить schema и validation для model manifest.
- [ ] Добавить локальный registry service в ядро.
- [ ] Поддержать lookup по `model_id`.
- [ ] Поддержать lookup по capability как metadata.
- [ ] Добавить `.adaos/models/registry`.
- [ ] Добавить `.adaos/models/installed`.
- [ ] Добавить `.adaos/models/cache`.
- [ ] Записывать provenance: source URI, checksum, size, created time,
  installed time, owning skill/system component, active/candidate status.
- [ ] Проверять checksum для всех не-тестовых artifacts.
- [ ] Хранить root observable model version отдельно от skill-local labels
  `current` и `previous`.

## Phase 3: Root-Hosted Artifact Store MVP

- [ ] Выделить место на root server для dev и малой апробации.
- [ ] Определить layout:
  `/models/<skill_id>/<label-or-global_model_version>/<artifact>`.
- [ ] Сделать `skill push` владельцем upload model artifacts.
- [ ] Root двигает labels при принятом upload: старый `current` становится
  `previous`, новая модель становится `current`.
- [ ] Считать rotation hash по одному объявленному weight-файлу.
- [ ] Если hash pushed weight-файла совпадает с `current`, upload пропускается.
- [ ] Ограничить хранение: максимум два model slots на `skill_id`,
  `current` и `previous`.
- [ ] Root записывает глобально наблюдаемую version для каждого принятого
  upload; точный формат implementation-defined.
- [ ] Artifact в slot immutable после успешного upload.
- [ ] Не добавлять subnet-specific variants и per-model ACL.
- [ ] Использовать существующие root backend routes поверхности публикации
  навыка для upload/download, например `/api/skills/{skill_id}/models/...`.
- [ ] Заложить отдельный static/object-storage facade как будущую оптимизацию.
- [ ] Добавить resolver для `adaos-models://`.
- [ ] Добавить resolver для `https://` и `file://`.
- [ ] Не зашивать решения, несовместимые с будущим `oci://`.
- [ ] При partial/failed download помечать install как unsuccessful и удалять
  временный файл.
- [ ] Добавить artifact lock files для concurrent installs.
- [ ] Добавить quota/pruning policy для node-local cache.
- [ ] Добавить CLI: `adaos models list`, `show`, `install`, `verify`, `prune`.

## Phase 4: Shared Python Dependency Environments

- [ ] Добавить schema для dependency profiles.
- [ ] Вычислять immutable env key из Python version, OS, architecture,
  CPU/GPU/CUDA variant и resolved package pins.
- [ ] Добавить `.adaos/runtimes/python/envs`.
- [ ] Добавить `.adaos/runtimes/python/locks`.
- [ ] Добавить `.adaos/runtimes/python/wheels`.
- [ ] Добавить `.adaos/runtimes/python/leases`.
- [ ] Позволить service skills запрашивать dependency profile вместо
  уникального физического venv, если профиль уже есть.
- [ ] Не мутировать активное shared environment; при изменении зависимостей
  создавать новое.
- [ ] Добавить prune unused envs после освобождения leases.
- [ ] Добавить readiness diagnostics для dependency profile.

## Phase 5: Minimal Backend Readiness

- [ ] Описать минимальный backend adapter interface для artifact readiness и
  metadata.
- [ ] Добавить mock backend для тестов.
- [ ] Добавить service-skill backend adapter для Neural NLU readiness.
- [ ] Добавить Torch readiness adapter для face vision artifact/dependency
  validation.
- [ ] Добавить диагностику: dependencies, artifacts, device, memory hints,
  health.
- [ ] Отложить generalized `infer`/`session` до стабилизации registry и shared
  dependency environments.

## Phase 6: Skill Manifest and CLI Integration

- [ ] Добавить `models.artifacts.weights` или аналог в skill manifests.
- [ ] Добавить dependency profile requirements в skill manifests.
- [ ] Добавить install-time planning для artifacts и dependency profiles.
- [ ] Поддержать optional model requirements и degraded skill mode.
- [ ] CLI должен показывать model artifact status и dependency environment
  status вместе.
- [ ] Добавить test helpers для fake installed models и fake dependency
  profiles.

## Phase 7: Jobs and ModelOps Target State

- [ ] Добавить persistence для model jobs.
- [ ] Добавить общий artifact-producing job result shape.
- [ ] Добавить evaluate/report job type.
- [ ] Добавить promote/rollback primitives.
- [ ] Добавить quality gates: accuracy, macro-F1, abstain rate, latency,
  custom metrics.
- [ ] Публиковать job lifecycle events.
- [ ] Добавить CLI для job status и promotion.
- [ ] Переложить Neural NLU rebuild/reindex/promote на shared primitives без
  поломки старых команд.

## Phase 8: Migrate Neural NLU

- [ ] Зарегистрировать Neural NLU artifacts через model registry, сохранив
  существующий service-owned layout.
- [ ] Перенести тяжелые зависимости Neural NLU к shared dependency profile там,
  где это совместимо с service-skill isolation.
- [ ] Представить `/parse` как `intent-detection` metadata.
- [ ] Позже представить `/reindex` как model job.
- [ ] Позже представить curated rebuild как train job.
- [ ] Позже представить golden evaluation как evaluate job.
- [ ] Сохранить старые CLI aliases до стабилизации `adaos models`.

## Phase 9: Migrate Face Vision

- [ ] Добавить model manifest для текущей DeepLabV3 binary segmentation model.
- [ ] Регистрировать uploaded `.pt` как local/root model artifacts с checksum и
  provenance.
- [ ] Перенести Torch/TorchVision из skill-level dependencies в shared
  dependency profile, где возможно.
- [ ] Оставить в skill playback, previews, streams, thresholds, cache UI,
  dice/IoU и domain workflow.
- [ ] Добавить readiness/device diagnostics из shared model metadata.
- [ ] Заменять direct model materialization на `ctx.models.session(...)` только
  после стабилизации registry/dependency layers.
- [ ] Сохранить существующие tool names и UI behavior на время миграции.

## Phase 10: External Provider Convergence Target State

- [ ] Добавить provider registry для external APIs.
- [ ] Добавить shared contracts для chat/text generation и embeddings.
- [ ] Добавить token counting/chunking service, где backend это поддерживает.
- [ ] Добавить provider-specific option escape hatches.
- [ ] Добавить fallback policy между local и external providers.

## Phase 11: OCI Registry Target

- [ ] Описать OCI artifact layout для manifests, weights, indexes, metadata.
- [ ] Добавить `oci://` resolver.
- [ ] Добавить push/pull commands для authorized maintainers.
- [ ] Добавить signature или attestation verification.
- [ ] Мигрировать root-hosted static artifacts в OCI без изменения skill
  manifests.

## MVP Acceptance Criteria

- [ ] Навык может объявить model requirement без хранения весов в git.
- [ ] `skill push` загружает изменившиеся model artifacts на root под
  `skill_id`.
- [ ] Rotation hash считается только по объявленному weight-файлу.
- [ ] Root хранит только `current` и `previous` версии для каждого
  `skill_id`.
- [ ] Installed artifacts проверяются checksum и видны через CLI.
- [ ] Partial download не оставляет installed artifact.
- [ ] Навык может объявить heavy dependency profile без отдельной физической
  копии Torch/TensorFlow в своем окружении.
- [ ] Neural NLU описан как `intent-detection` provider.
- [ ] Face vision регистрирует uploaded model artifact.
- [ ] Архитектура остается совместимой с будущим OCI registry.

## Правило миграции

Не мигрировать навыки первыми. Сначала заложить только registry, root artifact
store, manifest, shared dependency environments и minimal backend readiness в
ядре. Затем мигрировать Neural NLU и face vision как пилоты. General inference,
sessions, jobs, external APIs и OCI добавлять только когда пилотам это
понадобится.
