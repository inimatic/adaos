"""WSA fixture: real ingress, aiortc, Yjs and YStore with deterministic UI.

Run from the repository root; see webspace-session-regression.mjs. This does
not start a scenario compiler or use a developer's normal Core data directory.
"""
from pathlib import Path
import os
import asyncio
import threading
import json
import argparse
from contextlib import asynccontextmanager

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8789)
parser.add_argument('--runtime-dir', default='.tmp/webspace-session/runtime')
args = parser.parse_args()
os.environ['ADAOS_TOKEN'] = 'dev-local-token'
os.environ['ADAOS_BASE_DIR'] = str(Path(args.runtime_dir).resolve())
os.environ['ADAOS_CREDENTIAL_VAULT_BACKEND'] = 'file'
os.environ['ADAOS_YJS_IDLE_ROOM_RESET_S'] = '3600'
from adaos.apps.bootstrap import init_ctx
from adaos.services.settings import Settings
ctx = init_ctx(Settings(base_dir=Path(os.environ['ADAOS_BASE_DIR']), env_type='dev', owner_id='wsa-owner', subnet_id='wsa-subnet'))
from adaos.services.node_config import NodeConfig, save_config
save_config(NodeConfig(node_id='wsa-node', subnet_id='wsa-subnet', role='hub', token='dev-local-token'), ctx=ctx)
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from adaos.services.yjs import gateway_ws as gateway
from adaos.services.workspaces import index
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.domain.personalization_access import SubjectRef
from adaos.apps.api.desktop_access import require_desktop_caller, current_desktop_principal
from adaos.apps.api import node_api, personalization
from y_py import YMap
import y_py as Y
from ypy_websocket.ystore import YDocNotFound
from adaos.services.yjs.store import get_ystore_for_webspace

for desktop in ['wsa-a', 'wsa-b', 'wsa-c']:
    index.ensure_workspace(desktop)
    index.set_workspace_manifest(desktop, display_name=desktop, home_scenario='wsa-fixture')

delays = {}
async def ready(desktop):
    if desktop not in {'wsa-a','wsa-b','wsa-c'}: raise PermissionError('unknown_fixture_desktop')
    await asyncio.sleep(delays.get(desktop, 0))

gateway.ensure_webspace_ready = ready
from adaos.services.scenario import webspace_runtime
async def fixture_switch(desktop, scenario, **kwargs):
    await ready(desktop)
    if scenario not in {'wsa-fixture', 'wsa-second'}: raise ValueError('unknown_fixture_scenario')
    from adaos.services.yjs.doc import async_get_ydoc
    async with async_get_ydoc(desktop, prefer_live_room=True) as doc:
        application = json.loads(doc.get_map('ui').get('application').to_json())
        application['id'] = scenario
        with doc.begin_transaction() as txn:
            doc.get_map('ui').set(txn, 'current_scenario', scenario)
            doc.get_map('ui').set(txn, 'application', nested(application))
            doc.get_map('runtime').set(txn, 'materialization',
                {'scenario_id':scenario,'generation':'fixture-'+str(asyncio.get_running_loop().time()),'complete':True})
    return {'ok':True,'accepted':True,'scenario_id':scenario}
webspace_runtime.switch_webspace_scenario = fixture_switch
# Normal Core boot binds SDK subscriptions. The minimal stand binds the real
# administrative delete subscriber explicitly, without booting the compiler.
ctx.bus.subscribe('desktop.webspace.delete', webspace_runtime._on_webspace_delete)

def nested(value):
    if isinstance(value, dict): return YMap({key: nested(item) for key,item in value.items()})
    return value

