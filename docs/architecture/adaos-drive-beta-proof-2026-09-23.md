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

## Concurrent directory single-flight update

The stable `adaos_drive@0.1.13` release closes the remaining race in the
bounded directory cache. Release `0.1.12` reused completed enumerations, but
two simultaneous cache misses could still enumerate the same slow network
folder twice. An invalidation that raced an enumeration could also allow that
older result to repopulate the cache.

The skill now coalesces same-directory misses behind a per-key single flight,
uses a bounded five-second follower wait, and advances an invalidation epoch.
The leader may finish for its original caller, but an enumeration from an old
epoch is never installed into the cache. A stuck filesystem call cannot block
followers indefinitely.

- Candidate: `adaos_drive-0-1-13-75e8cba467bd`;
- release digest:
  `sha256:8dc894ebcb7c786e78df8d57d6622aede96ad339a30c2999693875e8cba467bd`;
- scenario package: `adaos_drive@0.1.7`, digest
  `sha256:2d8eb0dc33497247d8bec265441937cdfd4f86fb0b5a23ac5f65b6775c717f1d`;
- skill package: `adaos_drive@0.1.11`, digest
  `sha256:2b40a58ea2e6cb326af658f1c743ea62ed91adc5d124352cc71204e945766424`;
- stable `WorkspaceLock` revision: 68, digest
  `sha256:4a3263379a773f1c52ce85edb4cea4014c28c682dfcd3e0ecedb17b8eaed4659`.

All 22 Drive tests passed, including deterministic concurrent-miss and
invalidation-during-flight tests. Strict skill validation and focused Ruff
validation passed. The exact Application release passed Final Verification,
Trial placement, live tool smoke, acceptance, Workspace promotion and stable
data transition. The stable runtime then returned a cold snapshot in 948 ms,
an immediate warm snapshot in 390 ms, and successfully selected, opened and
left `Annaarch` through the Application access boundary.

## Single-probe directory entries and runtime-stability update

The governed `adaos_drive@0.1.15` release removes repeated filesystem metadata
probes from a directory enumeration. Each `os.scandir` entry now contributes
its type and stat data once to the bounded item projection. This matters for
remote and antivirus-observed folders, where repeated `Path.is_*` and `stat`
calls amplified latency even after the cache and single-flight work above.

Release `0.1.14` was built locally first but collided with the immutable
published version namespace. Its local-only artifact was preserved for audit;
the corrected governed release was rebuilt as `0.1.15` rather than overwriting
the existing version.

- Candidate: `adaos_drive-0-1-15-8e5323d7a7e4`;
- Candidate digest:
  `sha256:fff911bc2fc961ad24bd36e59c53b7a3c012dbe88a686d53aa96819190a10ac5`;
- release digest:
  `sha256:cca9d03cb6917031a31aac007f788c6a91e93e9dcb3d27a57e558e5323d7a7e4`;
- scenario package: `adaos_drive@0.1.8`, digest
  `sha256:d00cef28e8539f1f056b7d319557a99c26b2a56ca715aa70ccc50b21909f0f91`;
- skill package: `adaos_drive@0.1.12`, digest
  `sha256:43283dea2f567284f39f39294405c48a898529006d9d2c600c66acf7df9ed354`;
- stable `WorkspaceLock` revision: 69, digest
  `sha256:15c8793ad1526435352de5b1a3ea9ac16ae6ec63a27e882d9115c8eae22d1484`;
- Application installation revision: 5;
- stable RuntimeSelection revision: 10.

All 23 Drive tests, focused Ruff validation, strict skill validation, and
scenario validation passed. The Candidate then passed Trial placement, live
permission-bound navigation, explicit acceptance, promotion, stable
installation reconciliation, and a second live stable smoke. The stable run
returned a cold snapshot in 1,066 ms and an immediate warm snapshot in 474 ms;
select, activate, and open-folder calls completed in 682 ms, 436 ms, and 447 ms
respectively. Every operation returned `ok=true`, including entry into and out
of `Annaarch`, and neither `permission_not_declared` nor
`application_grant_missing` recurred.

The same verification closed the runtime-degradation blocker that had made
Drive testing unreliable. Runtime diagnostics no longer inspect live Python
frames or task stacks, node-display lookup no longer reads configuration files
from the Webspace materialization hot path, and catalog collection plus pure
semantic resolution run outside the live event-loop thread. A selection
refresh initially still took about 2.4--2.8 seconds end to end. A remaining
0.7-second stall was traced to synchronous Application selection, Trial source,
and scenario-manifest reads during owner-loop input collection. Those external
inputs now join catalog preparation off the owner loop. The repeated live
refresh reduced owner-loop collection from 851--1,003 ms to 153 ms and total
semantic rebuild to 2.1 seconds, with no watchdog stall or event-loop lag. Only
the bounded YDoc read/apply phases remain on their owner loop.

