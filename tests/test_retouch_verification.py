import copy
import hashlib
import io
import json
import sqlite3

from PIL import Image
import pytest

from adaos.e2e.retouch_verification import verify_downloads


def fixture(tmp_path, *, extra_runs=0, protected_pixel=False):
    owned=tmp_path/'owned'; output=tmp_path/'output'
    (owned/'data').mkdir(parents=True); output.mkdir()
    base=Image.new('RGB',(320,240),(100,120,140)); changed=base.copy()
    changed.putpixel((50,50) if protected_pixel else (100,100),(101,121,141))
    def encoded(image,fmt):
        stream=io.BytesIO(); image.save(stream,format=fmt); return stream.getvalue()
    original=encoded(base,'PNG'); sha=hashlib.sha256(original).hexdigest()
    snapshot={'photo_sha256':sha,'mask':{'permission_mode':'rectangle','context_border_px':24,
        'crop':{'x':0,'y':0,'width':320,'height':240},
        'strokes':[{'kind':'protect','points':[{'x':50,'y':50}],'radius':8}]}}
    report={'downloads':[],'revision_probe':{'original_run_id':'run.original'}}
    exports=[]; download_rows=[]
    for fmt,suffix in [('PNG','png'),('JPEG','jpg')]:
        raw=encoded(changed,fmt); name='selected.'+suffix; (output/name).write_bytes(raw)
        digest=hashlib.sha256(raw).hexdigest()
        exports.append({'format':fmt,'sha256':digest})
        report['downloads'].append({'filename':name,'digest':'sha256:'+digest})
        download_rows.append({'filename':name,'run_id':'run.original','format':fmt})
    overlay=Image.new('RGBA',base.size,(0,0,0,128)); overlay.putpixel((50,50) if protected_pixel else (100,100),(0,0,0,0))
    overlay.save(output/'difference-map.png')
    db=sqlite3.connect(owned/'data/retouch.sqlite3')
    db.execute('CREATE TABLE records(kind TEXT,id TEXT,body TEXT)')
    db.execute('CREATE TABLE assets(digest TEXT,base BLOB)')
    db.execute('INSERT INTO assets VALUES(?,?)',(sha,original))
    run={'status':'completed','snapshot':snapshot,'exports':exports}
    db.execute('INSERT INTO records VALUES(?,?,?)',('runs','run.original',json.dumps(run)))
    for index in range(extra_runs):
        other=copy.deepcopy(run); other['snapshot']['mask']['context_border_px']=25
        db.execute('INSERT INTO records VALUES(?,?,?)',('runs','run.other'+str(index),json.dumps(other)))
    for index,row in enumerate(download_rows):
        db.execute('INSERT INTO records VALUES(?,?,?)',('downloads',str(index),json.dumps(row)))
    db.commit();db.close()
    return owned,output,report


@pytest.mark.parametrize('extra_runs',[0,2])
def test_exact_download_ownership_survives_multiple_completed_runs_with_identical_pixels(tmp_path,extra_runs):
    owned,output,report=fixture(tmp_path,extra_runs=extra_runs)
    result=verify_downloads(owned_root=owned,output=output,report=report)
    assert result['passed'] and result['selected_run_id']=='run.original'
    assert result['completed_run_count']==extra_runs+1
    assert result['protected_pixels_equal'] and result['difference_overlay_exact']


@pytest.mark.parametrize('fault',['foreign_owner','mixed_owners','duplicate_filename','history_mismatch',
    'path_escape','file_corrupt','report_digest','overlay','protected_pixel'])
def test_independent_checker_rejects_targeted_mutations(tmp_path,fault):
    owned,output,report=fixture(tmp_path,extra_runs=2,protected_pixel=fault=='protected_pixel')
    with sqlite3.connect(owned/'data/retouch.sqlite3') as db:
        if fault in {'foreign_owner','mixed_owners'}:
            row=json.loads(db.execute("SELECT body FROM records WHERE kind='downloads' AND id='0'").fetchone()[0])
            row['run_id']='run.missing' if fault=='foreign_owner' else 'run.other0'
            db.execute("UPDATE records SET body=? WHERE kind='downloads' AND id='0'",(json.dumps(row),))
        if fault=='duplicate_filename':
            row=db.execute("SELECT body FROM records WHERE kind='downloads' LIMIT 1").fetchone()[0]
            db.execute("INSERT INTO records VALUES('downloads','duplicate',?)",(row,))
    if fault=='history_mismatch':report['revision_probe']['original_run_id']='run.other0'
    if fault=='path_escape':report['downloads'][0]['filename']='../selected.png'
    if fault=='file_corrupt':(output/'selected.png').write_bytes(b'not an image')
    if fault=='report_digest':report['downloads'][0]['digest']='sha256:'+'0'*64
    if fault=='overlay':Image.new('RGBA',(320,240),(0,0,0,128)).save(output/'difference-map.png')
    with pytest.raises((ValueError,OSError)):
        verify_downloads(owned_root=owned,output=output,report=report)
