"""Builder compatibility expressions derive from the executable WebUI ABI."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _contract():
    definition = json.loads((Path(__file__).resolve().parents[2] / "abi/webui.v1.schema.json").read_text(encoding="utf-8"))["$defs"]["declarativeExpression"]
    raw = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"schema": "adaos.webui.expression_contract.v1", "authority": "abi:webui.v1.schema.json#/$defs/declarativeExpression",
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(), "definition": definition,
            "conditional": {"op": "if", "args": ["condition", "then", "else"],
                            "named": ["condition", "then", "else"]}}


def expression_contract():
    return copy.deepcopy(_contract())


def expression_findings(value, *, path="value"):
    contract = _contract()
    allowed = contract["definition"]["properties"]["op"]["enum"]
    findings = []

    def visit(node, location):
        if isinstance(node, Mapping):
            if node.get("kind") == "expression":
                op = node.get("op")
                error = None
                if op not in allowed:
                    error = f"{location}.op={op!r} is not supported; use one of {', '.join(allowed)}. Conditional: op='if', args=[condition, then, else]."
                elif op == "if" and not ("condition" in node and ("then" in node or "else" in node)):
                    if not isinstance(node.get("args"), list) or len(node["args"]) != 3:
                        error = f"{location} if expression requires args=[condition, then, else] or named branches"
                if error:
                    findings.append({"code": "webui.expression.contract_invalid", "path": location,
                                     "contract_ref": contract["authority"], "contract_version": contract["schema"],
                                     "contract_digest": contract["digest"], "supported_ops": list(allowed), "detail": error})
            for key, child in node.items():
                visit(child, f"{location}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{location}[{index}]")
    visit(value, path)
    return findings
