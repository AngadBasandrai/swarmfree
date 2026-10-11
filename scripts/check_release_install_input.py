"""Verify the exact signed artifact before disposable Windows CI acceptance."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from verify_update_artifact import verify


def main():
    root = Path(__file__).resolve().parent.parent
    source = root / 'release-source'
    config = json.loads((source / 'src-tauri/tauri.conf.json').read_text())
    version = config['version']
    tag = os.environ['RELEASE_TAG']
    expected = os.environ['EXPECTED_SHA256'].lower()
    if tag != 'v' + version or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Tag/version or expected hash is invalid')
    artifact = root / 'build/acceptance' / f'Swarm-Workbench_{version}_x64-setup.exe'
    with artifact.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected:
        raise ValueError('Installer hash differs from the requested acceptance input')
    comment = verify(artifact, config['plugins']['updater']['pubkey'])
    if f'version:{version}' not in comment.split('\t'):
        raise ValueError('Authenticated installer version differs from the source tag')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    record = dict(tag=tag, version=version, source_commit=commit, sha256=digest, publisher_signature=True)
    (artifact.parent / 'input.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
