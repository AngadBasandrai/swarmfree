"""Clean-runner artifact checks and a per-platform signed updater manifest."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parent.parent
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);args=parser.parse_args()
    release=ROOT/'releases';version=json.loads((ROOT/'src-tauri/tauri.conf.json').read_text())['version']
    suffix='-setup.exe' if args.target.startswith('windows') else '.app.tar.gz' if args.target.startswith('darwin') else '.AppImage'
    artifacts=[p for p in release.iterdir() if version in p.name and p.name.endswith(suffix)]
    if len(artifacts)!=1: raise SystemExit('Expected exactly one updater artifact for '+args.target)
    binary=ROOT/'src-tauri/target/release/swarm-engine.exe' if args.target.startswith('windows') else next((ROOT/'src-tauri/binaries').glob('swarm-engine-*'))
    subprocess.run([sys.executable,str(ROOT/'scripts/check_desktop_engine.py'),str(binary)],check=True)
    if Path(str(artifacts[0])+'.sig').exists():
        from verify_update_artifact import verify
        config=json.loads((ROOT/'src-tauri/tauri.conf.json').read_text())
        verify(artifacts[0],config['plugins']['updater']['pubkey'])
        subprocess.run([sys.executable,str(ROOT/'scripts/make_update_manifest.py'),'--base-url','https://github.com/AngadBasandrai/swarmfree/releases/download/v'+version,'--target',args.target,'--asset-name',artifacts[0].name.replace(' ', '-'),'--output',str(release/(args.target+'.json')),str(artifacts[0])],check=True)
    elif __import__('os').environ.get('GITHUB_REF','').startswith('refs/tags/'):
        raise SystemExit('Tagged publication requires a signed updater artifact')
    print('Frozen engine and platform packaging checks passed')
if __name__=='__main__':main()
