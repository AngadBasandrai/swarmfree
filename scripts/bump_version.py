"""Keep application/package versions together without changing dependency versions."""
import argparse
import json
from pathlib import Path
import re

def main():
    parser=argparse.ArgumentParser();parser.add_argument('version');args=parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.]+)?',args.version):parser.error('Use a semantic version')
    root=Path(__file__).resolve().parents[1]
    for name in ('package.json','src-tauri/tauri.conf.json'):
        p=root/name;row=json.loads(p.read_text());row['version']=args.version;p.write_text(json.dumps(row,indent=2)+'\n')
    p=root/'package-lock.json';row=json.loads(p.read_text());row['version']=args.version;row['packages']['']['version']=args.version;p.write_text(json.dumps(row,indent=2)+'\n')
    for name in ('pyproject.toml','src-tauri/Cargo.toml'):
        p=root/name;text=p.read_text();p.write_text(re.sub(r'^version = "[^"]+"','version = "'+args.version+'"',text,count=1,flags=re.M))
    p=root/'swarm/app.py';p.write_text(re.sub(r'version="[0-9]+\.[0-9]+\.[0-9]+"','version="'+args.version+'"',p.read_text()))
    p=root/'scripts/check_desktop_engine.py';p.write_text(re.sub(r'\["version"\] == "[0-9]+\.[0-9]+\.[0-9]+"','["version"] == "'+args.version+'"',p.read_text()))
    p=root/'src-tauri/Cargo.lock';text=p.read_text();text=re.sub(r'(name = "swarm-workbench"\nversion = ")[^"]+',r'\g<1>'+args.version,text);p.write_text(text)
    print('Application version:',args.version)

if __name__=='__main__':main()
