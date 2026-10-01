# CBS10 Resolved Bundle and Clean-Node Proof

Date: 2026-09-30
Scope: `CBS10-10`, the executable part of `CBS10-11`, and stable archive-only
installation on experimental Linux node `192.168.0.34`.

## Result

`CBS10-10` is complete. AdaOS can export one deterministic sealed resolved
distribution and admit it without registry access. The envelope reuses the
existing semantic catalog, release repository, and content-addressed package
store; it is not a semantic package manager or a second blob store.

`CBS10-11` is partial, but the real-Application publication and offline
admission portions now pass. Gmail CBS Cleanroom `0.1.12` was built through the
governed release rail, admitted 2-of-2, accepted, promoted, and published to
`adaos-registry/main`. Its semantic projection contains two redacted portable
`EvidenceClaim` records and no credential or local evidence context. The clean
node synchronized that immutable registry revision, matched both requirements
through the read-only query ABI, and admitted the resulting exact bundle with
registry access absent from the admission path.

The clean install still exposes a `CBS10-11` prerequisite: it has no
artifact trust store and defaults to `ADAOS_ARTIFACT_ATTESTATIONS_MODE=off`.
The production export composition therefore stops with `Application
publication requires required artifact attestation mode`. The Root can return
the exact Gmail release and its attestation set over authenticated transport,
but the node still needs a Root-authenticated projection of the publisher key
before it can verify those Ed25519 attestations independently. Treating
HTTPS/mTLS as the artifact signature would weaken the existing provenance
boundary and is intentionally not used as a fallback.

## Bundle Contract

The v1 bundle carries:

- the exact semantic Application projection and requirement set;
- snapshot-pinned query and result;
- the online thin-resolution receipt and provenance-receipt digest;
- the exact `ReleasePlan`;
- only the selected canonical portable records, including portable evidence;
- every exact package archive in the selected release closure;
- an explicit statement that local authority and activation are absent.

The manifest and ZIP are deterministic. The archive SHA-256 is supplied by the
trusted registry/distribution channel and is required at admission. Admission
then validates every member and every cross-object identity before writing the
first destination byte. It rejects unknown or undeclared members, duplicate or
unsafe paths, symlinks, encrypted members, credentials/secrets, local CBS
authority, size-limit violations, canonical-digest drift, package tampering,
and closure mismatch.

Successful admission writes package bytes to `ContentAddressedPackageStore`,
portable records to `PortableContractCatalog`, and the exact plan to
`ReleaseRepository`. It returns a deterministic receipt. It does not provision
an account, create a credential, create a `BindingInstance` or `StateSpace`,
plan a transition, write a `WorkspaceLock`, or activate code.

The operator rail is exposed as `adaos project distribution-export` and
`adaos project distribution-admit`. Export requires the online registry and
configured artifact-provenance admission. Admit is deliberately registry-
offline and requires the trusted out-of-band bundle digest.

## Executable Evidence

The focused tests prove:

- cold-cache thin resolution and offline admission select the same exact
  resolution/package/portable-record digests;
- repeated bundle admission returns the same receipt;
- an unrelated registry record is not carried;
- changed member bytes and an injected `credentials/refresh-token.json` fail
  before cold-cache mutation;
- the expected bundle digest cannot be omitted or substituted;
- two subnet-local installations can create different binding/account refs
  while preserving the same portable binding and delivery digests;
- pre-commit activation failure preserves the prior lock, state generation,
  and writer authority through the existing CBS activation fault test.

## Clean Linux Node Evidence

The experimental Ubuntu node was rebuilt from a source archive with no `.git`
directory. The first run exposed two real installer defects:

1. Ubuntu Git 2.25 lacks `git sparse-checkout add`; the historical fallback
   corrupted cone patterns and failed to materialize required Applications.
2. a GitHub source archive omits the `vendor/y-py` submodule, while the exact
   patched AdaOS build is not available on PyPI.

The sparse fallback now reconstructs the selection through
`sparse-checkout set --no-cone`. Stable bootstraps install the platform-specific
patched `y-py==0.6.2+adaos.1` release wheel only after verifying a compiled-in
SHA-256. Linux x86-64/aarch64, Windows x86-64, and macOS arm64/x86-64 are
covered. The rerun installed the required `web_desktop`, `applications`, and
`users_access` projects, scenarios, and skills; initialized the node; reached
API readiness; and produced an initial core slot without Git metadata.

The historical `0.1.11` release remains useful negative evidence: it has no
portable claim and fails closed. It was not mutated. A new immutable release was
published instead:

- Project `gmail_cbs_cleanroom@0.1.12`, release digest
  `sha256:08dbd8e6bf70ff1436aaf39a24dd2100f3a79c751ffa97b18defb9f4713a9795`;
- scenario `gmail_cbs_cleanroom@0.1.9`, digest
  `sha256:353d66081638c8eb000d48994531bf6ad14bb29b64de0c6d34a9de4b106eabbc`;
- skill `gmail_cbs_cleanroom_skill@0.1.10`, digest
  `sha256:f3de83a3e530d064151ff34ef1b71bdded28ddd6b882bf3cb9deb743735b7022`;
- registry revision `c4571d2351e80dd592a7b60a2b4b396215fc4161`, with eight
  portable records including two portable evidence claims.

On `.34`, the snapshot-pinned query at that revision returned `matched` for
both Application requirements while proving `activation_performed=false` and
`local_authority_created=false`. The trusted publisher exported bundle
`sha256:2ab89eaef3415d9bc858a1aef18d622bc58906de00b0af35ae5b0ff510672780`.
The clean node admitted it to its existing stores and emitted receipt
`sha256:bc25f67323db8ba3d645018dbb199adc4346b1edef00b204c7ad9d1a44f39172`.
The receipt preserves the same two resolution digests, two package digests,
eight portable-record digests, and the exact registry snapshot. Admission
again reported no activation or local authority. Re-admission returned the
same receipt; substitution of the expected whole-bundle digest failed before
parsing or mutation.

## Remaining CBS10-11 Closure

Publication, snapshot synchronization, real Gmail bundle export, clean-node
offline admission, exact-selection comparison, idempotent receipt, and the
negative digest test are complete. The remaining closure is:

1. enroll or synchronize a Root-authenticated publisher-key trust projection;
2. run the online thin acquisition on `.34` from empty local package/portable
   caches and compare its receipt with the already admitted offline closure;
3. provision a distinct local Gmail account/credential attachment;
4. run reviewed planning/activation, including injected failure before commit;
5. scan the transported real-Application archive again in CI for forbidden
   local or secret material.

`CBS10-15` is now complete. Default-bundle generation runs after the semantic
registry commit exists, because that commit is part of the snapshot identity;
the bundle is stored by digest in the existing Root artifact store and its
immutable descriptor is keyed by exact ProjectRelease. The governed
`gmail_cbs_cleanroom` `0.1.12` republish produced bundle
`sha256:0a36276893e38402d799edadac0e68ba9bf0629adc910bb1665dd03a053f31ba`
at registry revision `255a1655ccb80c3ab1fbce694119b0c37b366220`. Repeated
descriptor and byte fetches returned an identical 71,869-byte archive whose
observed SHA-256 matched the descriptor.

`CBS10-16` then makes stable installation consume only the resulting immutable
ProjectRelease, package, and bundle artifacts. Source checkout and Git remain
available only to development builds.

The legacy `0.1.11` projection must remain unchanged. Its absence of portable
evidence is useful negative evidence that the new distribution rail fails
closed rather than manufacturing conformance.
