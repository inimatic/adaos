# Media Center Security And Privacy Review - 2026-08-20

Status: reviewed and accepted for a bounded single-node, one-subnet stand
pilot. Distributed and production acceptance remain separate gates.

## Reviewed Revisions

- Core deployment/topology and service bridge: `5a4df7aa` through `598bc015`.
- Client app shell and UI-as-data: `6cadad5` through `8f11625`.
- Media Project, scenario and skills: `35129f6` through `4e9f7d1` on the
  registry feature branch.

## Trust Boundaries

| Boundary | Control | Result |
| --- | --- | --- |
| Remote deployment | trusted node inventory, exact release/package digest, reviewed plan digest, generation compare-and-switch, capability grants and target receiver identity | fail closed before remote mutation |
| Node membership | activation/release/protocol match, renewable lease and fencing epoch | stale or incompatible instances are ineligible independently from health |
| Library root | canonical root containment, no overlap, symlinks off by default, exclusions, operator-only path diagnostics | arbitrary browser paths and path escape are rejected |
| Source bytes | root-bound `register_media_file`, direct endpoint route grant, ranged read, exact source revision/fingerprint | controller and coordinator never relay or copy original media |
| Derived bytes | one bounded worker, managed namespace, exact-source witness before/after publish, atomic partial file, quota and explicit retention | no partial advertise; source media is never deleted |
| Decoder/probe | subprocess without shell, `file,pipe` protocol allowlist, one thread by default, timeout/output/RSS limits, playback pressure pause | media containers cannot initiate HTTP/RTSP/UDP egress through ffmpeg |
| Persistent service output | rotating per-process capability, loopback-only HTTP, 256 KiB envelope, 50 events/s, fixed UI topics plus exact `events.publish` manifest allowlist | service processes can publish progress/domain events without receiving an ambient root event bus |
| Provider egress | built-in provider is local-only; provider id, claims, confidence, locale/job status and retry are persisted | no external metadata or embedding provider is enabled implicitly |
| Personal state | actor/profile key, query/playback policy, shared-surface history suppression, revision-safe mutation | favorites/history/resume do not use browser identity and are not leaked by shared-TV Home |
| Voice/control | actor/profile/target context, policy-filtered candidates, ambiguity clarification, target lease and command revision | voice has the same authority as direct tools |
| Compound voice | bounded workflow request, per-step idempotency/schedule, explicit confirmation, reconcile-on-unknown | speech handler cannot execute an unreviewed multi-effect sequence |
| Diagnostics/logs | bounded fields and counts; sanitizer removes credentials, tokens, direct URLs and source/root/content paths | export contains no media bytes, secrets or unbounded logs |
| Uninstall | package/runtime removal is separate from source and derived-data retention | agent removal cannot erase external media |

## Abuse And Failure Cases

1. A forged deployment receipt, stale inventory revision, incompatible release,
   expired lease or old fencing epoch is rejected by generic core contracts.
2. A root overlap, symlink cycle, unmounted source, permission error or source
   change produces bounded error/stale/invalidated state. It does not broaden
   root authority or silently remove catalog identity.
3. A malicious media container is passed as an argument array to a constrained
   child process. Network protocols are denied; time, RSS, threads, output and
   managed-disk use are bounded. Failed output is deleted.
4. A controller cannot request media bytes. It can only submit authorized,
   revision-safe commands to a selected playback target.
5. An unavailable shard makes search and browse `partial`; known rows remain
   distinguishable from currently available sources.
6. Duplicate/perceptual evidence is review-only. No automatic merge or source
   deletion follows from a hash collision.

## Privacy Decisions

- The default enrichment, embedding and recommendation implementations are
  offline and profile-scoped. They expose their signals and support opt-out.
- Enabling an external provider requires a separate provider package with an
  egress/privacy declaration, secret capability, locale policy, rate/resource
  budget and retention review. No such provider is part of this release.
- Shared TV surfaces hide personal history/recommendations unless profile
  policy explicitly admits them. Profile selection is not treated as proof of
  a different authenticated actor.
- Node-local paths are operational secrets. They are available to the owning
  agent/operator tools but are removed from normal catalog projections and
  diagnostic export.
- Stand catalog/search responses were checked for `/mnt`, `source_path`,
  `content_ref`, direct URL candidates and embedded credentials; only
  browser-safe core media routes remained.

## Residual Risks

- `ffmpeg` and browser decoders process untrusted formats. The pilot requires
  supported patched packages and the AdaOS skill process sandbox. A dedicated
  OS media-transcode service sandbox is recommended before broad unattended
  exposure.
- One-subnet trust and authorization still require physical stand proof. No
  cross-subnet federation, automatic coordinator election or general
  multi-writer authority is admitted.
- Browser playback cannot be guaranteed after process suspension or kill;
  native Android/iOS background sessions remain deferred.
- DRM, protected streaming services and automatic source deletion are outside
  this release.

## Decision

The implementation is accepted for exact-revision local validation and a
bounded single-node trusted-subnet stand pilot. It is not production-accepted:
shared-screen browser evidence, sustained playback/resource soak, physical
node loss/handoff, rolling adapter upgrade, decoder package review and
uninstall retention must be recorded first.
