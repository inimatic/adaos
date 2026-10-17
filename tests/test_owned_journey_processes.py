"""Public process ownership regression; never opens private consumer sources."""
import os
import subprocess
import sys

import psutil
import pytest

from adaos.services.builder.application_journeys import run_owned_journey


def test_journey_timeout_terminates_only_its_owned_process_family(tmp_path):
    marker = tmp_path / 'child-pid'
    program = ("import subprocess,sys,time;from pathlib import Path;"
               "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
               f"Path({str(marker)!r}).write_text(str(child.pid));time.sleep(60)")
    sibling = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_owned_journey([sys.executable, '-c', program], cwd=tmp_path, env=os.environ.copy(), timeout=5)
        assert marker.is_file(), 'Owned root must start its child before timeout'
        child_id = int(marker.read_text())
        try:
            # Exit/PID removal is asynchronous; disappearance between the
            # ownership lookup and status check is the expected success case.
            child = psutil.Process(child_id)
            psutil.wait_procs([child], timeout=3)
            assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE, 'Owned child survived timeout'
        except psutil.NoSuchProcess:
            pass
        assert sibling.poll() is None, 'Unrelated sibling must remain running'
    finally:
        sibling.terminate(); sibling.wait(timeout=5)