async def get_room(desktop):
    if desktop in gateway.y_server.rooms: return gateway.y_server.rooms[desktop]
    await ready(desktop)
    store = get_ystore_for_webspace(desktop)
    room = gateway.DiagnosticYRoom(ready=False, ystore=store)
    room._webspace_id = desktop
    room._thread_id, room._loop = threading.get_ident(), asyncio.get_running_loop()
    try:
        await store.apply_updates(room.ydoc)
    except YDocNotFound:
        pass
    if room.ydoc.get_map('ui').get('current_scenario'):
        room.ready = True
        gateway.y_server.rooms[desktop]=room
        await gateway.y_server.start_room(room)
        return room
    with room.ydoc.begin_transaction() as txn:
        room.ydoc.get_map('ui').set(txn, 'current_scenario','wsa-fixture')
        room.ydoc.get_map('ui').set(txn,'application',nested({'id':'wsa-fixture','desktop':{
          'pageSchema':{'id':'wsa-fixture','widgets':[{'id':'fixture-status','type':'visual.metricTile','area':'main',
              'inputs':{'label':'Shared desktop fixture','value':'Ready'}}], 'initialState':{'activeTab':'home'},
              'navigationState':{'bindings':[{'stateKey':'activeTab','queryKey':'view','hydrate':True}]}}, 'topbar':[]},
          'modals':{'apps_catalog':{},'widgets_catalog':{}}}))
        data=room.ydoc.get_map('data')
        for key,value in {'desktop':{},'nodes':{},'installed':{'apps':[],'widgets':[]},'catalog':{'apps':[],'widgets':[]},
                          'fixture':{'desktop':desktop}}.items(): data.set(txn,key,nested(value))
        room.ydoc.get_map('runtime').set(txn,'materialization',{'scenario_id':'wsa-fixture','generation':'fixture-'+desktop,'complete':True})
    await store.write(Y.encode_state_as_update(room.ydoc))
    room.ready = True
    gateway.y_server.rooms[desktop]=room
    await gateway.y_server.start_room(room)
    return room

gateway.y_server.get_room = get_room
async def acquire_fixture(desktop, device, **kwargs):
    return await get_room(desktop)
gateway._acquire_yws_room = acquire_fixture

@asynccontextmanager
async def life(app):
    await gateway.start_y_server()
    gateway.y_server.get_room = get_room
    yield

app=FastAPI(lifespan=life)
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:8100','http://localhost:8100','http://127.0.0.1:8101','http://localhost:8101'],allow_methods=['*'],allow_headers=['*'])
@app.get('/api/node/status')
def fixture_status():
    return {'ok':True,'role':'hub','node_id':'wsa-node','subnet_id':'wsa-subnet',
        'environment':{'local_auth_required':True},'runtime':{'environment':{'local_auth_required':True}},
        'lifecycle':{'state':'ready','ready':True}}
@app.get('/api/node/summary')
def fixture_summary(): return fixture_status()
app.include_router(gateway.router)
app.include_router(node_api.router,prefix='/api/node')
app.include_router(personalization.router,prefix='/api')

@app.get('/api/ping')
def ping(): return {'ok':True}

@app.post('/fixture/visitor',dependencies=[Depends(require_desktop_caller)])
def visitor():
    policy=DesktopAccessService()
    invite=policy.invite(current_desktop_principal(),'wsa-b',max_sessions=10)
    return {'invite_id':invite['invite_id']}

@app.post('/fixture/evict/{desktop}',dependencies=[Depends(require_desktop_caller)])
async def evict(desktop: str):
    DesktopAccessService().require(current_desktop_principal(), desktop, administer=True)
    return await gateway.reset_live_webspace_room(desktop, prewarm_after_reset=False)

@app.get('/fixture/state',dependencies=[Depends(require_desktop_caller)])
async def fixture_state():
    DesktopAccessService().require(current_desktop_principal(), None, administer=True)
    return {'rooms': {key: {'ui':json.loads(room.ydoc.get_map('ui').to_json()),
        'durable_probe':room.ydoc.get_map('data').get('durable_probe'),
        'store':room.ystore.runtime_snapshot()} for key,room in gateway.y_server.rooms.items()},
        'sessions': gateway.active_browser_session_snapshot()}

@app.post('/fixture/delay/{desktop}',dependencies=[Depends(require_desktop_caller)])
async def delay(desktop: str, seconds: float = 0):
    DesktopAccessService().require(current_desktop_principal(), desktop, administer=True)
    delays[desktop] = max(0, min(seconds, 5))
    return {'ok': True}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
