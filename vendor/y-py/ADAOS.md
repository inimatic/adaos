# AdaOS y-py fork

This directory vendors `y-py` 0.6.2 and Yrs 0.12.2. It preserves the Python
API used by AdaOS while fixing native document retention.

## Provenance

- y-py source: `y-py` 0.6.2 from <https://github.com/y-crdt/ypy>
- Yrs source: tag `v0.12.2` from <https://github.com/y-crdt/y-crdt>
- Ownership fix reference: y-crdt commit
  `57a0e796702bae857e7b240956274e3135e8836d`
- AdaOS package version: `0.6.2+adaos.1`

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
`.github/workflows/y-py-wheels.yml` for Windows, Linux, and macOS.

The published binary set is available from the GitHub release
[`y-py-v0.6.2-adaos.1`](https://github.com/inimatic/adaos/releases/tag/y-py-v0.6.2-adaos.1).
The Linux wheel must retain a `manylinux_2_17` or older compatibility tag; a
wheel linked directly on a newer Linux host is not an acceptable release
artifact even when its filename is manually tagged.
