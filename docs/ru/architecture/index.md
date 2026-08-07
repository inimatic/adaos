# Архитектура

AdaOS построен как local-first runtime с многослойной Python-кодовой базой и
небольшим управляющим surface:

- CLI поднимает и использует общий `AgentContext`
- FastAPI-сервер открывает тот же runtime по HTTP
- сервисы управляют навыками, сценариями, состоянием узла и Yjs webspace
- адаптеры изолируют файловую систему, базу данных, git, аудио, секреты и
  внешние интеграции

## Основные строительные блоки

- `src/adaos/apps`: CLI, API, launcher и process entry points
- `src/adaos/services`: orchestration и runtime-логика
- `src/adaos/sdk`: публичные helper-модули для навыков и сценариев
- `src/adaos/adapters`: реализации IO
- `src/adaos/ports`: контракты для инфраструктурных зависимостей
- `src/adaos/domain`: базовые типы и доменные helper'ы

## Модель runtime

В текущей реализации:

- узел работает как `hub` или `member`
- локальный API публикует маршруты для node, skill, scenario, observe, subnet,
  join и services
- service-type skills управляются через supervisor и health-aware API
- Yjs-backed webspace дают синхронизированное состояние сценариев и desktop
- autostart и core-update встроены в lifecycle runtime

Страницы в этом разделе в первую очередь описывают уже реализованную
архитектуру. Если документ явно помечен как roadmap или target-state design,
он фиксирует планируемую эволюцию control plane, совместимую с текущим
runtime.

Текущие target-state расширения control plane описаны в:

- [Model Runtime and Registry](model-runtime-and-registry.md): target-state слой `models` для model execution, artifact registry, local/remote backend, session и job architecture
- [Model Runtime Roadmap](model-runtime-roadmap.md): чеклист реализации core model infrastructure перед миграцией Neural NLU и face vision
- [AdaOS Research Fabric](../../architecture/research-fabric.md): целевая архитектура общего исследовательского каркаса, storage/tracker/executor boundaries, интеграция MLflow и Ray и TLP reference case
- [Research Fabric Roadmap](../../architecture/research-fabric-roadmap.md): приоритетный чеклист от локального research kernel до TLP proof, второго домена и отложенного слоя aResearcher

- [Infrascope](infrascope.md): human-facing архитектура control plane поверх
  canonical system model
- [Root MCP Foundation](root-mcp-foundation.md): root-hosted agent-facing
  foundation для будущих MCP development и operations surfaces
- [Персонализация, identity и доступ](personalization-identity-access.md):
  целевая local-first модель профилей, user keys, устройств, memberships,
  ролей, capabilities, QR/link join flows, privacy zones и audit
- [Персонализация, identity и доступ: дорожная карта](personalization-identity-access-roadmap.md):
  MoSCoW-чеклист для backend slices, current-user browser settings, AdaOS
  Connect join UX, user management, grants, recovery, privacy и optional
  external identity
- [Персонализация Phase 0 Contracts](personalization-identity-access-phase0-contracts.md):
  реализованный draft contract anchor для scope lattice, versioned schemas,
  migration stance, threat model, audit и regression matrix
- [Персонализация Phase 1 Access Kernel](personalization-identity-access-phase1-kernel.md):
  реализованный backend store, policy decision, revocation, replay-guard и
  audit kernel для Phase 1
- [Персонализация Phase 2 Profile and Preferences](personalization-identity-access-phase2-profile-preferences.md):
  реализованный service/SDK slice для profile/preferences, SDK compatibility,
  header-settings model, projection и redacted audit; browser settings UI
  реализован в Phase 4
- [Персонализация Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md):
  реализованные backend public guest join, targeted invite, consent preview,
  binding, revoke и cutoff hooks; AdaOS Connect/Join Browser UI реализован
  в Phase 5
- [Персонализация Phase 4 Current-User Settings API and Browser UI](personalization-identity-access-phase4-current-user-ui.md):
  реализованные runtime API и browser header/settings panel для current-user
  profile/preferences с read-only role/membership
- [Персонализация Phase 5 AdaOS Connect Join UX and Link Management](personalization-identity-access-phase5-connect-join-ux.md):
  реализованный browser/API vertical slice для guest links, targeted invites,
  public preview/claim, link listing и access-link revocation
- [Pointer/Projection roadmap для переключения сценариев webspace](webspace-scenario-pointer-projection-roadmap.md):
  целевая архитектура и чеклист миграции от materialize-and-copy к
  pointer-first semantic rebuild
