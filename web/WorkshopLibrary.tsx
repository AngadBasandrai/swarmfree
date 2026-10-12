import {useEffect,useState} from 'react';
import {api} from './api';
import type {Architecture,AgentSpec} from './types';
type Action=<T>(fn:()=>Promise<T>)=>Promise<T|undefined>;
type Blueprint={id:string;name:string;spec:AgentSpec};
type Revision={number:number;created_at:number;actor:string;architecture:Architecture};
export function AgentLibrary({node,add,action}:{node:AgentSpec;add:(node:AgentSpec)=>void;action:Action}){
  const [rows,setRows]=useState<Blueprint[]>([]),[name,setName]=useState('');
  const load=async()=>setRows((await api<{agents:Blueprint[]}>('/architecture-workbench')).agents);
  useEffect(()=>{void action(load);},[]);
  return <details className="workshop-panel"><summary>Personal agent library</summary><div className="workshop-inline"><label className="field"><span>Blueprint name</span><input aria-label="Blueprint name" value={name} placeholder={node.name} maxLength={80} onChange={e=>setName(e.target.value)}/></label><button className="secondary" onClick={()=>void action(async()=>{await api('/architecture-workbench/agents','POST',{name:name||node.name,spec:node});setName('');await load();})}>Save selected agent</button></div><p>Save instructions, tools, contracts and preferred model routes. Each inserted copy has its own identity.</p><div className="blueprint-list">{rows.map(row=><div key={row.id}><button className="secondary" draggable onDragStart={e=>e.dataTransfer.setData('application/swarm-node','blueprint:'+row.id)} onClick={()=>add(row.spec)}>{row.name}</button><button className="text-button" aria-label={'Delete blueprint '+row.name} onClick={()=>void action(async()=>{await api('/architecture-workbench/agents/'+row.id,'DELETE');await load();})}>Remove</button></div>)}</div>{!rows.length&&<small>Your saved agents will appear here.</small>}</details>;
}
export function VersionHistory({arch,load,action,refresh}:{arch:Architecture;load:(a:Architecture)=>void;action:Action;refresh:()=>Promise<void>}){
  const [rows,setRows]=useState<Revision[]>([]),[diff,setDiff]=useState<unknown>(),[selected,setSelected]=useState<number>();
  const fetchRows=async()=>setRows(await api<Revision[]>('/architecture-workbench/versions/'+arch.id));
  useEffect(()=>{setRows([]);setDiff(undefined);setSelected(undefined);if(!arch.builtin)void action(fetchRows);},[arch.id,arch.revision]);
  return <details className="workshop-panel"><summary>Saved version history {arch.revision?'· revision '+arch.revision:''}</summary><p>Restoring creates a new revision. Previously saved versions remain available. Save or export any unsaved edits before restoring.</p>{rows.map((row,index)=><div className="revision-row" key={row.number}><span>Revision {row.number} · {new Date(row.created_at*1000).toLocaleString()} · {row.actor}</span><button className="secondary" disabled={index===rows.length-1} onClick={()=>void action(async()=>{setSelected(row.number);setDiff(await api('/architecture-workbench/versions/'+arch.id+'/diff?left='+rows[index+1].number+'&right='+row.number));})}>Inspect changes</button><button className="secondary" onClick={()=>void action(async()=>{const saved=await api<Architecture>('/architecture-workbench/versions/'+arch.id+'/restore','POST',{number:row.number,expected_revision:arch.revision||0});load(saved);await refresh();})}>Restore revision {row.number}</button></div>)}{diff!==undefined&&<details open><summary>Changes in revision {selected}</summary><pre>{JSON.stringify(diff,null,2)}</pre></details>}{!rows.length&&<small>Save a custom architecture to create its first version.</small>}</details>;
}
