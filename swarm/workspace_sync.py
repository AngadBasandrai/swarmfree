"""Selective encrypted snapshots with preview, conflict checks and replay receipts."""
import base64
import copy
import json
import os
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .full_backup import key_for
from .privacy import digest
from .providers import RouteError
from .schema import Architecture,AgentSpec
from .store import uid

KINDS={'architecture','agent_blueprint','knowledge','run'}
MAX_BYTES=1_000_000


def seal(store,selection,password):
    rows=[]
    if not 1<=len(selection)<=200:raise RouteError('Select 1–200 workspace items')
    for item in selection:
        kind,id=item['kind'],item['id']
        if kind not in KINDS:raise RouteError('Only designs, blueprints, knowledge and run summaries can sync')
        value=copy.deepcopy(store.get(kind,id))
        if not value:raise RouteError('Selected sync item is missing')
        if kind=='architecture':
            value=Architecture.model_validate(value).model_dump();value['regression_gate_id']='';value['builtin']=False
        elif kind=='agent_blueprint':value=dict(id=id,name=value['name'],spec=AgentSpec.model_validate(value['spec']).model_dump())
        elif kind=='knowledge':
            if store.get('knowledge_sync_approval',id,{}).get('digest')!=digest(value):raise RouteError('Approve current knowledge before exporting it')
            value={k:value.get(k) for k in ('id','project_id','title','content','revision','data_labels','sources','depends_on','approved')}
        else:
            value={k:value.get(k) for k in ('id','task','status','created_at','finished_at','results','brief','project_id')}
        rows.append(dict(kind=kind,id=id,value=value))
    body=dict(id=uid('sync_'),version=1,created_at=time.time(),rows=rows)
    from .privacy import scan
    if scan(body):raise RouteError('Sync selection contains credential-like text; remove it before export')
    raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>MAX_BYTES:raise RouteError('Sync selection exceeds 2 MB; select fewer items')
    salt,nonce=os.urandom(16),os.urandom(12)
    cipher=AESGCM(key_for(password,salt)).encrypt(nonce,raw,b'SwarmWorkspaceSync1')
    return dict(version=1,salt=base64.b64encode(salt).decode(),nonce=base64.b64encode(nonce).decode(),ciphertext=base64.b64encode(cipher).decode())


def open_bundle(envelope,password):
    if len(json.dumps(envelope))>MAX_BYTES*1.4:raise RouteError('Sync envelope exceeds its size limit')
    try:
        if envelope['version']!=1:raise ValueError()
        salt=base64.b64decode(envelope['salt'],validate=True);nonce=base64.b64decode(envelope['nonce'],validate=True)
        if len(salt)!=16 or len(nonce)!=12:raise ValueError()
        data=json.loads(AESGCM(key_for(password,salt)).decrypt(nonce,base64.b64decode(envelope['ciphertext'],validate=True),b'SwarmWorkspaceSync1'))
        if not isinstance(data['id'],str) or len(data['id'])>100 or not 1<=len(data['rows'])<=200:raise ValueError()
        if len({(r['kind'],r['id']) for r in data['rows']})!=len(data['rows']):raise ValueError()
        for row in data['rows']:
            if row['kind'] not in KINDS or not isinstance(row['id'],str) or len(row['id'])>100 or row['value']['id']!=row['id']:raise ValueError()
            if row['kind']=='architecture':row['value']=Architecture.model_validate(row['value']).model_dump()
            elif row['kind']=='agent_blueprint':row['value']['spec']=AgentSpec.model_validate(row['value']['spec']).model_dump()
    except Exception as e:raise RouteError('Cannot decrypt or validate this sync bundle') from e
    return data


def destination(kind):return kind if kind in {'architecture','agent_blueprint'} else 'synced_'+kind


def preview(store,envelope,password):
    data=open_bundle(envelope,password);items=[]
    receipt=store.get('sync_receipt',data['id'])
    if receipt and receipt.get('bundle_digest')!=digest(data):raise RouteError('Bundle identity was already applied with different contents')
    for row in data['rows']:
        current=store.get(destination(row['kind']),row['id'])
        items.append(dict(kind=row['kind'],id=row['id'],name=row['value'].get('name',row['value'].get('title',row['id'])),existing_digest=digest(current),conflict=bool(current and digest(current)!=digest(row['value'])),protected=bool(current and current.get('builtin'))))
    result=dict(bundle_id=data['id'],items=items,already_applied=bool(store.get('sync_receipt',data['id'])))
    return dict(**result,review_digest=digest(result))


def apply(store,envelope,password,review_digest,choices):
    from .composition import resolve
    with store.transaction():
        review=preview(store,envelope,password)
        if review['review_digest']!=review_digest:raise RouteError('Workspace changed since preview; review conflicts again')
        if review['already_applied']:return store.get('sync_receipt',review['bundle_id'])
        data=open_bundle(envelope,password);applied=[]
        for row,info in zip(data['rows'],review['items']):
            action=choices.get(row['kind']+':'+row['id'],'skip' if info['conflict'] or info['protected'] else 'import')
            if action=='skip':continue
            if info['protected'] or action not in {'import','replace'} or (info['conflict'] and action!='replace'):raise RouteError('Explicit replacement is required for this conflict; built-ins are protected')
            value=copy.deepcopy(row['value']);kind=destination(row['kind'])
            if kind=='architecture':
                old=store.get(kind,row['id'],{})
                if old.get('regression_gate_id'):raise RouteError('A gated local architecture cannot be replaced through sync')
                value.update(builtin=False,revision=(old.get('revision') or 0)+1,regression_gate_id='')
            if kind.startswith('synced_'):value.update(read_only=True,requires_local_review=True)
            store.put(kind,row['id'],value);applied.append(dict(kind=kind,id=row['id']))
        for row in applied:
            if row['kind']=='architecture':
                value=store.get('architecture',row['id']);resolve(store,value)
                from .architecture_workbench import Workshop
                Workshop(store).record(value,value['revision'],'encrypted-sync')
        receipt=dict(id=data['id'],items=applied,applied_at=time.time(),bundle_digest=digest(data))
        return store.put('sync_receipt',data['id'],receipt)
