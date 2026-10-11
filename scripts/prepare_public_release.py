"""Create publisher pins and signed examples. Keep private keys in the OS vault."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from swarm.privacy import canonical
from swarm.vault import Vault
from swarm.schema import Architecture
from swarm.templates import TEMPLATES

ROOT=Path(__file__).resolve().parent.parent
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--github-secrets',action='store_true',help='Provision signing secrets on the authorized swarmfree repository')
    parser.add_argument('--build',action='store_true',help='Build desktop packages with the protected updater key')
    parser.add_argument('--skip-engine',action='store_true')
    args=parser.parse_args();vault=Vault()
    if not vault.status()['persistent']: raise SystemExit('A persistent OS credential vault is required to preserve publisher identity')
    raw=vault.get('publisher_registry_private')
    if raw: key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    else:
        key=Ed25519PrivateKey.generate();vault.set('publisher_registry_private',base64.b64encode(key.private_bytes_raw()).decode())
    public=base64.b64encode(key.public_key().public_bytes_raw()).decode()
    (ROOT/'swarm/registry_defaults.py').write_text('PUBLISHER_KEYS = '+repr({'swarmfree':public})+'\n',encoding='utf-8')
    signing=vault.get('publisher_update_key');password=vault.get('publisher_update_password')
    if not signing:
        password=secrets.token_urlsafe(32)
        folder=ROOT/'build/publisher-private';folder.mkdir(parents=True,exist_ok=True);path=folder/'updater.key'
        result=subprocess.run([shutil.which('npx'),'tauri','signer','generate','--ci','-p',password,'-w',str(path)],cwd=ROOT,capture_output=True)
        if result.returncode: raise SystemExit('Updater key generation failed; secret-bearing output was suppressed')
        signing=path.read_text().strip();vault.set('publisher_update_key',signing);vault.set('publisher_update_password',password)
        vault.set('publisher_update_public',Path(str(path)+'.pub').read_text().strip())
    update_public=vault.get('publisher_update_public')
    if not update_public: raise SystemExit('Updater public key is missing; restore publisher credentials before continuing')
    config_path=ROOT/'src-tauri/tauri.conf.json';config=json.loads(config_path.read_text())
    old=config['plugins']['updater'].get('pubkey','')
    if old and old!=update_public: raise SystemExit('Refusing to replace an existing updater publisher identity')
    config['plugins']['updater'].update(pubkey=update_public,endpoints=['https://swarms.grafitelab.in/updates/stable.json','https://github.com/AngadBasandrai/swarmfree/releases/latest/download/stable.json'])
    config_path.write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
    (ROOT/'swarm/release_defaults.py').write_text('ENDPOINT = "https://swarms.grafitelab.in/updates/stable.json"\nPUBLIC_KEY = '+repr(update_public)+'\n',encoding='utf-8')
    registry=ROOT/'public/registry';(registry/'packages').mkdir(parents=True,exist_ok=True)
    def signed(payload): return dict(format='swarm-json-v1',key_id='swarmfree',payload=payload,signature=base64.b64encode(key.sign(canonical(payload).encode())).decode())
    packages=[]
    for template in TEMPLATES:
        payload=dict(id=template['id'],name=template['name'],version='0.5.0',kind='architecture',license='MIT',permissions=sorted({tool for node in template['nodes'] for tool in node.get('tools') or []}),architecture=template,
            examples=['Run with the offline demo before assigning live models'],evaluation=dict(status='deterministic demo validation',note='No live-provider quality ranking is claimed'))
        name=template['id']+'.json';raw=(json.dumps(signed(payload),indent=2)+'\n').encode();(registry/'packages'/name).write_bytes(raw)
        packages.append(dict(id=payload['id'],name=payload['name'],version=payload['version'],kind=payload['kind'],path='packages/'+name,sha256=hashlib.sha256(raw).hexdigest()))
    payload=dict(id='mcp-local-example',name='Local MCP adapter example',version='0.5.0',kind='plugin',license='MIT',permissions=['Run an owner-installed executable','Invoke explicitly granted MCP tools'],
        extension=dict(kind='mcp',transport='stdio',argv=['OWNER_INSTALLED_MCP_SERVER'],enabled=False),examples=['Replace argv with an installed server and review tools in Extensions'],evaluation=dict(status='declaration example; executable not bundled'))
    raw=(json.dumps(signed(payload),indent=2)+'\n').encode();(registry/'packages/mcp-local-example.json').write_bytes(raw)
    packages.append(dict(id=payload['id'],name=payload['name'],version='0.5.0',kind='plugin',path='packages/mcp-local-example.json',sha256=hashlib.sha256(raw).hexdigest()))
    now=int(time.time());index=dict(kind='index',serial=now,expires_at=now+365*86400,packages=packages)
    (registry/'index.json').write_text(json.dumps(signed(index),indent=2)+'\n',encoding='utf-8')
    (ROOT/'public/.nojekyll').touch();(ROOT/'public/CNAME').write_text('swarms.grafitelab.in\n')
    if args.github_secrets:
        for name,value in {'TAURI_SIGNING_PRIVATE_KEY':signing,'TAURI_SIGNING_PRIVATE_KEY_PASSWORD':password,'SWARM_REGISTRY_PRIVATE_KEY':base64.b64encode(key.private_bytes_raw()).decode()}.items():
            result=subprocess.run(['gh','secret','set',name,'--repo','AngadBasandrai/swarmfree'],input=value.encode(),capture_output=True)
            if result.returncode: raise SystemExit('GitHub rejected signing-secret provisioning; no secret output was displayed')
        print('Publisher secrets provisioned in the authorized GitHub repository')
    print('Public keys and signed example registry prepared; private keys remain protected')
    if args.build:
        env=dict(os.environ,TAURI_SIGNING_PRIVATE_KEY=signing,TAURI_SIGNING_PRIVATE_KEY_PASSWORD=password)
        cmd=[sys.executable,str(ROOT/'scripts/build_desktop.py')]
        if args.skip_engine: cmd.append('--skip-engine')
        subprocess.run(cmd,cwd=ROOT,env=env,check=True)
if __name__=='__main__':main()
