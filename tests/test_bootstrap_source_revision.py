import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('revision,suffix', [
    ('a'*40, 'a'*40),
    ('main','refs/heads/main'),
    ('ops/root-de-20261022','refs/heads/ops/root-de-20261022'),
])
def test_linux_archive_installer_admits_immutable_sha_without_calling_it_a_branch(revision,suffix):
    source=(Path(__file__).resolve().parents[1]/'tools/init/linux/init.sh').read_text()
    function=re.search(r'^source_archive_url\(\) \{.*?^\}',source,re.M|re.S).group(0)
    bash='C:/Program Files/Git/bin/bash.exe' if os.name=='nt' else shutil.which('bash')
    assert bash
    result=subprocess.run([bash,'-c',function+'\nREPO_OWNER=inimatic\nREPO_NAME=adaos\nsource_archive_url "$1"','test',revision],capture_output=True,text=True,check=True)
    assert result.stdout=='https://codeload.github.com/inimatic/adaos/tar.gz/'+suffix
    assert 'url="$(source_archive_url "$REV")"' in source
