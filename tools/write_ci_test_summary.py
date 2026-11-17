"""Retain bounded, source-bound diagnostic receipts independently of artifact quota."""
from __future__ import annotations
import base64
import hashlib
import io
import os
from pathlib import Path
import zipfile


def main() -> None:
    paths = sorted(Path("reports").glob("*.xml"))
    browser = Path("e2e/artifacts/channel-reliability-ci")
    if browser.is_dir():
        paths += sorted(browser.glob("*.json"))
    included = []
    excluded = []
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in paths:
            if path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
                bundle.writestr(path.as_posix(), path.read_bytes())
                included.append(path.as_posix())
            else:
                excluded.append(path.as_posix())
    raw = output.getvalue()
    heading = (f"\n### Test evidence ({os.environ.get('GITHUB_JOB', 'local')})\n\n"
               f"Source `{os.environ.get('GITHUB_SHA', 'unknown')}`, run "
               f"`{os.environ.get('GITHUB_RUN_ID', 'local')}.{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}`.\n\n")
    if len(raw) <= 600 * 1024:
        heading += (f"ZIP SHA256 `{hashlib.sha256(raw).hexdigest()}`, {len(included)} report files. "
                    "The base64 ZIP below preserves these receipts even when the optional Actions archive is unavailable.\n\n"
                    "<details><summary>Source-bound report bundle (base64 ZIP)</summary>\n\n```text\n"
                    + base64.b64encode(raw).decode('ascii') + "\n```\n</details>\n")
    else:
        heading += "Report bundle exceeds summary capacity; full diagnostics remain in the job log and optional archive.\n"
    if excluded:
        heading += "\nFiles above the per-file evidence limit: " + ", ".join(excluded) + "\n"
    destination = os.environ.get('GITHUB_STEP_SUMMARY')
    if destination:
        with open(destination, 'a', encoding='utf-8') as stream:
            stream.write(heading)
    else:
        print(f"Prepared {len(included)} report files ({len(raw)} compressed bytes)")


if __name__ == '__main__':
    main()
