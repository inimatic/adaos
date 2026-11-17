# AdaOS y-py fork

This directory vendors `y-py` 0.6.2 and Yrs 0.12.2. It preserves the Python
API used by AdaOS while fixing native document retention and GC clock ranges.

## Provenance

- y-py source: `y-py` 0.6.2 from <https://github.com/y-crdt/ypy>
- Yrs source: tag `v0.12.2` from <https://github.com/y-crdt/y-crdt>
- Ownership fix reference: y-crdt commit
  `57a0e796702bae857e7b240956274e3135e8836d`
- AdaOS source package version: `0.6.2+adaos.2`

The original sources and their MIT licenses are retained in this directory.

## Patch scope

Yrs 0.12.2 stored an `Rc<Store>` in every integrated `Branch`. The `Store`
also owns those branches, creating this native cycle:

```text
Doc -> StoreRef(Rc<Store>) -> Branch -> StoreRef(Rc<Store>)
```

Rust reference counting cannot collect that cycle. Dropping a Python `YDoc`
therefore retained its complete CRDT store. The patch changes only the branch
back-reference to `Weak<Store>` and upgrades it when a branch starts a
transaction. This is the ownership model adopted upstream after 0.12.2.

No Python API changes from y-py 0.6.2 are included.

Version `adaos.2` also fixes `BlockRange::slice`: splitting a GC interval now
shrinks its left half as well as creating the right half. Previously a
cross-client origin into a GC range left overlapping clock intervals. Encoding
and reloading that document could manufacture future clocks and prevent a Yjs
browser from converging with the server. The Python wheel suite covers both
origin directions, boundary offsets and repeated snapshot exchange. The native
suite includes a cross-client GC/tombstone regression.

## Build

Rust 1.72 or newer and Python 3.11 are required.

```shell
python -m pip install "maturin>=1.2.3,<2"
CARGO_TARGET_DIR=.cache/y-py-target maturin build --release \
  --manifest-path vendor/y-py/Cargo.toml --interpreter python
```

On PowerShell, set `$env:CARGO_TARGET_DIR = ".cache/y-py-target"` before the
`maturin` command. Keeping Cargo output outside this directory prevents Python
editable-build metadata discovery from traversing Rust build artifacts.

Normal AdaOS development, installation, and core updates resolve a checked-in
copy of the published platform wheel through the wheelhouse declared in
`pyproject.toml`; they do not build this directory or require GitHub release
asset access from the node. The adjacent flat index records the canonical
release URLs and SHA-256 digests. Release automation must update both the
published assets and these verified distribution copies together.
Use the bootstrap `--build-vendored-y-py`/`-BuildVendoredYPy` option only when
developing the fork itself. Release wheels are built by
`.github/workflows/y-py-wheels.yml` for Windows, Linux x86_64 and aarch64,
and macOS.

The previous binary set is available from the GitHub release
[`y-py-v0.6.2-adaos.1`](https://github.com/inimatic/adaos/releases/tag/y-py-v0.6.2-adaos.1).
The Linux wheel must retain a `manylinux_2_17` or older compatibility tag; a
wheel linked directly on a newer Linux host is not an acceptable release
artifact even when its filename is manually tagged.

The `adaos.2` candidate wheelhouse contains qualified Windows, Linux x86_64 and
aarch64, and Intel macOS wheels. Apple Silicon deliberately retains `adaos.1`
through complementary dependency markers while the native ARM runner is
unavailable. Its GC fix is not qualified or promised by this staged candidate.
The wheel index preserves that distinction; do not relabel an old ARM binary
as `adaos.2`. The `adaos.2` release assets must be published before promoting
this candidate as a release; local installation resolves the checked-in wheels.

## Core update invariant

Seeded A/B environments must remove the copied AdaOS package and every stale
`adaos-*.dist-info` directory before installing a candidate. Slot preparation
then requires exactly one AdaOS distribution at the checkout's declared
version. This keeps dependency reuse fast without allowing historical package
metadata to accumulate or make `importlib.metadata.version("adaos")`
nondeterministic.

## Channel qualification

`python e2e/stand/qualify-channel-reliability.py` runs Chrome against the real
Core `DiagnosticYRoom`, desktop admission, aiortc and DataChannel adapter in an
isolated loopback fixture. Client and browser dependencies must already be
installed. It manages its scratch lease under `.tmp/` and retains receipts under
`e2e/artifacts/`. The default case delays initial STEP2 by 6.5 seconds, allows a
live update to arrive first, transfers a 512 KiB snapshot, checks cross-engine
clocks and content, exercises RTC/YWS failures and snapshot restart, and
blackholes Yjs requests while ICE remains connected.

The initial RTC readiness contract requires STEP2. After readiness, a STEP1
probe runs every 15 seconds and requires STEP2 within 10 seconds. A live Update
alone does not renew this proof. Expiry closes only the affected Yjs channel
and activates the existing YWS fallback; the command/media channels have their
own lifecycle. Runtime diagnostics expose confirmation time, expiry and RTT.
Browser timer suspension can delay recovery work; the displayed proof still
expires by wall clock. These are protocol and recovery bounds, not a promise
that an external network or Root is always reachable.

`browser/channel-ui-reliability.mjs` additionally qualifies the full debug
Angular client against `e2e/stand/fixtures/webspace-session-core.py` started
with `--gc-origin-regression`. It checks automatic fallback, independent
command ACKs, one active provider and no page reload. Set `ADAOS_WSA_APP_URL`
and `ADAOS_WSA_CORE_URL` to the isolated client/Core endpoints.
