"""Standalone boot-time mount helper for an operator-enrolled private volume.

Only stdlib is used so the reviewed helper can be installed with its private
systemd configuration without importing an older bootstrap Core checkout.
"""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess


def mount_enrolled_store(config_path: Path) -> dict:
    if os.name != 'posix':
        raise ValueError('Private native volume mounting requires POSIX')
    path = config_path.absolute()
    info = path.stat()
    if (path != path.resolve() or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
        raise ValueError('Private mount configuration is not owner-controlled')
    config = json.loads(path.read_text(encoding='utf-8'))
    if config.get('schema') != 'adaos.application.private_volume_mount.v1':
        raise ValueError('Unsupported private mount configuration')
    private = Path(config['private_root']).absolute()
    if private != private.resolve() or private == Path('/'):
        raise ValueError('Private mount owner root must not be linked or broad')
    if not path.is_relative_to(private / 'state'):
        raise ValueError('Private mount configuration must be retained by its runtime owner')
    for parent in path.parents:
        info = parent.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError('Private mount authority has a writable or foreign parent')
        if parent == private:
            break
    name = config['volume_name']
    if not name or not all(c.isalnum() or c in '-_' for c in name):
        raise ValueError('Invalid private volume name')
    target = private / 'volumes' / name
    if target != target.resolve():
        raise ValueError('Private mount target must not traverse a link')
    fd = os.open(config['source'], os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
    try:
        source = os.fstat(fd)
        identity = config['volume_identity']
        expected = (identity['inode'], identity['fsid'], os.geteuid(), identity['gid'], 0o700)
        observed = (source.st_ino, os.fstatvfs(fd).f_fsid, source.st_uid, source.st_gid, stat.S_IMODE(source.st_mode))
        if observed != expected or source.st_dev == private.stat().st_dev:
            raise ValueError('Enrolled store identity, ownership or private mode changed')
        parent = target.parent
        parent.mkdir(mode=0o700, exist_ok=True)
        if parent.stat().st_uid != os.geteuid() or stat.S_IMODE(parent.stat().st_mode) != 0o700:
            raise ValueError('Private volume parent must remain owner-only')
        if target.is_mount():
            mounted = target.stat()
            if (mounted.st_dev, mounted.st_ino) != (source.st_dev, source.st_ino):
                raise ValueError('A different volume occupies the private mount target')
            return {'status': 'already_mounted', 'volume_name': name}
        target.mkdir(mode=0o700, exist_ok=True)
        if target.stat().st_uid != os.geteuid() or stat.S_IMODE(target.stat().st_mode) != 0o700 or any(target.iterdir()):
            raise ValueError('Private mount target is not owned, private and empty')
        # The shared parent of source may be writable by other users. Mount the
        # verified open directory, not a pathname they could rename or swap.
        subprocess.run(['mount', '--no-canonicalize', '--bind', f'/proc/self/fd/{fd}', str(target)],
                       pass_fds=(fd,), capture_output=True, check=True)
        mounted = target.stat()
        if not target.is_mount() or (mounted.st_dev, mounted.st_ino) != (source.st_dev, source.st_ino):
            raise ValueError('Private bind mount identity was not preserved')
        return {'status': 'mounted', 'volume_name': name}
    finally:
        os.close(fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    print(json.dumps(mount_enrolled_store(parser.parse_args().config)))
