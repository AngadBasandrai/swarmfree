"""Verified inference packages. Downloads never execute their contents."""
import asyncio
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import platform
import re
import shutil
import tarfile
import tempfile
import time
import threading
import zipfile
import httpx
from .providers import RouteError
from .store import uid
from .onboarding import download_verified


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def unpack(path,folder,cancel=None):
    folder=Path(folder).resolve();total=0;files={}
    def write(name,size,stream,mode=0):
        nonlocal total
        if cancel and cancel.is_set():raise RouteError('Runtime installation cancelled')
        rel=PurePosixPath(name)
        if not name or '\\' in name or ':' in name or rel.is_absolute() or any(p in {'.','..'} for p in rel.parts):raise RouteError('Unsafe runtime archive path')
        target=(folder/str(rel)).resolve()
        if not target.is_relative_to(folder) or target==folder or target.exists():raise RouteError('Duplicate or escaping runtime archive member')
        total+=size
        if size<0 or total>4*1024**3 or len(files)>=2000:raise RouteError('Runtime archive exceeds extraction limits')
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as output:
            copied=0
            while chunk:=stream.read(min(1024*1024,size-copied+1)):
                if cancel and cancel.is_set():raise RouteError('Runtime installation cancelled')
                copied+=len(chunk)
                if copied>size:raise RouteError('Runtime member exceeds its declared size')
                output.write(chunk)
        if copied!=size:raise RouteError('Truncated runtime member')
        if mode&0o111 and os.name!='nt':target.chmod(0o755)
        files[rel.as_posix()]=sha(target)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist())>4000:raise RouteError('Runtime archive has too many entries')
            for item in archive.infolist():
                if (item.external_attr>>16)&0o170000==0o120000:raise RouteError('Runtime symlinks are not supported')
                if item.is_dir():continue
                with archive.open(item) as stream:write(item.filename,item.file_size,stream,item.external_attr>>16)
    else:
        with tarfile.open(path,'r:*') as archive:
            for count,item in enumerate(archive):
                if count>=4000:raise RouteError('Runtime archive has too many entries')
                if item.isdir():continue
                if not item.isfile():raise RouteError('Only regular files are accepted in runtime archives')
                with archive.extractfile(item) as stream:write(item.name,item.size,stream,item.mode)
    servers=[name for name in files if PurePosixPath(name).name in {'llama-server','llama-server.exe'}]
    if len(servers)!=1:raise RouteError('Package must contain exactly one llama-server executable')
    if os.name!='nt':(folder/servers[0]).chmod(0o755)
    return dict(files=files,server=servers[0])


class RuntimeInstaller:
    def __init__(self,store,root,runtimes):self.store,self.root,self.runtimes=store,Path(root),runtimes;self.lock=asyncio.Lock()

    async def catalog(self):
        async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False) as client:
            response=await client.get('https://api.github.com/repos/ggml-org/llama.cpp/releases',params={'per_page':10},headers={'Accept':'application/vnd.github+json'})
            if response.status_code!=200 or len(response.content)>2_000_000:raise RouteError('Official runtime catalog is unavailable')
            releases=response.json()
        if not isinstance(releases,list):raise RouteError('Unexpected upstream release catalog')
        system=platform.system().lower();arch=platform.machine().lower();assets=[]
        for release in releases:
            if release.get('draft') or release.get('prerelease'):continue
            for asset in release.get('assets',[]):
                name=asset['name'].lower();digest=asset.get('digest') or ''
                matches=((any(token in name for token in ('-win-','-windows-','_win_','_windows_'))) if system=='windows' else ('macos' in name or 'darwin' in name) if system=='darwin' else 'ubuntu' in name or 'linux' in name)
                matches=matches and (('arm64' in name or 'aarch64' in name) if arch in {'arm64','aarch64'} else ('x64' in name or 'amd64' in name or 'x86_64' in name))
                if matches and not name.startswith('cudart-') and re.fullmatch(r'sha256:[0-9a-f]{64}',digest) and name.endswith(('.zip','.tar.gz','.tgz')):
                    assets.append(dict(id=str(asset['id']),tag=release['tag_name'],name=asset['name'],url=asset['browser_download_url'],sha256=digest[7:],size=asset['size'],system=system,arch=arch))
        self.store.put('settings','runtime_catalog',dict(assets=assets,checked_at=time.time()))
        return dict(assets=assets,system=system,arch=arch,source='https://github.com/ggml-org/llama.cpp/releases')

    def verify(self,row):
        folder=Path(row['folder']).resolve()
        if not folder.is_relative_to((self.root/'managed-runtimes').resolve()):raise RouteError('Managed runtime directory is invalid')
        actual=[p for p in folder.rglob('*') if p.is_file() or p.is_symlink()]
        if any(p.is_symlink() for p in folder.rglob('*')) or {p.relative_to(folder).as_posix() for p in actual}!=set(row['files']):raise RouteError('Installed runtime contents differ from the verified package')
        for name,expected in row['files'].items():
            target=(folder/name).resolve()
            if not target.is_relative_to(folder) or target.is_symlink() or not target.is_file() or sha(target)!=expected:raise RouteError('Installed runtime changed; reinstall this pinned package')
        return str(folder/row['server'])

    async def install(self,id,asset_id):
        async with self.lock:
            cancelled=threading.Event();pending=None
            try:
                asset=next((a for a in self.store.get('settings','runtime_catalog',{}).get('assets',[]) if a['id']==asset_id),None)
                if not asset:raise RouteError('Refresh the verified runtime catalog and select an asset')
                self.store.patch('runtime_install',id,dict(status='downloading'))
                def progress(n):
                    if cancelled.is_set():raise RouteError('Runtime download cancelled')
                pending=asyncio.create_task(asyncio.to_thread(download_verified,self.root,asset['url'],asset['sha256'],asset['name'],min(asset['size']+1024,2*1024**3),progress))
                result=await asyncio.shield(pending)
                parent=self.root/'managed-runtimes';parent.mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=parent,prefix='staging-') as folder:
                    pending=asyncio.create_task(asyncio.to_thread(unpack,result['path'],folder,cancelled))
                    try:contents=await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        cancelled.set();await asyncio.gather(pending,return_exceptions=True);raise
                    final=parent/id
                    if final.exists():raise RouteError('Runtime package ID already installed')
                    Path(folder).replace(final)
                row=dict(id=id,status='installed',asset=asset,folder=str(final),**contents,installed_at=time.time())
                self.store.put('runtime_install',id,row)
            except asyncio.CancelledError:
                cancelled.set()
                if pending:await asyncio.gather(pending,return_exceptions=True)
                self.store.patch('runtime_install',id,dict(status='cancelled'));raise
            except Exception as error:self.store.patch('runtime_install',id,dict(status='failed',error=str(error) if isinstance(error,RouteError) else 'Runtime download or extraction failed'))

    async def activate(self,id):
        async with self.lock,self.runtimes.providers.local_lock:
            row=self.store.get('runtime_install',id)
            if not row or row.get('status')!='installed':raise RouteError('Select an installed runtime version')
            server=await asyncio.to_thread(self.verify,row)
            await self.runtimes.close()
            previous=self.store.get('settings','active_runtime',{}).get('id')
            for model in self.store.all('runtime'):
                if model.get('managed_install_id'):
                    stat=Path(server).stat()
                    self.store.patch('runtime',model['id'],dict(server_path=server,server_size=stat.st_size,server_modified_ns=stat.st_mtime_ns,managed_install_id=id))
            return self.store.put('settings','active_runtime',dict(id=id,previous_id=previous,server_path=server))
