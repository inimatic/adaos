# AdaOS Drive beta proof, 2026-09-23

Status: accepted into the stable desktop Webspace, permission-complete, and
validated locally.

AdaOS Drive was updated directly to the current Core/Application lifecycle
contract. This proof does not claim that Builder can migrate an older
Application automatically.

## Original exact lifecycle result

- release: `adaos_drive@0.1.10`;
- release digest:
  `sha256:0048ac17db9a350289b0b878a530bbc9bda20c37ca30366c2b7ef404c70ab017`;
- Candidate: `adaos_drive-0-1-10-f404c70ab017`;
- component-update notice: `cupdate.c22dd1779d9810ce4940bceb4e`;
- stable selection source: `stable_installation`;
- Webspace/Application/root: `desktop` / `adaos_drive` / `workspace`;
- stable selection revision: 2;
- component transition: `accepted`, with `workspace_committed=true`.

The public Accept action resolves the reviewed Candidate to one exact
`RuntimeSelection`, including Webspace, Application, release digest, runtime
root, and revision. It no longer searches only by Application and release, so
the former error `The reviewed Candidate has no unambiguous RuntimeSelection`
does not occur at the authority boundary.

The real Trial path passed before stable acceptance. The stable published
Application then passed wide and compact browser E2E with 19 rendered widgets,
no page errors, and no failed requests. Run evidence is retained at
`e2e/artifacts/adaos-drive-beta-20260923/stable-final/review.json`.

## Permission-complete stable update

The follow-up `adaos_drive@0.1.11` release closes a contract drift found by a
real table-row navigation attempt:

- Candidate: `adaos_drive-0-1-11-e9c6e0768b0e`;
- release digest:
  `sha256:6993289f511d908f10f4cd491bded88999e072c7c3103080aed2e9c6e0768b0e`;
- skill package: `adaos_drive@0.1.6`, digest
  `sha256:8d98d481a8565aed45e30df356ddbea7115c5e994291a6ca5ef68b5565e552e7`;
- declared and observed permissions: `workspace.read`, `workspace.write`;
- permission-profile digest:
  `sha256:74ebf6e7fb0215f9c70e422118e367f046cfb3c1b52e741bd298e2c0dd68373b`;
- Application installation revision: 2;
- stable RuntimeSelection revision: 4;
- publisher-owner grant:
  `appgrant.5c1021442c1d84656745521203ab216b`.

Drive intentionally has no differentiated `application_roles`. Core now
materializes a reviewed publisher-owner permission grant for this case and
evaluates the release as roleless: permission profile, grant ceiling,
component capability and subject floors remain mandatory, while only the
nonexistent Application-local RBAC intersection is skipped. This removes both
failure modes observed during the update: `permission_not_declared` and
`application_grant_missing`.

The authoritative Drive package passed 19 tests. Stable API smoke then called
`get_snapshot`, selected `Annaarch`, activated that folder, and navigated back
to the root through `http://127.0.0.1:8777/api/tools/call`; every operation
returned `ok=true` under the Application access boundary.

The low-level Project lifecycle and the Application placement lifecycle remain
separate authority surfaces. An Application Trial must be placed before its
Candidate is accepted and promoted, or the exact promoted release must be
explicitly reconciled into `ApplicationInstallation` and `RuntimeSelection`.
Running a new Application Trial after low-level Workspace promotion would
correctly select the Trial again and is not an acceptance workflow.

## Bounded network snapshot update

The stable `adaos_drive@0.1.12` release removes a user-visible latency debt in
the two-panel snapshot path. A snapshot whose panels address the same source
and directory now performs one physical directory enumeration and reuses a
bounded process-local result for the other panel and immediately following
reads. The cache has a two-second TTL, a hard maximum of 64 entries, returns
copies rather than mutable shared values, and is invalidated by explicit
refresh and every Drive mutation that can affect a directory view. Runtime
drain and reset clear it completely.

- Candidate: `adaos_drive-0-1-12-4c2af41022e0`;
- release digest:
  `sha256:159b225d39d93eb46a67bf96ad562e740ea2a3b105d8b07f8e654c2af41022e0`;
- scenario package: `adaos_drive@0.1.6`, digest
  `sha256:ea99794abef3353277a15b8de2d61e38ede33c04622eb12485293e4b35edfd4b`;
- skill package: `adaos_drive@0.1.9`, digest
  `sha256:b7572a1753aa949cd59c354ed5d66b599895ed611ec4a2ba9a2e7fc5861613d6`;
- stable RuntimeSelection revision: 6;
- WorkspaceLock revision: 66, digest
  `sha256:072c43a32f8fe35d0956592c208d28ff48fca4ac1e0f25cb5d1eec0897f478b4`;
- declared permissions and permission-profile digest are unchanged.

The package passed all 20 Drive tests, focused Ruff validation of the changed
handler, and the skill validator with no issues. Trial, Application access
verification, Trial placement, Candidate acceptance, promotion, runtime
reload, and stable Application reconciliation all passed in that order.

After restarting the development runtime exclusively through `api serve` on
port 8777, one and only one listener was active and the node reported ready.
Live stable calls measured 458 ms for an expired-cache snapshot and 393 ms for
the immediately repeated snapshot, including HTTP, access-admission, workspace
setup and tool-bridge overhead. The earlier 10.9-second Drive tool execution
did not recur. The same live run selected and activated `Annaarch` and returned
to the root; all three calls completed with `ok=true` and without an
Application permission denial. The unit proof separately asserts one physical
enumeration for equal left/right views and explicit-refresh invalidation, so
the optimization does not rely on the timing difference alone.

Legacy undeclared Drive data was preserved in the private runtime recovery
area. It is not an active compatibility dependency and must not be deleted
until the native state adoption, backup/restore, and retention decision are
explicitly complete.
