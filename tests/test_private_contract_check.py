"""Private source and failure diagnostics must never enter public CI receipts."""
import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/run_private_contract_check.py'
spec = importlib.util.spec_from_file_location('private_contract_check', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_public_receipt_drops_all_private_metadata(tmp_path):
    source = tmp_path / 'private.xml'
    destination = tmp_path / 'public.xml'
    source.write_text('''<testsuites><testsuite name="PRIVATE" hostname="PRIVATE"><properties>
        <property name="secret" value="PRIVATE"/></properties><testcase name="PRIVATE" classname="PRIVATE" file="PRIVATE" time="PRIVATE">
        <failure message="PRIVATE" type="PRIVATE">PRIVATE source code</failure><system-out>PRIVATE</system-out>
        <system-err>PRIVATE</system-err></testcase><testcase name="PRIVATE"><skipped message="PRIVATE"/></testcase>
        </testsuite></testsuites>''', encoding='utf-8')
    assert module.public_junit(source, destination) == {'tests': 2, 'failures': 1, 'errors': 0, 'skipped': 1}
    content = destination.read_text(encoding='utf-8')
    assert 'PRIVATE' not in content
    assert ET.fromstring(content).find('.//failure') is not None


def test_real_command_failure_is_preserved_without_console_output():
    result = subprocess.run([sys.executable, str(SCRIPT), '--label', 'privacy-regression', '--',
                             sys.executable, '-c', 'print("PRIVATE SOURCE"); raise SystemExit(23)'],
                            capture_output=True, text=True)
    assert result.returncode == 23
    assert 'PRIVATE SOURCE' not in result.stdout + result.stderr
    assert '"exit_code": 23' in result.stdout
