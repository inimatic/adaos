from __future__ import annotations

import argparse
import json
import os
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote a validated AdaOS slot into the stable root checkout")
    parser.add_argument("--slot", required=True, choices=("A", "B"))
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--root-repo-root", required=True)
    parser.add_argument("--runtime-host", default="127.0.0.1")
    parser.add_argument("--runtime-port", type=int, default=8777)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    os.environ["ADAOS_BASE_DIR"] = str(args.base_dir)
    os.environ["ADAOS_ROOT_REPO_ROOT"] = str(args.root_repo_root)

    # Import only after the stable paths are explicit. The module itself is
    # executed from the already validated candidate slot via PYTHONPATH.
    from adaos.services.core_update import promote_root_from_slot

    result = promote_root_from_slot(slot=str(args.slot))
    if bool(result.get("ok")):
        try:
            # Promotion itself does not need the live application context.
            # Initialize it only for the wrapper refresh so import preflight
            # cannot contend with the running node's SQLite state.
            from adaos.apps.bootstrap import init_ctx
            from adaos.services.autostart import default_spec, refresh_wrapper

            ctx = init_ctx()
            spec = default_spec(
                ctx,
                host=str(args.runtime_host),
                port=int(args.runtime_port),
            )
            result["wrapper_refresh"] = refresh_wrapper(ctx, spec)
        except Exception as exc:
            result["ok"] = False
            result["wrapper_refresh"] = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
