"""Publish only a complete, signature-bearing platform set and advance the channel."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote, urlparse
ROOT=Path(__file__).resolve().parent.parent
def run(*args,**kwargs): return subprocess.run(args,cwd=ROOT,check=True,**kwargs)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true',help='Verify and assemble assets without publishing or changing Git branches');args=parser.parse_args()
    version=json.loads((ROOT/'src-tauri/tauri.conf.json').read_text())['version'];tag='v'+version
    if not args.prepare_only and os.environ.get('GITHUB_REF')!='refs/tags/'+tag: raise SystemExit('Release tag must match all package versions')
    manifests=list((ROOT/'build/collected').glob('*/*.json'));platforms={}
    for path in manifests:
        value=json.loads(path.read_text())
        if value.get('version')==version: platforms.update(value.get('platforms',{}))
    if set(platforms)!={'windows-x86_64','darwin-aarch64','darwin-x86_64','linux-x86_64'}: raise SystemExit('A required signed platform manifest is missing')
    destination=ROOT/'build/publish';destination.mkdir(parents=True,exist_ok=True)
    for file in (ROOT/'build/collected').glob('*/*'):
        if file.is_file() and file.suffix not in {'.json','.txt'}:
            target=destination/file.name.replace(' ', '-')
            if target.exists() and target.read_bytes()!=file.read_bytes(): raise SystemExit('Release asset collision: '+file.name)
            shutil.copy2(file,target)
    for platform in platforms.values():
        name=Path(unquote(urlparse(platform['url']).path)).name.replace(' ', '-')
        artifact=destination/name
        if not artifact.is_file() or not Path(str(artifact)+'.sig').is_file(): raise SystemExit('Updater asset or adjacent signature is missing: '+name)
        if Path(str(artifact)+'.sig').read_text().strip()!=platform['signature']: raise SystemExit('Manifest signature differs from its artifact signature')
        platform['url']='https://github.com/AngadBasandrai/swarmfree/releases/download/'+tag+'/'+quote(name)
    from datetime import datetime,timezone
    manifest=dict(version=version,notes='Swarm Workbench '+version+' — see FEATURES_0_5.md in the package',pub_date=datetime.now(timezone.utc).isoformat(),platforms=platforms)
    (destination/'stable.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from verify_update_artifact import verify
    key=json.loads((ROOT/'src-tauri/tauri.conf.json').read_text())['plugins']['updater']['pubkey']
    for signature in destination.glob('*.sig'):
        comment=verify(Path(str(signature)[:-4]),key)
        if 'version:'+version not in comment.split('\t'): raise SystemExit('Signed artifact version differs from the release version')
    checksum=''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name+'\n' for f in sorted(destination.iterdir()) if f.is_file() and f.name not in {'SHA256SUMS.txt','release-notes.md'})
    (destination/'SHA256SUMS.txt').write_text(checksum)
    body=destination/'release-notes.md';body.write_text('Downloadable Swarm Workbench '+version+'. Includes guided setup, reusable sub-swarms, bounded channels, team roles, data destination reviews, response reuse, failure simulations, A2A text tasks, experimental Exo placement and signed community packages.\n\nInstallers carry updater signatures. Operating system signatures depend on separately provisioned certificate credentials. Model weights and optional runtimes are separate downloads.\n\nSee bundled docs/FEATURES_0_5.md and docs/BUILD_STATUS.md for supported scope and validation.\n')
    if args.prepare_only:
        print('Verified release assets prepared locally: '+str(destination));return
    existing=subprocess.run(['gh','release','view',tag,'--repo','AngadBasandrai/swarmfree'],cwd=ROOT,capture_output=True)
    if existing.returncode: run('gh','release','create',tag,'--repo','AngadBasandrai/swarmfree','--verify-tag','--title','Swarm Workbench '+version,'--notes-file',str(body))
    run('gh','release','upload',tag,*[str(p) for p in destination.iterdir() if p.name!='release-notes.md'],'--repo','AngadBasandrai/swarmfree','--clobber')
    saved=ROOT/'build/channel-stable.json';shutil.copy2(destination/'stable.json',saved)
    run('git','fetch','origin')
    exists=subprocess.run(['git','show-ref','--verify','--quiet','refs/remotes/origin/release-channel'],cwd=ROOT).returncode==0
    run('git','checkout','-B','release-channel','origin/release-channel' if exists else 'origin/main')
    target=ROOT/'public/updates';target.mkdir(parents=True,exist_ok=True);shutil.copy2(saved,target/'stable.json')
    run(sys.executable,str(ROOT/'scripts/sign_registry_ci.py'))
    run('git','config','user.name','github-actions[bot]');run('git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    run('git','add','public');run('git','commit','-m','Publish verified '+tag+' update channel');run('git','push','origin','HEAD:release-channel')
if __name__=='__main__':main()
