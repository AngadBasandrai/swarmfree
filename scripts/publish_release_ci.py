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
    global ROOT
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true',help='Verify and assemble assets without publishing or changing Git branches')
    parser.add_argument('--workspace');parser.add_argument('--verified-tag');parser.add_argument('--build-run');parser.add_argument('--acceptance-run');args=parser.parse_args()
    if args.workspace: ROOT=Path(args.workspace).resolve()
    version=json.loads((ROOT/'src-tauri/tauri.conf.json').read_text())['version'];tag='v'+version
    provenance=None
    if args.verified_tag:
        if args.verified_tag!=tag or not args.build_run or not args.acceptance_run: raise SystemExit('Existing-release evidence and matching tag are required')
        from check_existing_release import verify_existing
        provenance=verify_existing(ROOT,tag,args.build_run,args.acceptance_run)
    elif not args.prepare_only and os.environ.get('GITHUB_REF')!='refs/tags/'+tag: raise SystemExit('Release tag must match all package versions')
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
    series=version.rsplit('.',1)[0].replace('.','_')
    feature_doc='FEATURES_'+series+'.md'
    manifest=dict(version=version,notes='Swarm Workbench '+version+' — see '+feature_doc+' in the package',pub_date=datetime.now(timezone.utc).isoformat(),platforms=platforms)
    (destination/'stable.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if provenance: (destination/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    from verify_update_artifact import verify
    key=json.loads((ROOT/'src-tauri/tauri.conf.json').read_text())['plugins']['updater']['pubkey']
    for signature in destination.glob('*.sig'):
        comment=verify(Path(str(signature)[:-4]),key)
        if 'version:'+version not in comment.split('\t'): raise SystemExit('Signed artifact version differs from the release version')
    checksum=''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name+'\n' for f in sorted(destination.iterdir()) if f.is_file() and f.name not in {'SHA256SUMS.txt','release-notes.md'})
    (destination/'SHA256SUMS.txt').write_text(checksum)
    body=destination/'release-notes.md'
    notes=ROOT/'docs'/('RELEASE_'+series+'.md')
    body.write_text(notes.read_text(encoding='utf-8') if notes.is_file() else 'Downloadable Swarm Workbench '+version+'.\n\nInstallers carry updater signatures. Operating system signatures depend on separately provisioned certificate credentials. Model weights and optional runtimes are separate downloads.\n\nSee bundled docs/'+feature_doc+' and docs/BUILD_STATUS.md for supported scope and validation.\n',encoding='utf-8')
    if args.prepare_only:
        print('Verified release assets prepared locally: '+str(destination));return
    existing=subprocess.run(['gh','release','view',tag,'--repo','AngadBasandrai/swarmfree'],cwd=ROOT,capture_output=True)
    if existing.returncode: run('gh','release','create',tag,'--repo','AngadBasandrai/swarmfree','--verify-tag','--draft','--title','Swarm Workbench '+version+' alpha','--notes-file',str(body))
    remote=json.loads(subprocess.check_output(['gh','release','view',tag,'--repo','AngadBasandrai/swarmfree','--json','assets'],cwd=ROOT,text=True))
    known={p['name']:p.get('digest') for p in remote['assets']}
    uploads=[p for p in destination.iterdir() if p.name!='release-notes.md' and known.get(p.name)!='sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()]
    if uploads: run('gh','release','upload',tag,*[str(p) for p in uploads],'--repo','AngadBasandrai/swarmfree','--clobber')
    run('gh','release','edit',tag,'--repo','AngadBasandrai/swarmfree','--draft=false','--latest','--title','Swarm Workbench '+version+' alpha','--notes-file',str(body))
    saved=ROOT/'build/channel-stable.json';shutil.copy2(destination/'stable.json',saved)
    # Preserve the registry from the verified tag before switching to an older
    # channel branch. Its package bytes must match its signed index.
    registry=ROOT/'build/channel-registry'
    from swarm.registry import verify as verify_registry
    from swarm.registry_defaults import PUBLISHER_KEYS
    source=ROOT/'public/registry'
    index=verify_registry(json.loads((source/'index.json').read_text()),PUBLISHER_KEYS)
    for item in index['packages']:
        path=(source/item['path']).resolve()
        if not path.is_relative_to(source.resolve()) or not path.is_file():raise SystemExit('Registry package escapes the source directory or is missing')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise SystemExit('Registry package differs from the signed index')
        package=verify_registry(json.loads(path.read_text()),PUBLISHER_KEYS)
        if package['version']!=version or item['version']!=version:raise SystemExit('Registry package version differs from the verified release tag')
    shutil.copytree(source,registry,dirs_exist_ok=True)
    run('git','fetch','origin')
    exists=subprocess.run(['git','show-ref','--verify','--quiet','refs/remotes/origin/release-channel'],cwd=ROOT).returncode==0
    run('git','checkout','-B','release-channel','origin/release-channel' if exists else 'origin/main')
    target=ROOT/'public/updates';target.mkdir(parents=True,exist_ok=True);shutil.copy2(saved,target/'stable.json')
    shutil.copytree(registry,ROOT/'public/registry',dirs_exist_ok=True)
    run(sys.executable,str(ROOT/'scripts/sign_registry_ci.py'))
    run('git','config','user.name','github-actions[bot]');run('git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    run('git','add','public');run('git','commit','-m','Publish verified '+tag+' update channel');run('git','push','origin','HEAD:release-channel')
if __name__=='__main__':main()
