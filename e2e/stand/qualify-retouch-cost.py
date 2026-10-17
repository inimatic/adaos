"""Held-out authority-join probe: real candidate handlers with isolated metering."""
import argparse,asyncio,io,json,socket
from pathlib import Path
from PIL import Image
from adaos.e2e.retouch_fixture import RetouchFixture,digest

parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--owned-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
root=Path.cwd().resolve();owned=args.owned_root.resolve();out=args.output.resolve()
if not owned.is_relative_to(root/'.tmp/retouch-browser-fixtures') or owned==root/'.tmp/retouch-browser-fixtures' or owned.exists():
 parser.error('A fresh owned fixture directory is required')
if not out.is_relative_to(root/'e2e/artifacts') or out==root/'e2e/artifacts':parser.error('An E2E artifact directory is required')
out.mkdir(parents=True,exist_ok=True)
# Windows initializes its internal socketpair when the event loop is created.
# After that setup no connection, including the real local API, is permitted.
event_loop=asyncio.new_event_loop()
def denied(sock,address):raise RuntimeError('No provider or local API transport in cost authority verification')
socket.socket.connect=denied
case_ids=['measured-current-run','other-run-isolation','other-run-unknown-must-not-hide-known-current-cost','partial-confirmed-with-unknown']
(out/'verification-spec.json').write_text(json.dumps({'schema':'adaos.e2e.retouch_cost_spec.v1','cases':case_ids,
 'authority':'run-scoped metered SDK observations; never estimate, commitment or another run',
 'scope':'handler presentation + owned ledger; provider invoice and browser rendering excluded'},indent=2),encoding='utf8')
owner=RetouchFixture(owned,args.source,'owned-probe-no-network');owner.install_blob_boundary()
async def call(name,**kw):
 value=await owner.call(name,kw)
 assert value.get('ok',True),value
 return value
async def run():
 image=Image.new('RGB',(320,240),(122,100,90));image.putpixel((100,100),(190,80,70));stream=io.BytesIO();image.save(stream,format='JPEG');raw=stream.getvalue()
 upload_token=owner.upload.set((raw,'image/jpeg'))
 try:await call('upload_attachment',filename='cost-probe.jpg',field_id='source_file',media_type='image/jpeg',size_bytes=len(raw),digest=digest(raw))
 finally:owner.upload.reset(upload_token)
 ref='/api/tools/natural_retouch_skill/read_attachment/attachments/originals/'+digest(raw)[7:]+'/cost-probe.jpg'
 photo=(await call('mutate_record',kind='photos',values={'filename':'cost-probe.jpg','format':'JPEG','date':'2026-10-17','source_file':ref},request_id='probe-photo'))['item']
 await call('save_mask',photo_id=photo['id'],revision=photo['revision'],mask={'schema':'adaos.image_masks.v1','width':320,'height':240,'crop':{'x':0,'y':0,'width':320,'height':240},'permission_mode':'rectangle','context_border_px':24,'strokes':[]})
 values={'target_area':'Remove only test blemish; preserve original pixels elsewhere','strength':25,'max_run_usd':2,**{'inc_'+k:k=='skin' for k in owner.module.OPS}}
 settings=(await call('mutate_record',kind='editor_settings',id=photo['id'],revision=1,values=values,request_id='probe-settings'))['item']
 job=(await call('run_command',photo_id=photo['id'],command='analyze',revision=settings['revision']))['item']
 for command in ['check_analysis','approve_plan','continue_run','continue_run']:
  job=(await call('run_command',photo_id=photo['id'],command=command,run_id=job['id'],revision=job['revision']))['item']
 assert job['status']=='completed',job['status']
 state=await call('run_state',photo_id=photo['id'],locale='en');audit=owner.audit()
 attempts=[a for a in audit['budget']['attempts'] if a['run_id']==job['id']]
 assert len(attempts)==3 and all(a['status']=='reconciled' and a['cost_basis']=='metered' for a in attempts)
 expected=sum(a['observed_usd'] for a in attempts)
 import re
 match=re.search(r'confirmed spend:\s*([0-9.]+)\s*USD',state['cost'],re.I)
 observed=float(match[1]) if match else None
 cases=[{'id':'measured-current-run','text':state['cost'],'expected_usd':expected,'observed_usd':observed,'passed':observed is not None and abs(observed-expected)<1e-9}]
 owner.ledger.reserve(owner.module.pipeline.SPEND_SCOPE,request_id='other-run-request',run_id='other-run',input_digest='independent-known-cost',reserve_usd=2)
 owner.ledger.observe('other-run-request',receipt_ref='other-run-meter',usd=.7,basis='metered',complete=True)
 scoped=await call('run_state',photo_id=photo['id'],locale='en')
 match=re.search(r'confirmed spend:\s*([0-9.]+)\s*USD',scoped['cost'],re.I);observed_scope=float(match[1]) if match else None
 cases.append({'id':'other-run-isolation','text':scoped['cost'],'expected_usd':expected,'observed_usd':observed_scope,'passed':observed_scope is not None and abs(observed_scope-expected)<1e-9})
 owner.ledger.reserve(owner.module.pipeline.SPEND_SCOPE,request_id='other-run-unconfirmed',run_id='other-run',input_digest='other-unknown-attempt',reserve_usd=2)
 other_partial=await call('run_state',photo_id=photo['id'],locale='en')
 match=re.search(r'confirmed spend:\s*([0-9.]+)\s*USD',other_partial['cost'],re.I);other_observed=float(match[1]) if match else None
 cases.append({'id':'other-run-unknown-must-not-hide-known-current-cost','text':other_partial['cost'],'expected_usd':expected,'observed_usd':other_observed,'passed':other_observed is not None and abs(other_observed-expected)<1e-9})
 owner.ledger.reserve(owner.module.pipeline.SPEND_SCOPE,request_id='current-run-unconfirmed',run_id=job['id'],input_digest='unknown-additional-attempt',reserve_usd=2)
 partial=await call('run_state',photo_id=photo['id'],locale='en')
 # A partial account must preserve known metered cost without calling it the
 # complete total. This case is graded separately if a label requires review.
 part=re.search(r'confirmed (?:part|minimum|portion):\s*([0-9.]+)\s*USD',partial['cost'],re.I)
 cases.append({'id':'partial-confirmed-with-unknown','text':partial['cost'],'expected_known_usd':expected,
               'observed_known_usd':float(part[1]) if part else None,
               'passed':part is not None and abs(float(part[1])-expected)<1e-9 and 'confirmed spend: unknown' in partial['cost']})
 report={'schema':'adaos.eval.retouch_cost_authority.v1','source_digest':owner.source_digest,'environment':audit['environment'],'scope':'actual handler state text and isolated ledger; browser rendering and provider admission excluded','run_id':job['id'],'completed':True,'expected_authoritative_sdk_usd':expected,'displayed_confirmed_usd':observed,'run_cost_field':job['cost'].get('actual_usd'),'text':state['cost'],'cases':cases,'passed':all(c['passed'] for c in cases),'external_model_calls':0,'attempts':attempts}
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({'passed':report['passed'],'cases':[{k:c.get(k) for k in ['id','passed']} for c in cases]}))
 return report['passed']
try:
 passed=event_loop.run_until_complete(run())
finally:
 event_loop.close()
if not passed:raise SystemExit(1)
