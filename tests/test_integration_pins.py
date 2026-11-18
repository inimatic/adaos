from pathlib import Path
import subprocess
import sys


TOOL = Path(__file__).resolve().parents[1] / 'tools/update_integration_pins.py'


def test_pins_follow_the_staged_release_and_ignore_an_unpublished_working_checkout(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args])
    git('init', '-q')
    relative = 'src/adaos/integrations/adaos-client'
    metadata = tmp_path / (relative + '.sha')
    metadata.parent.mkdir(parents=True)
    metadata.write_text('a' * 40 + '\n', encoding='ascii')
    git('add', relative + '.sha')
    git('update-index', '--add', '--cacheinfo', '160000,' + 'b' * 40 + ',' + relative)
    checkout = tmp_path / relative
    checkout.mkdir()
    (checkout / 'unpublished-work').write_text('c' * 40)
    before = subprocess.run([sys.executable, str(TOOL), '--repo-root', str(tmp_path), '--check'], capture_output=True)
    assert before.returncode == 1
    assert metadata.read_text().strip() == 'a' * 40
    subprocess.run([sys.executable, str(TOOL), '--repo-root', str(tmp_path)], check=True, capture_output=True)
    assert metadata.read_text().strip() == 'b' * 40
    subprocess.run([sys.executable, str(TOOL), '--repo-root', str(tmp_path), '--check'], check=True)
