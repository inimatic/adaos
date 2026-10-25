from copy import deepcopy
from adaos.services.scenario.legacy_webui import upgrade_legacy_webui


def test_explicit_migration_preserves_widgets_actions_state_and_regions():
    original={"ui":{"application":{"desktop":{"pageSchema":{
        "id":"test", "initialState":{"selected":None},
        "layout":{"type":"split","areas":[{"id":"left","role":"sidebar","width":240},
            {"id":"main","role":"main"},{"id":"aux","role":"right"}],
            "interaction":{"detail":"inspector"},
            "variants":[{"id":"focus","when":"selected != null","type":"single",
                "areas":[{"id":"main","role":"main"}]}]},
        "widgets":[{"id":"one","type":"ui.table","area":"main",
            "actions":[{"on":"click","type":"callSkill","target":"owned.read"}]}]}}}}}
    before=deepcopy(original)
    upgraded,paths=upgrade_legacy_webui(original)
    page=upgraded['ui']['application']['desktop']['pageSchema']
    assert original==before
    assert page['widgets']==before['ui']['application']['desktop']['pageSchema']['widgets']
    assert page['initialState']=={'selected':None}
    assert page['layout']['pattern']=='workbench'
    assert [r['id'] for r in page['layout']['regions']]==['left','main','aux']
    assert page['layout']['regions'][0]['presentation']['compact']=='drawer'
    assert page['layout']['regions'][0]['size']=={'preferredPx':240}
    assert page['layout']['interaction']['detail']=='drawer'
    assert [r['id'] for r in page['layout']['variants'][0]['regions']]==['main']
    assert paths
    again,new_paths=upgrade_legacy_webui(upgraded)
    assert again==upgraded and new_paths==[]


def test_current_declarations_are_not_reformatted_or_weakened():
    current={'layout':{'version':2,'pattern':'document','regions':[{'id':'custom','role':'main'}]}}
    upgraded,paths=upgrade_legacy_webui(current)
    assert upgraded==current and paths==[]


def test_variants_keep_their_own_regions_and_do_not_inherit_base_pattern():
    document={'layout':{'type':'split','areas':[{'id':'base','role':'main'}],
        'variants':[{'id':'compact','regions':[{'id':'focus','role':'detail'}]}, {'id':'empty'}]}}
    upgraded,_=upgrade_legacy_webui(document)
    variants=upgraded['layout']['variants']
    assert len(variants)==1
    assert variants[0]['pattern']=='document'
    assert variants[0]['scroll']=='page'
    assert [r['id'] for r in variants[0]['regions']]==['focus']


def test_semantic_authoring_metadata_and_application_state_are_not_renderer_layouts():
    layout = {"type": "split", "areas": [{"id": "main", "role": "main"}]}
    document = {"pageSchema": {"layout": layout,
        "semantic": {"layout": layout, "interaction": {"detail": "inspector"}},
        "initialState": {"layout": layout}}}
    upgraded, paths = upgrade_legacy_webui(document)
    assert paths == ["/pageSchema/layout"]
    assert upgraded["pageSchema"]["semantic"] == document["pageSchema"]["semantic"]
    assert upgraded["pageSchema"]["initialState"] == document["pageSchema"]["initialState"]
