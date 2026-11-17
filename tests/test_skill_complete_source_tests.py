"""Whole-source qualification cannot omit unmarked tests beside suite folders."""
from pathlib import Path
import sys

from adaos.services.skill.tests_runner import run_tests


def test_complete_source_run_includes_root_and_grouped_tests_in_one_bounded_process(tmp_path):
    component = tmp_path / 'component'
    smoke = component / 'tests/smoke'
    smoke.mkdir(parents=True)
    (smoke / 'test_smoke.py').write_text('def test_smoke(): assert True\n', encoding='utf-8')
    (component / 'tests/test_regression.py').write_text('def test_regression(): assert False\n', encoding='utf-8')
    log = tmp_path / 'source-tests.log'
    result = run_tests(component, log_path=log, interpreter=Path(sys.executable),
                       dev_mode=True, complete_suite=True, timeout_seconds=30)
    assert result['pytest'].status == 'failed'
    text = log.read_text(encoding='utf-8')
    assert 'test_smoke.py' in text and 'test_regression.py' in text
    assert text.count(' -m pytest ') == 1
