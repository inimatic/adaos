# CBS10 Resolved Bundle and Clean-Node Proof

Date: 2026-09-30
Scope: `CBS10-10`, the executable part of `CBS10-11`, and stable archive-only
installation on experimental Linux node `192.168.0.34`.

## Result

`CBS10-10` is complete. AdaOS can export one deterministic sealed resolved
distribution and admit it without registry access. The envelope reuses the
existing semantic catalog, release repository, and content-addressed package
store; it is not a semantic package manager or a second blob store.

`CBS10-11` is partial. The mechanism proof passes, and the clean node can fetch
the real Gmail release closure and its attestation set. The published Gmail
`0.1.11` semantic projection cannot pass thin resolution because it predates
portable native conformance evidence. The resolver correctly fails closed.
Native admission now emits a redacted portable `EvidenceClaim`; closing the
real-Application proof requires a new Gmail publication rather than mutating or
implicitly trusting the old immutable release.

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

From that clean node, the existing authenticated Root artifact rail returned
Gmail CBS Cleanroom `0.1.11`, its exact scenario and skill archives, and the
three-entry release attestation set. This proves that package/release transport
is available independently of a core Git checkout.

## Remaining CBS10-11 Closure

Publish a new Gmail release using the corrected native admission, then on the
clean node:

1. synchronize the new semantic snapshot;
2. run thin installation from empty local package/portable caches;
3. export the sealed bundle and record its trusted digest;
4. clear a second isolated cache and disable registry access;
5. admit the bundle and compare exact selections with the thin receipt;
6. provision a distinct local Gmail account/credential attachment;
7. run reviewed planning/activation, including injected failure before commit;
8. rerun import/install to confirm idempotency and scan all transported members
   for forbidden local or secret material.

The legacy `0.1.11` projection must remain unchanged. Its absence of portable
evidence is useful negative evidence that the new distribution rail fails
closed rather than manufacturing conformance.
