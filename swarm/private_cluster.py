"""Opt-in vLLM multiprocessing clusters on trusted private Linux GPU workers."""
import asyncio
import hashlib
import ipaddress
import json
from pathlib import Path
import platform
import re
import socket
import time
import httpx
from .providers import RouteError
from .isolation import command
from .hardware import hardware
from .privacy import digest
from .store import uid


def private_ip(value):
    try:ip=ipaddress.IPv4Address(value)
    except ValueError as e:raise RouteError('Use a private IPv4 interface address') from e
    if not any(ip in ipaddress.IPv4Network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):raise RouteError('Distributed inference is limited to trusted RFC1918 networks')
    return str(ip)


def image_digest(image):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}',image):raise RouteError('Pin an installed vLLM Linux image by digest')
    return image


def model_manifest(root,name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',name):raise RouteError('Select a registered model directory name')
    base=Path(root).resolve();folder=(base/name).resolve()
    if not folder.is_relative_to(base) or folder==base or not folder.is_dir():raise RouteError('Cluster model is outside the worker model root')
    entries=[];size=0
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():raise RouteError('Cluster model symlinks are not supported')
        if not path.is_file():continue
        if len(entries)>=2000:raise RouteError('Cluster model has too many files')
        relative=path.relative_to(folder).as_posix()
        if relative.endswith(('.py','.so','.dll')):raise RouteError('Cluster models must not require remote code')
        h=hashlib.sha256()
        with path.open('rb') as stream:
            while chunk:=stream.read(1024*1024):h.update(chunk)
        entries.append(dict(path=relative,size=path.stat().st_size,sha256=h.hexdigest()));size+=path.stat().st_size
    if not (folder/'config.json').is_file() or not any(e['path'].endswith('.safetensors') for e in entries):raise RouteError('Use a complete local safetensors model with config.json')
    return dict(folder=str(folder),digest=digest(entries),size=size,files=entries)


class ClusterWorker:
    def __init__(self,store,root,image,model_root,address,budgets):
        self.store,self.root,self.image,self.model_root,self.address,self.budgets=store,Path(root),image,model_root,address,budgets

    def describe(self):
        return dict(enabled=bool(self.image and self.model_root and self.address),image=self.image,address=self.address,system=platform.system(),models=[p.name for p in Path(self.model_root).iterdir() if p.is_dir() and (p/'config.json').is_file()] if self.model_root and Path(self.model_root).is_dir() else [])

    def models(self):
        return [dict(name=r['served_name'],size=0,loaded=True,distributed=True) for r in self.store.all('owned_cluster') if r.get('rank')==0 and r.get('status')=='running' and r['expires_at']>time.time()]

    async def stop(self,id):
        row=self.store.get('owned_cluster',id)
        if row:
            try:await command(['docker','rm','-f',row['container']],timeout=15)
            except (RouteError,OSError):pass
            self.store.patch('owned_cluster',id,dict(status='stopped'))

    async def reap(self,revoked=False):
        for row in self.store.all('owned_cluster'):
            if row.get('status')=='running' and (revoked or row['expires_at']<time.time()):await self.stop(row['id'])

    async def handle(self,payload):
        if not self.describe()['enabled'] or platform.system()!='Linux':raise RouteError('vLLM clusters require explicitly enabled private Linux GPU workers')
        image=image_digest(self.image);address=private_ip(self.address);action=payload.get('action')
        if action=='inspect':
            inspected=json.loads(await command(['docker','image','inspect',image],timeout=15))
            if not inspected or inspected[0].get('Os')!='linux':raise RouteError('Install the pinned Linux image first')
            help=await command(['docker','run','--pull','never','--rm','--network','none','--entrypoint','vllm',image,'serve','--help'],timeout=45,limit=1_000_000)
            if any(flag not in help for flag in ('--nnodes','--node-rank','--master-addr','--headless')):raise RouteError('This pinned vLLM version lacks the required multiprocessing flags')
            manifest=await asyncio.to_thread(model_manifest,self.model_root,payload['model'])
            import psutil
            addresses={a.address for values in psutil.net_if_addrs().values() for a in values if a.family==socket.AF_INET}
            if address not in addresses:raise RouteError('Cluster address does not belong to a local interface')
            return dict(image=image,address=address,manifest_digest=manifest['digest'],size=manifest['size'],hardware=hardware(),budgets=self.budgets,verified_at=time.time())
        id=payload.get('id','')
        if not re.fullmatch(r'cluster_[A-Za-z0-9_-]{1,80}',id):raise RouteError('Invalid private cluster identity')
        if action=='stop':await self.stop(id);return dict(stopped=True)
        row=self.store.get('owned_cluster',id)
        if action=='renew':
            if not row or row['status']!='running':raise RouteError('Owned cluster is not running')
            self.store.patch('owned_cluster',id,dict(expires_at=time.time()+120));return dict(renewed=True)
        if action=='health':
            if not row or row['status']!='running':raise RouteError('Owned cluster is not running')
            if row['rank']==0:
                async with httpx.AsyncClient(timeout=5,trust_env=False) as client:
                    try:
                        response=await client.get(f"http://127.0.0.1:{row['port']}/health")
                        return dict(healthy=response.status_code==200)
                    except httpx.HTTPError:return dict(healthy=False)
            inspected=json.loads(await command(['docker','inspect',row['container']],timeout=10))
            return dict(healthy=bool(inspected[0]['State']['Running']))
        if action!='start':raise RouteError('Unsupported cluster control action')
        if row and row.get('status')=='running':raise RouteError('Cluster is already running on this worker')
        if any(r.get('status')=='running' for r in self.store.all('owned_cluster')):raise RouteError('This worker already owns a cluster allocation')
        count,rank,tp=payload['nodes'],payload['rank'],payload['tensor_parallel']
        if any(isinstance(v,bool) or not isinstance(v,int) for v in (count,rank,tp)) or not 2<=count<=8 or not 0<=rank<count or not 1<=tp<=8:raise RouteError('Invalid cluster topology')
        head=private_ip(payload['head']);manifest=await asyncio.to_thread(model_manifest,self.model_root,payload['model'])
        if manifest['digest']!=payload['manifest_digest'] or payload['image']!=image:raise RouteError('Model or runtime differs from the approved placement')
        hw=hardware();gpus=hw.get('gpus',[])
        if len(gpus)<tp or any(g.get('free_gb') is None for g in gpus[:tp]):raise RouteError('Live GPU memory telemetry is required for cluster placement')
        estimated=manifest['size']/1024**3/count/tp*1.25+2
        if any(min(g['free_gb'],self.budgets['vram_gb'])<estimated for g in gpus[:tp]):raise RouteError('Model shard and cache exceed live GPU memory or worker allocation')
        if self.budgets['ram_gb']<2:raise RouteError('Allocate at least 2 GiB RAM to this cluster worker')
        port=payload.get('port',8200);master_port=payload.get('master_port',29501)
        if not 1024<=port<=65535 or not 1024<=master_port<=65535 or port==master_port:raise RouteError('Invalid cluster port selection')
        probe=None
        if rank:
            started=time.monotonic()
            try:
                reader,writer=await asyncio.wait_for(asyncio.open_connection(head,master_port),10);writer.close();await writer.wait_closed();probe=round((time.monotonic()-started)*1000,2)
            except (OSError,TimeoutError) as e:raise RouteError('Cannot reach the private cluster master; check routing and firewall') from e
        name='swarm-vllm-'+id
        args=['docker','run','--pull','never','-d','--name',name,'--network','host','--gpus',f'device={",".join(str(i) for i in range(tp))}','--memory',str(int(self.budgets['ram_gb']*1024))+'m','--shm-size','1g','--cap-drop','ALL','--security-opt','no-new-privileges','--mount',f'type=bind,src={manifest["folder"]},dst=/model,readonly','--env','VLLM_HOST_IP='+address,'--env','DO_NOT_TRACK=1','--entrypoint','vllm',image,'serve','/model','--served-model-name',id,'--host','127.0.0.1','--port',str(port),'--tensor-parallel-size',str(tp),'--pipeline-parallel-size',str(count),'--distributed-executor-backend','mp','--nnodes',str(count),'--node-rank',str(rank),'--master-addr',head,'--master-port',str(master_port),'--max-model-len',str(self.budgets['context']),'--gpu-memory-utilization',str(min(.8,self.budgets['vram_gb']/max(g['total_gb'] for g in gpus[:tp])))]
        if rank:args+=['--headless']
        self.store.put('owned_cluster',id,dict(id=id,container=name,rank=rank,port=port,served_name='cluster:'+id,status='running',expires_at=time.time()+120))
        try:await command(args,timeout=30)
        except BaseException:await self.stop(id);raise
        return dict(started=True,tcp_latency_ms=probe,note='TCP reachability is not tensor bandwidth; benchmark the actual workload')

    async def infer(self,model,messages,max_tokens,usage,native_tools,native_calls):
        id=model.removeprefix('cluster:');row=self.store.get('owned_cluster',id)
        if not row or row.get('rank')!=0 or row['status']!='running' or row['expires_at']<time.time():raise RouteError('Private cluster lease is unavailable')
        from .protocol import usage_counts
        body=dict(model=id,messages=messages,max_tokens=max_tokens,stream=True,stream_options=dict(include_usage=True))
        if native_tools:body['tools']=native_tools
        async with httpx.AsyncClient(timeout=90,trust_env=False) as client:
            async with client.stream('POST',f"http://127.0.0.1:{row['port']}/v1/chat/completions",json=body) as response:
                if response.status_code!=200:raise RouteError('Private cluster inference rejected the request')
                async for line in response.aiter_lines():
                    if not line.startswith('data: '):continue
                    if line[6:]=='[DONE]':return
                    event=json.loads(line[6:])
                    if event.get('usage'):usage.update(usage_counts(event))
                    for choice in event.get('choices',[]):
                        delta=choice.get('delta',{})
                        if delta.get('tool_calls'):raise RouteError('Cluster native tools are not supported yet; use structured tool requests')
                        if delta.get('content'):yield delta['content']


class PrivateCluster:
    def __init__(self,store,pool):self.store,self.pool=store,pool

    async def control(self,device_id,payload,timeout=120):
        device=self.store.get('device',device_id,{})
        if not device.get('approved') or device.get('revoked') or not device.get('cluster_approved') or not device.get('cluster',{}).get('enabled') or time.time()-device.get('last_seen',0)>35:raise RouteError('Select an online worker separately approved for private clustering')
        id=uid('control_');self.store.put('job',id,dict(id=id,device_id=device_id,kind='cluster',model='private cluster control',messages=[],text='',status='queued',deadline=time.time()+timeout,created_at=time.time(),payload=payload))
        try:
            while True:
                job=self.store.get('job',id)
                if job['status']=='done':return job['cluster_result']
                if job['status'] in {'failed','expired','cancelled'}:raise RouteError(job.get('error') or 'Private cluster control failed')
                if time.time()>job['deadline'] or (job['status']=='leased' and time.time()>job['lease_until']):raise RouteError('Private cluster worker lease expired')
                await asyncio.sleep(.2)
        finally:
            if self.store.get('job',id)['status'] in {'queued','leased'}:self.store.patch('job',id,dict(status='cancelled'))

    async def preview(self,devices,model,tp):
        if not 2<=len(devices)<=8 or len(set(devices))!=len(devices) or not 1<=tp<=8:raise RouteError('Choose 2–8 distinct workers and 1–8 GPUs per worker')
        nodes=[]
        for id in devices:nodes.append(dict(device_id=id,**await self.control(id,dict(action='inspect',model=model),timeout=600)))
        if len({n['manifest_digest'] for n in nodes})!=1 or len({n['image'] for n in nodes})!=1 or len({n['address'] for n in nodes})!=len(nodes):raise RouteError('Workers need identical model bytes, pinned images and distinct private interface addresses')
        for node in nodes:
            private_ip(node['address']);gpus=node['hardware'].get('gpus',[]);estimated=node['size']/1024**3/len(nodes)/tp*1.25+2
            if len(gpus)<tp or any(g.get('free_gb') is None or min(g['free_gb'],node['budgets']['vram_gb'])<estimated for g in gpus[:tp]):raise RouteError('A model shard does not fit an approved live GPU allocation')
        row=dict(id=uid('placement_'),nodes=nodes,model=model,tensor_parallel=tp,expires_at=time.time()+300,note='Trusted private network only. Placement is an estimate; actual startup and task tests determine compatibility.')
        row['review_digest']=digest(row);return self.store.put('cluster_placement',row['id'],row)

    async def deploy(self,id,placement_id,review_digest):
        plan=self.store.get('cluster_placement',placement_id);started=[]
        async def renew():
            while True:
                await asyncio.sleep(25)
                for device in list(started):await self.control(device,dict(action='renew',id=id))
        renewal=asyncio.create_task(renew())
        try:
            if not plan or plan['review_digest']!=review_digest or plan['expires_at']<time.time():raise RouteError('Cluster placement expired or changed; inspect again')
            self.store.patch('private_cluster',id,dict(status='starting',placement=plan))
            for rank,node in enumerate(plan['nodes']):
                payload=dict(action='start',id=id,nodes=len(plan['nodes']),rank=rank,head=plan['nodes'][0]['address'],tensor_parallel=plan['tensor_parallel'],model=plan['model'],image=node['image'],manifest_digest=node['manifest_digest'])
                await self.control(node['device_id'],payload,timeout=600);started.append(node['device_id'])
                if rank==0:await asyncio.sleep(3)
            deadline=time.time()+480;healthy=False
            while time.time()<deadline:
                statuses=[await self.control(node['device_id'],dict(action='renew',id=id)) for node in plan['nodes']]
                checks=[await self.control(node['device_id'],dict(action='health',id=id)) for node in plan['nodes']]
                if all(c['healthy'] for c in checks):healthy=True;break
                await asyncio.sleep(3)
            if not healthy:raise RouteError('Private cluster did not pass startup health checks')
            self.store.patch('private_cluster',id,dict(status='running',model='cluster:'+id,head_device_id=plan['nodes'][0]['device_id']))
            while True:
                await asyncio.sleep(30)
                for node in plan['nodes']:await self.control(node['device_id'],dict(action='renew',id=id))
        except asyncio.CancelledError:self.store.patch('private_cluster',id,dict(status='stopped'));raise
        except Exception as error:self.store.patch('private_cluster',id,dict(status='failed',error=str(error) if isinstance(error,RouteError) else 'Private cluster startup failed'))
        finally:
            renewal.cancel();await asyncio.gather(renewal,return_exceptions=True)
            for device in started:
                try:await self.control(device,dict(action='stop',id=id),timeout=20)
                except RouteError:pass