The first Trial snapshot after a full API restart took 11.2 seconds inside the
skill execution boundary, while the immediate repeat took 790 ms and the later
stable cold run took 1.1 seconds. This points to process/import/filesystem
warm-up rather than steady-state Drive enumeration. Cold skill activation must
remain separately instrumented; it is not folded into the steady-state Drive
latency claim.

## Cold-start and tool-preflight debt closure

The `adaos_drive@0.1.16` pass exposed a source-authority mistake in the release
procedure. The optimized code had been committed in the stable Workspace, but
the Candidate was correctly built from the separate DEV source root, which was
still stale. Its successful smoke therefore proved the core runtime changes,
not the intended Drive source change. That release remains immutable audit
history and is superseded by the corrected `adaos_drive@0.1.17` release.

Release `0.1.17` was prepared only after the DEV and Workspace handler content
matched. It restores the `scandir` implementation and removes two additional
amplifiers:

- a directory entry receives its already-known relative path from the bounded
  parent enumeration, so projecting 64 entries does not resolve the source root
  and child path 64 more times;
- initial Drive state reads one coherent skill-environment snapshot instead of
  loading the same JSON document four times while merging global and Webspace
  sources.

The change has deterministic tests for one memory snapshot, one metadata probe
per directory entry, directory-cache reuse, concurrent single flight, stale
generation rejection, and the complete Drive behavior suite.

- Candidate: `adaos_drive-0-1-17-a999530dc18a`;
- release digest:
  `sha256:ad28d3d702e14e711e91d08edcf4e3dc643409e96557b14caadea999530dc18a`;
- scenario package: `adaos_drive@0.1.11`, digest
  `sha256:141e7e98aa2d300af734a56597b4968a0e6f77daf4c3dd95165c9784a922a57b`;
- skill package: `adaos_drive@0.1.15`, digest
  `sha256:4dd968d44f65b5cf64792bb075a17c28dd08bb04b0ebc8095b0fb1b475ea09ac`;
- stable `WorkspaceLock` revision: 71, digest
  `sha256:b4d5f15dc62fc465cf967e6df3a8e1c0f3c232fe6685b3bca3f32a952acd075f`;
- Application installation revision: 7;
- stable RuntimeSelection revision: 14.

All 24 Drive tests and focused Ruff validation passed. The exact Candidate
passed access verification, Trial placement, live navigation, acceptance,
promotion, stable installation reconciliation, and a clean restart through
`api serve` on port 8777. The first stable snapshot after that restart took
1,157 ms, the immediate repeat 358 ms, and select/activate/open-folder calls
took 596/348/330 ms. Every operation returned `ok=true`; the former
`permission_not_declared` failure did not recur. Internal Drive snapshot work
stayed below its 250 ms diagnostic threshold, so the remaining cold time is the
bounded first runtime dispatch rather than repeated filesystem projection.

This incident also fixes the operational rule for later Builder proofs:
Candidate evidence must name and inspect the authoritative DEV source tree;
a clean or committed stable Workspace checkout is not evidence that the DEV
checkpoint contains the same change.

The same runtime-debt pass made tool admission coherent and bounded. Tool
side effects, approval scope, permissions, and Application access are now read
from one resolved-manifest snapshot. A DEV call resolves its Project through
the server-owned current Webspace scenario before falling back to the existing
unique-owner scan. Runtime handler lookup has an exact path index, an
invalidation-aware negative cache, and an immutable source-revision fast path;
revision changes still purge modules and bytecode. Four concurrent Gmail DEV
calls consequently reduced pre-local admission from 9--10 seconds to roughly
0.3--0.6 seconds. Their remaining 1--3 second duration is inside generated
provider execution and is tracked separately from CBS/Application authority.

Finally, both YDoc materialization paths now prepare Application selections,
Trial sources, launcher entries, and desktop catalog inputs outside the YDoc
owner loop. Runtime-channel enumeration also avoids per-file filesystem
`realpath`. The previously observed 2.1-second
`ApplicationRuntimeChannel.list_selections` owner-loop stall is covered by a
regression test and did not recur in the post-change live tool runs.
