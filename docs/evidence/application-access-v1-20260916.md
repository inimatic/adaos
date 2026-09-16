# Application Access V1 Evidence: 2026-09-16

This record closes every non-deferred item in
[`application-access-permissions-roadmap.md`](../architecture/application-access-permissions-roadmap.md).
It records the reproducible checks run on the Windows development node; local
runtime logs, generated scenario state, and screenshots remain ignored build
artifacts.

## Automated Verification

The focused regression profile collected and passed 311 tests across:

- Application access contracts, policy kernel, management, API, SDK, Root MCP,
  registry projection, runtime grants, and tool bridge;
- Builder profiling and Final Verification, release evidence, Trial and
  publication admission;
- declarative Applications and Users & Access UI contracts, fixtures, EN/RU
  localization, and Telegram inline-keyboard projection;
- existing Application release and SDK compatibility.

Command family:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_application_access_contracts.py `
  tests/test_application_access_service.py `
  tests/test_application_access_management.py `
  tests/test_application_access_api.py `
  tests/test_application_distribution.py `
  tests/test_application_registry_projection.py `
  tests/test_builder_workspace.py `
  tests/test_root_mcp_applications_plane.py `
  tests/test_runtime_action_grants.py `
  tests/test_sdk_application_access_context.py `
  tests/test_tool_bridge.py `
  tests/test_ui_capabilities.py `
  tests/test_api_runtime_identity.py `
  tests/test_application_contracts.py `
  tests/test_sdk_applications.py `
  tests/test_router_voice_chat.py::test_io_out_chat_append_projects_telegram_controls_before_local_skip `
  -q
```

Additional checks passed:

- Ruff on all changed Python modules and tests. The legacy API server was
  checked while excluding only its pre-existing `E402`, `E401`, and `F811`
  layout findings.
- `compileall` for the access API, runtime bridge, domain, policy, management,
  Root MCP, and SDK modules.
- JSON parsing for 25 Application ABI, project-release, and UI domain-pack
  documents.
- Roadmap audit proving no unchecked `must`, `should`, or `could` item remains.

## Browser And Live Runtime

The TEST-only scenario `test_application_access_review_20260916` was exercised
against the live API at `http://127.0.0.1:8778` and the local client at
`http://127.0.0.1:8100/`. Its source digest was
`sha256:ed8a7104d681b42ffb09eadbd63ea3db46900c6a8c43cf2546e56f377fcd34ce`.

The browser run passed at `1440x1000` and `390x844`. It verified:

- release-bound access API data for `family_tasks` release
  `sha256:0d2c8fbb467448279e91590769e63bff19ddb4ba43101be7cca205702e7bc9ca`;
- a live `applications.access.show` call through the local Root MCP bridge;
- Permissions, Access, Roles, Connected Accounts, Release Readiness, Activity,
  and Users & Access keyboard activation;
- People, Guests, Children, Devices, Sessions, Application Access, and Activity
  user-centric projections;
- grant selection plus change/revoke controls, non-empty fixtures, selected-tab
  visibility, no page errors, no failed governed requests, and no horizontal
  overflow.

The generated report and screenshots are under
`e2e/artifacts/application-access/v1-20260916/` and are intentionally ignored.
The API health response reported `node_state=ready`,
`accepting_new_work=true`, and active runtime port `8778` after the run.

## Security And Release Evidence

`test_application_access_v1_end_to_end_evidence_bundle` covers owner, member,
child, and TTL guest grants; guardian step-up; guest floors; runtime allow and
deny; connected-account states and secret redaction; update review; revoke
cutoff; Users & Access correlation; audit; Final Verification; and validation
of `adaos.application.release_evidence_bundle.v1`.

Separate integration tests prove that verified Application context enters the
common runtime decision before method approval, a covered Application grant
prevents repeated method prompts, subject/holder mismatches fail closed, and
uncovered actions route to Application-aware Pending Actions with chat,
Telegram, and trusted-device voice affordances.

The V1 boundaries are deliberate: role declarations are Application-owned;
platform APIs own assignment writes; holder fields are PoP-compatible without
providing an OAuth token server; and the deterministic in-toto statement is not
signed. Those larger capabilities remain in the deferred roadmap.
