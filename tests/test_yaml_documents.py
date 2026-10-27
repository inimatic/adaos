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
