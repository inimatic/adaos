"""Qualify early Builder diagnostics on exact Application source without writes."""
import argparse
import hashlib
import json
from pathlib import Path
import yaml
from adaos.sdk.builder.semantic_ui import validate_patch, validate_tool_arguments
from adaos.services.builder.traceability import digest

parser=argparse.ArgumentParser(__doc__)
parser.add_argument('--scenario',type=Path,required=True)
parser.add_argument('--skill',type=Path,required=True)
parser.add_argument('--button',required=True)
parser.add_argument('--tool',required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
root=Path.cwd().resolve();output=args.output.resolve()
if not output.is_relative_to(root/'e2e/artifacts') or output==root/'e2e/artifacts':
    parser.error('An explicit E2E artifact directory is required')
source=args.scenario/'webui.json';raw=source.read_bytes();document=json.loads(raw)
manifest=args.skill/'skill.yaml';manifest_raw=manifest.read_bytes()
tool=next(row for row in yaml.safe_load(manifest_raw).get('tools',[]) if row['name']==args.tool)
operation={'operation':'set','target_ref':args.button,'property_path':['label'],'value':'Diagnostic preview only'}

def preview(row,expected=None):
    result=validate_patch(document,[row],expected_digest=expected or digest(document),write_refs=[row['target_ref']])
    return {key:result[key] for key in ('ok','source_digest','candidate_digest','findings','effects','behavioral_acceptance')}

valid=preview(operation)
invalid_target=preview({**operation,'target_ref':args.button.rpartition(':')[0]+':__absent__'})
ternary=preview({**operation,'property_path':['enabledIf'],
                 'value':{'kind':'expression','op':'ternary','args':[True,True,False]}})
stale=preview(operation,'sha256:stale')
arguments={'photo_id':'owned-diagnostic','command':'refresh'}
valid_tool=validate_tool_arguments(arguments,tool['input_schema'])
invalid_tool=validate_tool_arguments({**arguments,'unsupported_budget_keyword':2},tool['input_schema'])
source_unchanged=source.read_bytes()==raw and manifest.read_bytes()==manifest_raw
cases={'valid_nested_button':valid,'wrong_nested_button':invalid_target,'unsupported_ternary':ternary,
       'stale_source':stale,'valid_tool_arguments':valid_tool,'unsupported_tool_keyword':invalid_tool}
passed=(valid['ok'] and valid_tool['ok'] and all(not row['ok'] for row in
        (invalid_target,ternary,stale,invalid_tool)) and source_unchanged)
receipt={'schema':'adaos.eval.builder_development_contracts.v1','ok':bool(passed),
    'source':{'webui':'sha256:'+hashlib.sha256(raw).hexdigest(),
              'tool_manifest':'sha256:'+hashlib.sha256(manifest_raw).hexdigest()},
    'cases':cases,'application_source_unchanged':source_unchanged,
    'model_calls':0,'tool_calls':0,'usd':0,
    'scope':'early syntax/selector/tool-schema diagnostics; no behavioral or real-image quality acceptance'}
output.mkdir(parents=True,exist_ok=False)
(output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ok':bool(passed),'cases':len(cases),'model_calls':0,'source_unchanged':source_unchanged}))
raise SystemExit(0 if passed else 1)
