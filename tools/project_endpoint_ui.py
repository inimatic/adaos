"""Offline WebUI endpoint preparation; performs no requests or action dispatch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adaos.services.endpoint_ui import endpoint_profile, project_endpoint_ui, resolve_endpoint_ui_action


def read(path: Path, *, maximum: int = 2 * 1024 * 1024) -> dict:
    if path.stat().st_size > maximum:
        raise ValueError("Input exceeds the offline tool budget")
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def preview_document(result: dict) -> str:
    """Local inspection renderer, deliberately containing no node bindings."""
    data = {"ok": result["ok"], "projection": result.get("projection"),
            "projection_digest": result.get("receipt", {}).get("projection_digest"),
            "findings": result.get("findings", [])}
    encoded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Endpoint UI preview</title>
<style>
body{font:18px system-ui,sans-serif;background:#f3f5f7;color:#17212b;margin:0;padding:24px}
main{max-width:800px;margin:auto}h1{font-size:26px}p{line-height:1.5;color:#475767}
section{background:white;padding:24px;border-radius:12px;margin:20px 0;border:1px solid #d7dee5}
nav{display:flex;gap:12px;flex-wrap:wrap}button{font:inherit;min-height:52px;padding:12px 18px;border:1px solid #52687c;border-radius:8px;background:#f8fafb;color:#17212b;cursor:pointer}
button:focus-visible{outline:3px solid #145ea8;outline-offset:3px}button:disabled{opacity:.45;cursor:default}
textarea{box-sizing:border-box;width:100%;min-height:250px;font:14px monospace;padding:12px;border:1px solid #a0aebb;border-radius:8px;background:white}
summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
</style><main><h1>Endpoint UI preview</h1>
<p>Experimental browser inspection of a prepared projection. Buttons create a local event below. No command is sent and no device capability is verified.</p>
<div id="controls"></div><label for="event">Local input event</label>
<textarea id="event" readonly aria-live="polite" placeholder="Select a control to inspect its normalized event."></textarea>
<details><summary>Projection identity</summary><pre id="identity"></pre></details></main>
<script id="projection-data" type="application/json">""" + encoded + """</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('projection-data').textContent);
const host=document.getElementById('controls'), output=document.getElementById('event');
document.getElementById('identity').textContent=JSON.stringify(data.projection?{profile:data.projection.profile,scope:data.projection.scope,source:data.projection.source}:data.findings,null,2);
let sequence=0;
if(!data.ok){const error=document.createElement('p');error.textContent='Projection refused. See the diagnostic below.';host.append(error);}
for(const widget of data.projection?.widgets||[]){
 const section=document.createElement('section'), title=document.createElement('h2'), controls=document.createElement('nav');
 title.textContent=widget.title;controls.setAttribute('aria-label',widget.title);section.append(title,controls);host.append(section);
 for(const definition of widget.inputs.buttons){
  const button=document.createElement('button');button.type='button';button.textContent=definition.label;button.disabled=definition.disabled;controls.append(button);
  button.addEventListener('click',()=>{
   const scope=data.projection.scope;
   const event={schema:'adaos.ui.endpoint_action.v1',event_id:'preview:'+ ++sequence,endpoint_id:scope.endpoint_id,surface_id:scope.surface_id,state_revision:scope.state_revision,generation:scope.generation,projection_digest:data.projection_digest,action_ref:definition.action_ref};
   output.value=JSON.stringify(event,null,2);
  });
 }
}
</script></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("profile", help="Inspect the exact experimental contract")
    prepare = modes.add_parser("prepare", help="Prepare selected widgets from the original WebUI")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--context", type=Path, required=True, help="Node-owned SDK keyword arguments except expires_at/now")
    prepare.add_argument("--ttl", type=int, default=120)
    prepare.add_argument("--output", type=Path, required=True, help="Node-owned preparation; contains action bindings")
    prepare.add_argument("--projection-output", type=Path, help="Receiver data only; contains no skill calls")
    prepare.add_argument("--preview-output", type=Path, help="Standalone local HTML inspection; sends nothing")
    resolve = modes.add_parser("resolve", help="Inspect the original action; never execute it")
    resolve.add_argument("--preparation", type=Path, required=True)
    resolve.add_argument("--event", type=Path, required=True)
    resolve.add_argument("--endpoint", required=True, help="Identity from the authenticated caller context")
    resolve.add_argument("--state-revision", required=True)
    resolve.add_argument("--generation", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "profile":
            result = endpoint_profile()
        elif args.mode == "prepare":
            outputs = [p.resolve() for p in (args.output, args.projection_output, args.preview_output) if p is not None]
            if len(set(outputs)) != len(outputs) or any(p in (args.source.resolve(), args.context.resolve()) for p in outputs):
                raise ValueError("Input, node preparation and receiver output paths must be distinct")
            context = read(args.context, maximum=131072)
            clock = time.time()
            result = project_endpoint_ui(read(args.source), **context, now=clock, expires_at=clock + args.ttl)
            write(args.output, result)
            if args.projection_output:
                write(args.projection_output, result["projection"] if result.get("ok") else result)
            if args.preview_output:
                args.preview_output.parent.mkdir(parents=True, exist_ok=True)
                args.preview_output.write_text(preview_document(result), encoding="utf-8")
            # Keep node action descriptors and snapshot values out of the summary.
            print(json.dumps({key: result[key] for key in ("ok", "receipt", "findings") if key in result}))
            return 0 if result["ok"] else 1
        else:
            result = resolve_endpoint_ui_action(read(args.preparation), read(args.event, maximum=4096),
                authenticated_endpoint_id=args.endpoint, current_state_revision=args.state_revision,
                current_generation=args.generation)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("ok", True) else 1
    except (ValueError, TypeError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
