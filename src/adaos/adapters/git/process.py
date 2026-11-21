"""Noninteractive, bounded Git children for Core-owned background operations."""
from __future__ import annotations

import os
import subprocess

import psutil


def _stop_owned_tree(process: subprocess.Popen) -> None:
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True)
    except psutil.Error:
        root, children = None, []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if root is not None:
        try:
            root.kill()
        except psutil.NoSuchProcess:
            pass
    else:
        process.kill()


def run_bounded_git(command, *, cwd=None, capture_output=True, text=True,
                    encoding='utf-8', errors='replace', timeout=90):
    environment = os.environ.copy()
    environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never', GCM_GUI_PROMPT='0')
    process = subprocess.Popen(command, cwd=cwd, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=text, encoding=encoding, errors=errors)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # subprocess.run kills only its immediate child. On Windows Git's
        # transport/credential grandchildren retain the pipes and can keep
        # communicate() waiting indefinitely after that nominal timeout.
        _stop_owned_tree(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
