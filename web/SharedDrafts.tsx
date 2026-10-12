import {useEffect,useState} from 'react';
import {api} from './api';
import {Architectures} from './ArchitectureEditor';
import type {SharedDesign,SharedTarget} from './sharedArchitecture';
import type {Snapshot} from './types';
type Action=<T>(fn:()=>Promise<T>)=>Promise<T|undefined>;
export function SharedDrafts({snapshot,action,refresh}:{snapshot:Snapshot;action:Action;refresh:()=>Promise<void>}){
  const [rows,setRows]=useState<SharedDesign[]>([]),[remote,setRemote]=useState(false),[target,setTarget]=useState<SharedTarget>();
  const load=async()=>setRows(remote?await api<SharedDesign[]>('/workbench/team/remote','POST',{path:'/api/team/architecture-sessions',method:'GET',body:null}):(await api<{sessions:SharedDesign[]}>('/architecture-workbench')).sessions);
  useEffect(()=>{setRows([]);void action(load);},[remote]);
  if(target)return <div className="shared-editor"><button className="secondary" onClick={()=>{setTarget(undefined);void action(load);}}>Back to shared designs</button><Architectures key={target.id} sharedInitial={target} snapshot={snapshot} action={action} refresh={refresh}/></div>;
  return <article className="studio-card"><h2>Shared architecture drafts</h2><p>Share a design from Architectures. An architect credential can edit drafts for its selected projects. Connect another coordinator under Team rooms first. The owner publishes reviewed drafts to the mission library.</p><label className="checkbox"><input aria-label="Designs from linked coordinator" type="checkbox" checked={remote} onChange={e=>setRemote(e.target.checked)}/>Browse the linked coordinator</label><button className="secondary" onClick={()=>void action(load)}>Refresh shared designs</button>{rows.map(row=><div className="revision-row" key={row.id}><span><strong>{row.draft.name}</strong> · draft {row.revision} · project {row.project_id}</span><button className="secondary" onClick={()=>setTarget({id:row.id,remote})}>Edit shared design</button></div>)}{!rows.length&&<p>No shared designs are available.</p>}</article>;
}
