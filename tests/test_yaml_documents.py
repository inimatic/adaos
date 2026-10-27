import pytest
import yaml

from adaos.services.yaml_documents import load_safe_yaml


def test_fast_safe_loader_keeps_alias_types_and_full_document_validation():
    document = 'version: "1.2.3"\nbase: &base {enabled: true, count: 2}\ncopy: *base\n'
    assert load_safe_yaml(document) == yaml.safe_load(document)
    with pytest.raises(yaml.YAMLError):
        load_safe_yaml('version: "1.2.3"\nbody: [broken\n')
    with pytest.raises(yaml.YAMLError):
        load_safe_yaml('value: !!python/object/apply:os.system ["must-not-run"]')


def test_safe_fallback_without_libyaml(monkeypatch):
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    assert load_safe_yaml('enabled: true\ncount: 2') == {"enabled": True, "count": 2}


@pytest.mark.parametrize('accelerated', [True, False])
def test_service_discovery_uses_the_safe_document_parser(tmp_path, monkeypatch, accelerated):
    from adaos.services.skill import service_supervisor

    if not accelerated:
        monkeypatch.delattr(yaml, 'CSafeLoader', raising=False)
    manifest = tmp_path/'skill.yaml'
    document = 'name: test\nversion: "1.2.3"\nruntime: {kind: service}\nservice: {port: 1234}\n'
    manifest.write_text(document, encoding='utf-8')
    assert service_supervisor._read_skill_manifest(tmp_path) == yaml.safe_load(document)
    manifest.write_text('runtime: {kind: service}\nlate: [broken', encoding='utf-8')
    assert service_supervisor._read_skill_manifest(tmp_path) == {}
    manifest.write_text('value: !!python/object/apply:os.system ["must-not-run"]', encoding='utf-8')
    assert service_supervisor._read_skill_manifest(tmp_path) == {}
