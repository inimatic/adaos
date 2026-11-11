#!/usr/bin/env python3
"""Qualify an exact Windows x64 kit in offline Windows Sandbox as a standard user.

Requires the Windows Sandbox optional feature, Node and the checkout's existing
backend Express/client esbuild dependencies. Only public fixtures and a dedicated
diagnostic directory are mapped. No checkout, owner state or credentials enter
the guest. Outputs must remain under the repository .tmp/ directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = r'''
import {createRequire} from 'node:module';
import path from 'node:path';
const [root,backend,output]=process.argv.slice(1);
const require=createRequire(path.join(root,'src/adaos/integrations/adaos-client/package.json'));
await require('esbuild').build({entryPoints:[path.join(backend,'backend/bootstrap-distribution-stand.mjs')],
  outfile:output,bundle:true,platform:'node',format:'esm',target:'node22',logLevel:'warning',
  banner:{js:'import {createRequire as qaCreateRequire} from "node:module"; const require=qaCreateRequire(import.meta.url);'}});
'''


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--backend-dir', type=Path, default=ROOT / 'src/adaos/integrations/adaos-backend')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows Sandbox requires a Windows host')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / '.tmp') or not 120 <= args.timeout <= 3600:
        parser.error('Use output under repository .tmp/ and timeout 120..3600 seconds')
    sandbox = Path(os.environ['WINDIR']) / 'System32/WindowsSandbox.exe'
    node = shutil.which('node')
    if not sandbox.is_file() or not node:
        parser.error('Windows Sandbox and Node must already be installed')
    running = subprocess.check_output(['tasklist', '/FI', 'IMAGENAME eq WindowsSandbox.exe', '/FO', 'CSV', '/NH'],
                                      text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if any(row and row[0].lower() == 'windowssandbox.exe' for row in csv.reader(running.splitlines())):
        parser.error('An existing Sandbox is running; preserve it and close it before this dedicated test')
    record = json.loads((args.release_dir / 'release.json').read_text(encoding='utf-8-sig'))
    if (record['os'], record['arch']) != ('windows', 'x86_64'):
        parser.error('This qualification accepts only Windows x64 kits')
    filename = record['filename']
    if Path(filename).name != filename or not filename.endswith('.tar.gz'):
        parser.error('Invalid bundle filename')
    source_bundle = args.release_dir / filename
    if source_bundle.is_symlink() or source_bundle.stat().st_size != record['bytes'] or digest(source_bundle) != record['sha256']:
        parser.error('Bundle integrity mismatch')
    output.mkdir(parents=True, exist_ok=False)
    inputs, diagnostics = output / 'input', output / 'guest'
    (inputs / 'release').mkdir(parents=True); diagnostics.mkdir()
    for name in ('release.json', filename, 'bootstrap-distribution.ps1'):
        source = args.release_dir / name
        if source.is_symlink():
            parser.error('Release fixtures must not be symbolic links')
        shutil.copy2(source, inputs / 'release' / name)
    for name in ('prepare.ps1', 'consumer.ps1', 'check_runtime.py'):
        shutil.copy2(ROOT / 'tools/windows_sandbox' / name, inputs / name)
    shutil.copy2(node, inputs / 'node.exe')
    subprocess.run([node, '--input-type=module', '-e', BUNDLE, str(ROOT), str(args.backend_dir.resolve()),
                    str(inputs / 'receiver.mjs')], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    manifest = {'schema': 'adaos.bootstrap.sandbox-fixtures.v1', 'bundle_sha256': record['sha256'],
                'source': record['source'], 'networking': 'Disable',
                'node_version': subprocess.check_output([node, '--version'], text=True,
                                                        creationflags=subprocess.CREATE_NO_WINDOW).strip(),
                'files': {str(path.relative_to(inputs)).replace('\\', '/'): digest(path)
                          for path in inputs.rglob('*') if path.is_file()}}
    (output / 'fixtures.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    config = ET.Element('Configuration')
    for key in ('VGpu', 'Networking', 'AudioInput', 'VideoInput', 'ClipboardRedirection', 'PrinterRedirection'):
        ET.SubElement(config, key).text = 'Disable'
    ET.SubElement(config, 'MemoryInMB').text = '4096'
    mappings = ET.SubElement(config, 'MappedFolders')
    for source, target, readonly in ((inputs, r'C:\AdaosInput', True), (diagnostics, r'C:\AdaosOutput', False)):
        mapping = ET.SubElement(mappings, 'MappedFolder')
        ET.SubElement(mapping, 'HostFolder').text = str(source)
        ET.SubElement(mapping, 'SandboxFolder').text = target
        ET.SubElement(mapping, 'ReadOnly').text = str(readonly).lower()
    command = ET.SubElement(ET.SubElement(config, 'LogonCommand'), 'Command')
    command.text = r'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\AdaosInput\prepare.ps1'
    config_path = output / 'qualification.wsb'
    ET.ElementTree(config).write(config_path, encoding='utf-8', xml_declaration=True)
    startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = 0
    with (output / 'launcher.log').open('wb') as log:
        process = subprocess.Popen([str(sandbox), str(config_path)], startupinfo=startup,
                                   creationflags=subprocess.CREATE_NO_WINDOW, stdout=log, stderr=subprocess.STDOUT)
        print(json.dumps({'started': True, 'sandbox_pid': process.pid, 'networking': 'Disable', 'bundle_sha256': record['sha256']}), flush=True)
        deadline, previous = time.monotonic() + args.timeout, None
        try:
            while not (diagnostics / 'result.json').is_file():
                if process.poll() is not None:
                    raise RuntimeError('Sandbox exited before exporting a result; inspect launcher/guest logs')
                if time.monotonic() > deadline:
                    raise TimeoutError('Sandbox qualification timed out; available diagnostics preserved')
                try:
                    progress = json.loads((diagnostics / 'progress.json').read_text(encoding='utf-8-sig'))
                    current = (progress['scenario'], progress['stage'])
                    if current != previous:
                        print(json.dumps({'progress': progress}), flush=True); previous = current
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(1)
            result = json.loads((diagnostics / 'result.json').read_text(encoding='utf-8-sig'))
            print(json.dumps(result), flush=True)
            if not result['ok']:
                raise RuntimeError('Sandbox qualification failed; inspect preserved guest diagnostics')
            assert result['bundle_sha256'] == record['sha256']
            assert result['source_revision'] == record['source']['revision']
        finally:
            if process.poll() is None:
                # Only this dedicated process tree is retired, after the guest's
                # atomic result marker confirms diagnostic export has finished.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                process.wait(timeout=30)


if __name__ == '__main__':
    main()
