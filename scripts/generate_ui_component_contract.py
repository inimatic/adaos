"""Generate/check the bounded C2 Client registration and public input types."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaos.services.ui_component_contracts import component_registration_types, load_component_contract  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--client", type=Path, default=ROOT / "src/adaos/integrations/adaos-client")
    args = parser.parse_args()
    output = args.client / "src/app/runtime/component-contract.generated.ts"
    rendered = component_registration_types()
    abi_path = ROOT / "src/adaos/abi/webui.v1.schema.json"
    abi_text = abi_path.read_text(encoding="utf-8")
    abi = json.loads(abi_text)
    metadata = load_component_contract()["input_shapes"]["ContractListMetadata"]
    actual_metadata = abi["$defs"]["listInputs"]["properties"]["meta"]["items"]
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print("Component contract projection is missing/stale: " + str(output))
            return 1
        if actual_metadata != metadata:
            print("WebUI list metadata projection is stale: " + str(abi_path))
            return 1
        print("Component contract projection is current.")
    else:
        output.write_text(rendered, encoding="utf-8", newline="\n")
        if actual_metadata != metadata:
            # Preserve the formatting of unrelated ABI declarations.
            meta_at = abi_text.index('"meta":', abi_text.index('"listInputs":'))
            start = abi_text.index('"items":', meta_at) + len('"items":')
            while abi_text[start].isspace():
                start += 1
            _, length = json.JSONDecoder().raw_decode(abi_text[start:])
            indent = len(abi_text[:start].rsplit('\n', 1)[-1]) - len('"items": ')
            rendered_metadata = json.dumps(metadata, ensure_ascii=False, indent=2).replace('\n', '\n' + ' ' * indent)
            abi_path.write_text(abi_text[:start] + rendered_metadata + abi_text[start+length:], encoding="utf-8")
        print("Generated " + str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
