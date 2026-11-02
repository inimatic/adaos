"""Execute exactly one preregistered native cell; no automatic continuation."""
import argparse
import json
from pathlib import Path

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.semantic_route_trial import execute_cell

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bundle',required=True)
parser.add_argument('--host',required=True)
parser.add_argument('--cell',type=int,required=True)
parser.add_argument('--budget',required=True,help='Shared user-authorized 48-call allowance; never reset for a new cohort')
parser.add_argument('--budget-offset',type=int,default=0)
parser.add_argument('--client-source')
parser.add_argument('--client-url',default='http://127.0.0.1:8100/')
parser.add_argument('--reconcile-created-project',action='store_true',help='Read-verify interrupted setup; never repeat create or a chat send')
args=parser.parse_args()
init_ctx(Settings.from_sources())
result=execute_cell(Path(__file__).resolve().parents[2],args.bundle,args.host,args.cell,
    budget_path=args.budget,budget_offset=args.budget_offset,
    client_source=args.client_source,client_url=args.client_url,
    reconcile_created=args.reconcile_created_project)
print(json.dumps({key:value for key,value in result.items() if key!='provider_attempts'},ensure_ascii=True))
