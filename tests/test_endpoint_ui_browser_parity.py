"""Compare the admitted subset with the actual Client expression implementation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adaos.services.endpoint_ui import project_endpoint_ui

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/endpoint_ui"
BROWSER = ROOT / "src/adaos/integrations/adaos-client/src/app/runtime/state-expression.ts"
SCRIPT = r"""
import fs from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
let source = fs.readFileSync(process.argv[1], 'utf8');
const original = "import { PageState } from './page-state.service'";
if (!source.includes(original)) throw Error('Browser fixture type import changed; review the harness');
source = source.replace(original, 'type PageState = Record<string, any>');
const module = await import('data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
const vectors = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(vectors.map(v => ({
  visible: module.evaluateStateExpression(v.widget.visibleIf, v.state),
  params: v.widget.actions.map(a => module.resolveDeclarativeValue(a.params, {state:v.state})),
  disabled: v.widget.inputs.buttons.map(b => module.resolveDeclarativeValue(b.disabled ?? false, {state:v.state}))
}))));
"""


def test_media_controls_bindings_and_visibility_match_real_browser_code():
    if not shutil.which("node") or not BROWSER.exists():
        pytest.skip("Actual public Client source and Node type stripping are required")
    support = subprocess.run(["node", "-e", "process.exit(typeof require('node:module').stripTypeScriptTypes==='function'?0:1)"], capture_output=True)
    if support.returncode:
        pytest.skip("Node with built-in TypeScript type stripping is required")
    document = json.loads((FIXTURE / "media_center_controls.webui.json").read_text())
    base = json.loads((FIXTURE / "media_center_controls.context.json").read_text())
    rows = [
        ("$state.surfaceProfile == 'mobile_control'", "mobile_control", "family", "target:kitchen"),
        ("$state.surfaceProfile != 'mobile_control'", "desktop", "family", "target:kitchen"),
        ("$state.surfaceProfile === false", False, False, 0),
        ("$state.surfaceProfile == 0", 0, 0, False),
        ("$state.surfaceProfile == 0", False, "family", "target:kitchen"),
        ("$state.surfaceProfile == null", None, "family", "target:kitchen"),
        ("$state.surfaceProfile !== null", "mobile_control", "семья", "target:kitchen"),
    ]
    vectors, preparations = [], []
    for condition, actual, profile, target in rows:
        source, args = copy.deepcopy(document), copy.deepcopy(base)
        widget = source["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]
        widget["visibleIf"] = condition
        args["state"].update(surfaceProfile=actual, profileId=profile, selectedPlaybackTargetId=target, locked=False)
        widget["inputs"]["buttons"][0]["disabled"] = False
        vectors.append({"widget": widget, "state": args["state"]})
        preparations.append(project_endpoint_ui(source, **args, expires_at=1120, now=1000))
    result = subprocess.run(["node", "--input-type=module", "-e", SCRIPT, str(BROWSER)],
        input=json.dumps(vectors), capture_output=True, text=True, encoding="utf-8", check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    for browser, prepared in zip(json.loads(result.stdout), preparations, strict=True):
        assert prepared["ok"], prepared
        widgets = prepared["projection"]["widgets"]
        assert bool(widgets) == browser["visible"]
        if widgets:
            buttons = widgets[0]["inputs"]["buttons"]
            assert [b["disabled"] for b in buttons] == browser["disabled"]
            assert [prepared["node_bindings"][b["action_ref"]]["action"]["params"] for b in buttons] == browser["params"]
