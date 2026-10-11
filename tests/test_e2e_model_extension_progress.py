import importlib.util
from pathlib import Path

from adaos.e2e.builder import _write_json


def outcome():
    path = Path(__file__).parents[1] / 'e2e/stand/report-prototype-model-extension-progress.py'
    spec = importlib.util.spec_from_file_location('model_extension_progress_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.candidate_outcome


def test_repair_outcome_does_not_repeat_the_failed_primary_verdict(tmp_path):
    _write_json(tmp_path / 'result.json', {'generation_ok': False, 'structural': {'ok': False}})
    _write_json(tmp_path / 'generation-result.json', {'ok': False})
    _write_json(tmp_path / 'planned-repair-result.json', {'ok': True})
    value = outcome()(tmp_path)
    assert value['first_compile_pass'] is False
    assert value['final_compile_pass'] is True
    assert value['final_candidate_source'] == 'planned-repair-result.json'


def test_a_failed_repair_is_not_counted_as_success(tmp_path):
    _write_json(tmp_path / 'result.json', {'generation_ok': False})
    _write_json(tmp_path / 'planned-repair-result.json', {'ok': False})
    assert outcome()(tmp_path)['final_compile_pass'] is False


def test_incomplete_candidate_verdicts_are_unknown(tmp_path):
    value = outcome()(tmp_path)
    assert value['first_compile_pass'] is None
    assert value['final_compile_pass'] is None
    assert value['final_candidate_source'] is None
