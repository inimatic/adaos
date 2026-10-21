"""Provider-owned capability admission; no model-name heuristics or paid probes."""
from __future__ import annotations
import hashlib
import json
import time

# Exact ids only. Account availability is still taken from the live catalog.
# Reviewed 2026-10-18: https://developers.openai.com/api/docs/models/gpt-6-astra
CATALOG = {
    'gpt-6-astra': {'efforts':['low','medium','high','xhigh','max'], 'default':None, 'web_search':True,
                    'source':'https://developers.openai.com/api/docs/models/gpt-6-astra', 'version':'2026-10-18'},
}


def descriptor(row, observed_at=None):
    identity=row.get('slug') or row.get('id')
    known=CATALOG.get(identity,{})
    capabilities=row.get('capabilities') if isinstance(row.get('capabilities'),dict) else {}
    efforts=capabilities.get('reasoning_efforts',row.get('supported_reasoning_efforts'))
    declared=isinstance(efforts,list) and all(isinstance(v,str) and v and len(v)<=40 for v in efforts)
    if not declared: efforts=known.get('efforts')
    default=capabilities.get('default_reasoning_effort',known.get('default'))
    if not efforts or default not in efforts: default=None
    web=capabilities.get('web_search',known.get('web_search'))
    if not isinstance(web,bool): web=None
    spec={'efforts':efforts,'default':default,'web_search':web,
          'source':'provider.models' if declared else known.get('source','unreported'),
          'version':str(capabilities.get('version','provider')) if declared else known.get('version','unknown')}
    revision=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
    return {'id':identity,'label':row.get('display_name') or row.get('label') or identity,
            'available':row.get('available',True) is not False,'capabilities':spec,
            'capability_revision':revision,'observed_at':observed_at or time.time(),
            'expires_at':(observed_at or time.time())+90,'reason':None}
