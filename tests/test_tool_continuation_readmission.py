import copy
import json
from types import SimpleNamespace
import time

import pytest

from adaos.services import tool_continuations as queue


@pytest.fixture
def recovery(tmp_path):
    source = tmp_path / 'src';source.mkdir()
    (source / 'handler.py').write_text('reviewed_revision = 2')
    manifest = tmp_path / 'resolved.json'
    manifest.write_text(json.dumps({'name':'sample','source':str(source),'tools':{'run':{'durable_replay':True}}}))
    status = {'resolved_manifest':str(manifest),'ready':True}
    manager = SimpleNamespace(dev_runtime_status=lambda _:status,runtime_status=lambda _:status)
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda:tmp_path/'state'))
    binding = {'pin':{'manifest':'old-slot','source':'old-src','digest':'a'*64},
        'body':{'tool':'sample:run','dev':True,'arguments':{'photo_id':'owned','mask':{'points':[1,2]},'paid_request_id':'unchanged'}},
        'actor':{'type':'user','id':'owner'},'scope':{'webspace_id':'desktop-dev'},
        'identity':{'request_id':'same','idempotency_key':'same'},'headers':{},'interval':3}
    with queue._connect(queue._database(ctx)) as db:
        db.execute('INSERT INTO continuations(id,owner,key,binding,status,due,expires,error) VALUES(?,?,?,?,?,?,?,?)',
            ('one','owner','photo-run',json.dumps(binding),'blocked',0,time.time()+3600,'PermissionError: continuation_runtime_changed'))
    kwargs = dict(expected_digest='a'*64,target_digest=queue._runtime_pin(manager,'sample',True)['digest'],
                  operator='transport:test-operator',approval_ref='reviewed-change:123',reason='Owner authorized recovery after reviewed update.')
    return SimpleNamespace(ctx=ctx,manager=manager,status=status,source=source,manifest=manifest,binding=binding,kwargs=kwargs)


def row(f):
    with queue._connect(queue._database(f.ctx)) as db:return dict(db.execute('SELECT * FROM continuations').fetchone())


def test_inspect_then_apply_changes_only_pin_and_keeps_deadline_identity_and_paid_receipts(recovery):
    f=recovery;before=row(f)
    plan=queue.operator_readmit_runtime(f.ctx,f.manager,'one',**f.kwargs)
    assert plan['applied'] is False and row(f)==before
    with queue._connect(queue._database(f.ctx)) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='continuation_readmissions'").fetchone()
    result=queue.operator_readmit_runtime(f.ctx,f.manager,'one',apply=True,**f.kwargs)
    after=row(f);binding=json.loads(after['binding'])
    assert after['status']=='waiting' and after['expires']==before['expires']
    assert after['owner']==before['owner'] and after['key']==before['key']
    old=copy.deepcopy(binding);old['pin']=f.binding['pin']
    assert old==f.binding
    assert binding['pin']==queue._runtime_pin(f.manager,'sample',True)
    assert result['receipt']['operator']=='transport:test-operator'
    with queue._connect(queue._database(f.ctx)) as db:
        assert db.execute('SELECT count(*) FROM continuation_readmissions').fetchone()[0]==1
    again=queue.operator_readmit_runtime(f.ctx,f.manager,'one',apply=True,**f.kwargs)
    assert again['idempotent'] and row(f)==after


@pytest.mark.parametrize('case',['old_digest','target_digest','source_changed','expired','running','completed','approval_block','deactivated','opt_out','authority'])
def test_readmission_requires_exact_review_and_cannot_wake_other_blocks(recovery,case):
    f=recovery;kwargs=dict(f.kwargs)
    if case=='old_digest':kwargs['expected_digest']='b'*64
    elif case=='target_digest':kwargs['target_digest']='b'*64
    elif case=='source_changed':(f.source/'handler.py').write_text('unreviewed = True')
    elif case=='authority':kwargs['approval_ref']=''
    elif case=='deactivated':f.status['deactivated']=True
    elif case=='opt_out':
        m=json.loads(f.manifest.read_text());m['tools']['run']['durable_replay']=False
        f.manifest.write_text(json.dumps(m));kwargs['target_digest']=queue._runtime_pin(f.manager,'sample',True)['digest']
    else:
        with queue._connect(queue._database(f.ctx)) as db:
            if case=='expired':db.execute('UPDATE continuations SET expires=0')
            elif case=='approval_block':db.execute("UPDATE continuations SET error='action_approval_required'")
            else:db.execute('UPDATE continuations SET status=?',(case,))
    before=row(f)
    with pytest.raises((ValueError,PermissionError)):
        queue.operator_readmit_runtime(f.ctx,f.manager,'one',apply=True,**kwargs)
    assert row(f)==before
