import copy
import pytest

from adaos.services.builder.reference_artifacts import BuilderReferenceArtifacts


def test_html_and_script_survive_exactly_as_inert_project_references(tmp_path):
    service=BuilderReferenceArtifacts(state_dir=tmp_path)
    text='<html><script>fetch("https://example.invalid/never")</script>Ретушь</html>'
    ref=service.add(project_ref='scenario:retouch',name='index.html',media_type='text/html',text=text)
    assert ref==service.add(project_ref='scenario:retouch',name='index.html',media_type='text/html',text=text)
    restored=BuilderReferenceArtifacts(state_dir=tmp_path).resolve(project_ref='scenario:retouch',receipts=[ref,ref])
    assert len(restored)==1 and restored[0]['text']==text
    assert restored[0]['execution']=='never' and restored[0]['authority']=='reference_only'
    with pytest.raises(ValueError,match='another project'):
        service.resolve(project_ref='scenario:other',receipts=[ref])
    drift=copy.deepcopy(ref);drift['content_sha256']='0'*64
    with pytest.raises(ValueError,match='identity mismatch'):
        service.resolve(project_ref='scenario:retouch',receipts=[drift])


@pytest.mark.parametrize('name', ['../index.html','D:\\private.html','https://example.com/index.html','.','a\n.html'])
def test_references_accept_content_not_paths_or_network_locations(tmp_path,name):
    with pytest.raises(ValueError,match='plain file name'):
        BuilderReferenceArtifacts(state_dir=tmp_path).add(project_ref='scenario:retouch',name=name,media_type='text/html',text='content')


def test_reference_bounds_and_corruption_fail_before_context_submission(tmp_path):
    from adaos.services.context_control import ContextConflict
    service=BuilderReferenceArtifacts(state_dir=tmp_path)
    with pytest.raises(ValueError,match='256 KiB'):
        service.add(project_ref='scenario:retouch',name='big.txt',media_type='text/plain',text='я'*140000)
    refs=[service.add(project_ref='scenario:retouch',name=f'{i}.txt',media_type='text/plain',text=str(i)*200000) for i in range(3)]
    with pytest.raises(ValueError,match='byte allowance'):
        service.resolve(project_ref='scenario:retouch',receipts=refs)
    path=service.store.artifact_root/(refs[0]['digest'].removeprefix('sha256:')+'.json')
    path.write_text('corrupt',encoding='utf-8')
    with pytest.raises(ContextConflict,match='digest mismatch'):
        service.resolve(project_ref='scenario:retouch',receipts=[refs[0]])
