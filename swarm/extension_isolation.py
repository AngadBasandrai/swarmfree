"""Docker stdio adapter with a filtered read-only workspace and no network."""
import json
from pathlib import Path
import re
import shutil
import tempfile
from .providers import RouteError
from .full_backup import eligible_files
from .isolation import command
from .store import uid


def policy(value):
    image=value.get('image','')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}',image):raise RouteError('Pin the installed sandbox image by SHA-256 digest')
    files=value.get('files',[]);secrets=value.get('secrets',{})
    if not isinstance(files,list) or len(files)>100 or any(not isinstance(p,str) or len(p)>1000 for p in files):raise RouteError('Select at most 100 sandbox files')
    if not isinstance(secrets,dict) or len(secrets)>8 or any(not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',name) or not isinstance(ref,str) or not re.fullmatch(r'extension_[A-Za-z0-9_-]{1,100}',ref) for name,ref in secrets.items()):raise RouteError('Sandbox secrets reference explicit extension vault entries')
    memory=value.get('memory_mb',512);cpu=value.get('cpus',1)
    if isinstance(memory,bool) or not isinstance(memory,int) or not 64<=memory<=8192 or isinstance(cpu,bool) or not isinstance(cpu,(int,float)) or not .1<=cpu<=8:raise RouteError('Invalid sandbox CPU or memory allocation')
    return dict(enabled=True,image=image,files=files,secrets=secrets,memory_mb=memory,cpus=cpu,network='none')


class IsolatedRPC:
    def __init__(self,config,project,vault):self.config,self.project,self.vault=config,project,vault;self.rpc=None;self.redactions=[]

    async def __aenter__(self):
        from .extensions import StdioRPC
        p=policy(self.config['isolation']);self.name='swarm-extension-'+uid();self.folder=tempfile.TemporaryDirectory(prefix='swarm-extension-')
        try:
            inspected=json.loads(await command(['docker','image','inspect',p['image']],timeout=15))
            if not inspected or inspected[0].get('Os')!='linux':raise RouteError('Extension isolation needs an installed Linux container image')
            root=Path(self.folder.name);workspace=root/'workspace';workspace.mkdir();secret_dir=root/'secrets';secret_dir.mkdir()
            selected=set(p['files']);copied=[];total=0
            for relative,path in eligible_files(self.project['root']):
                if relative not in selected:continue
                raw=path.read_bytes();total+=len(raw)
                if total>2_000_000:raise RouteError('Sandbox file selection exceeds 2 MB')
                target=workspace/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);copied.append(relative)
            if set(copied)!=selected:raise RouteError('Some selected sandbox files are missing, private or ineligible')
            self.source_paths=copied
            args=['docker','run','--pull','never','--rm','-i','--name',self.name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64','--memory',str(p['memory_mb'])+'m','--cpus',str(p['cpus']),'--user','65534:65534','--tmpfs','/tmp:rw,noexec,nosuid,size=64m','--mount',f'type=bind,src={workspace},dst=/workspace,readonly','--workdir','/workspace']
            for name,ref in p['secrets'].items():
                value=self.vault.get(ref)
                if not value:raise RouteError('A selected sandbox secret is unavailable')
                self.redactions.append(value);path=secret_dir/name;path.write_text(value,encoding='utf-8')
                args+=['--env',name+'_FILE=/run/secrets/'+name]
            if p['secrets']:args+=['--mount',f'type=bind,src={secret_dir},dst=/run/secrets,readonly']
            argv=self.config['argv']
            if not argv or any(not isinstance(a,str) or not a or len(a)>2000 or '\x00' in a for a in argv):raise RouteError('Configure executable arguments inside the sandbox image')
            args += ['--entrypoint',argv[0],p['image'],*argv[1:]]
            self.rpc=StdioRPC(args,None);await self.rpc.__aenter__();return self
        except BaseException:
            await self.__aexit__(None,None,None);raise

    async def send(self,*args,**kwargs):
        if kwargs.get('on_notification'):
            callback=kwargs['on_notification']
            kwargs['on_notification']=lambda value:callback(self.redact(value))
        value=await self.rpc.send(*args,**kwargs)
        return self.redact(value)

    def redact(self,value):
        if value is None:return value
        raw=json.dumps(value)
        for secret in self.redactions:raw=raw.replace(json.dumps(secret)[1:-1],'[sandbox secret redacted]')
        return json.loads(raw)

    async def __aexit__(self,*args):
        try:
            if self.rpc and hasattr(self.rpc,'process'):await self.rpc.__aexit__(*args)
        finally:
            try:await command(['docker','rm','-f',self.name],timeout=10)
            except (RouteError,OSError):pass
            self.folder.cleanup()


def rpc_for(config,project,vault):
    from .extensions import StdioRPC
    return IsolatedRPC(config,project,vault) if config.get('isolation',{}).get('enabled') else StdioRPC(config['argv'],project['root'])
