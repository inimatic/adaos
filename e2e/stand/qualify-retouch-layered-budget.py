"""Exercise the candidate request ledger without a provider or user data."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
from PIL import Image

from adaos.services.builder.application_journeys import REQUEST_CASES, qualify_request_report
from adaos.e2e.paths import owned_subdirectory

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--owned-root',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();repo=Path.cwd().resolve()
root=args.owned_root.resolve();source=args.source.resolve()
try:
    root=owned_subdirectory(root,repo/'.tmp')
    output=owned_subdirectory(args.output,repo/'e2e/artifacts')
except ValueError:
    parser.error('Explicit owned fixture and E2E output paths required')
root.mkdir(parents=True,exist_ok=False);output.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('owned_layered_budget',source/'handlers/layer_store.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
store=module.LayerStore(root)
with sqlite3.connect(store.path) as db:
    db.execute('CREATE TABLE records(kind TEXT,id TEXT,revision INTEGER,body TEXT)')
store.migrate();store.configure_budget(2,1)
with store.transaction() as db:
    photo=store.put(db,'photo','photo',{'title':'owned synthetic photo'},0)
series=store.capture(photo['id'],photo['revision'],{'max_requests':9},'owned-budget-trial')
request={'request_id':'owned-image-request','prompt':'synthetic transport boundary'}
fresh,_=store.claim_job(series['id'],'image:retouch:0',request)
duplicate,_=store.claim_job(series['id'],'image:retouch:0',request)
before=store.budget()
store.claim_job(series['id'],'qa:0',{'request_id':'owned-text-request'})
after=store.budget();store.query({'kind':'series'});store.record(photo['id'])
admission_error=reset_error=None
try: store.claim_job(series['id'],'image:repair:0',{'request_id':'refused-image'})
except ValueError as error: admission_error=str(error)
reopened=module.LayerStore(root);reopened.migrate()
try: reopened.configure_budget(2,0)
except ValueError as error: reset_error=str(error)
with sqlite3.connect(store.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    claims=[dict(row) for row in db.execute('SELECT request_id,phase,status FROM layered_job_claims ORDER BY rowid')]
    allowance=db.execute('SELECT request_limit,prior_used FROM layered_request_budget WHERE id=1').fetchone()
    used=sum(row['phase'].startswith('image:') for row in claims)
    actual_budget={'configured':bool(allowance),'limit':allowance['request_limit'],
       'prior_used':allowance['prior_used'],'reserved':used,
       'remaining':allowance['request_limit']-allowance['prior_used']-used}
    if 'native_reserved' in after:
        # New native runs expose a separate counter without changing the
        # historical allowance. Prove the extension from stored claims rather
        # than rejecting the additional field or trusting the candidate value.
        bodies={row['id']:json.loads(row['body']) for row in db.execute('SELECT id,body FROM records')}
        actual_budget['native_reserved']=sum(
            row['phase'].startswith('image:') and
            bodies.get(row['series_id'],{}).get('snapshot',{}).get('pipeline_version','').startswith('openai-native-')
            for row in db.execute('SELECT series_id,phase FROM layered_job_claims'))
checks=[fresh is True and duplicate is False,len(claims)==2 and before==after,
        admission_error=='request_budget_exhausted',
        reset_error=='historical_usage_is_immutable' and reopened.budget()==after==actual_budget]
digest=lambda raw:'sha256:'+hashlib.sha256(raw).hexdigest()
source_digest=digest(json.dumps({p.name:digest(p.read_bytes()) for p in sorted((source/'handlers').glob('*.py'))},sort_keys=True).encode())
report={'schema':'adaos.e2e.retouch_request_allowance.v1','source_digest':source_digest,'basis':'image-request-count',
        'external_model_calls':0,'budget':{k:v for k,v in actual_budget.items() if k!='native_reserved'},
        'native_reserved':actual_budget.get('native_reserved'),'claims':claims,'duplicate_fresh':duplicate,
        'admission_error':admission_error,'reset_error':reset_error,
        'environment':{'python':sys.version.split()[0],'platform':sys.platform,'pillow':Image.__version__,'numpy':np.__version__},
        'cases':[{'id':identity,'passed':bool(passed)} for identity,passed in zip(REQUEST_CASES,checks)],'passed':all(checks)}
qualification=qualify_request_report(report,source_digest=source_digest)
report['passed']=report['passed'] and qualification['ok']
(output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'passed':report['passed'],'basis':report['basis'],'external_model_calls':0}))
if not report['passed']:raise SystemExit(1)
