import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {graphErrors} from './architectureTools';
import type {Architecture} from './types';

export type SharedDesign={id:string;project_id:string;revision:number;draft:Architecture;presence?:{name:string;actor:string}[];edits?:{revision:number;actor:string;paths:string[][]}[]};
export type SharedTarget={id:string;remote:boolean};
type Value=unknown;
type Patch={path:string[];value?:Value;remove?:boolean};
const object=(v:Value):v is Record<string,Value>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const equal=(a:Value,b:Value)=>JSON.stringify(a)===JSON.stringify(b);
function normalized(a:Architecture){return {...structuredClone(a),nodes:Object.fromEntries(a.nodes.map(n=>[n.id,n])),edges:Object.fromEntries(a.edges.map(e=>[e.source+'->'+e.target,e]))};}
function delta(before:Value,after:Value,path:string[]=[]):Patch[]{
  if(object(before)&&object(after))return [...new Set([...Object.keys(before),...Object.keys(after)])].flatMap(key=>!Object.hasOwn(after,key)?[{path:[...path,key],remove:true}]:!Object.hasOwn(before,key)?[{path:[...path,key],value:after[key]}]:delta(before[key],after[key],[...path,key]));
  return equal(before,after)?[]:[{path,value:after}];
}
export function mergeLocal(sent:Architecture,current:Architecture,server:Architecture):Architecture{
  const value=normalized(server) as unknown as Record<string,any>;
  for(const p of delta(normalized(sent),normalized(current))){
    let cursor=value;
    for(const key of p.path.slice(0,-1)){if(!Object.hasOwn(cursor,key)||!object(cursor[key]))Object.defineProperty(cursor,key,{value:{},writable:true,enumerable:true,configurable:true});cursor=cursor[key];}
    const key=p.path.at(-1)!;
    if(p.remove)delete cursor[key];else Object.defineProperty(cursor,key,{value:structuredClone(p.value),writable:true,enumerable:true,configurable:true});
  }
  return {...value,nodes:Object.values(value.nodes),edges:Object.values(value.edges)} as Architecture;
}
export const sharedCall=<T,>(target:SharedTarget,suffix='',method='GET',body?:unknown):Promise<T>=>target.remote?api<T>('/workbench/team/remote','POST',{path:'/api/team/architecture-sessions/'+target.id+suffix,method,body:body??null}):api<T>('/architecture-workbench/sessions/'+target.id+suffix,method,body);

export function useSharedArchitecture(architecture:Architecture,adopt:(a:Architecture,baseline:Architecture)=>void,initial?:SharedTarget){
  const [target,setTarget]=useState(initial),[row,setRow]=useState<SharedDesign>(),[status,setStatus]=useState(''),[blocked,setBlocked]=useState(false);
  const local=useRef(architecture),base=useRef<SharedDesign|undefined>(undefined),busy=useRef(false),accept=useRef(adopt),epoch=useRef(0);
  local.current=architecture;accept.current=adopt;
  useEffect(()=>{base.current=undefined;setRow(undefined);setBlocked(false);setStatus('');epoch.current++;},[target?.id,target?.remote]);
  async function poll(){
    if(!target||busy.current)return;
    const generation=epoch.current;
    try{const next=await sharedCall<SharedDesign>(target);if(generation!==epoch.current)return;
      if(!base.current||equal(local.current,base.current.draft)){base.current=next;accept.current(next.draft,next.draft);}
      setRow(next);
    }catch(e){if(generation===epoch.current)setStatus((e as Error).message);}
  }
  useEffect(()=>{if(!target)return;void poll();const timer=setInterval(()=>void poll(),2000);return()=>clearInterval(timer);},[target?.id,target?.remote]);
  async function sync(){
    if(!target||!base.current||busy.current||blocked||graphErrors(local.current).length)return;
    if(equal(local.current,base.current.draft))return base.current;
    const generation=epoch.current,sent=structuredClone(local.current);busy.current=true;setStatus('Syncing draft…');
    try{const next=await sharedCall<SharedDesign>(target,'/edits','POST',{base_revision:base.current.revision,architecture:sent});if(generation!==epoch.current)return;
      const merged=mergeLocal(sent,local.current,next.draft);base.current=next;accept.current(merged,next.draft);setRow(next);setStatus('Draft synced');return equal(merged,next.draft)?next:undefined;
    }catch(e){if(generation===epoch.current){setStatus((e as Error).message);setBlocked(true);}}
    finally{busy.current=false;}
  }
  useEffect(()=>{if(!target||blocked)return;const timer=setTimeout(()=>void sync(),900);return()=>clearTimeout(timer);},[JSON.stringify(architecture),target?.id,blocked,row?.revision]);
  async function reload(){if(!target||busy.current)return;const next=await sharedCall<SharedDesign>(target);base.current=next;accept.current(next.draft,next.draft);setRow(next);setBlocked(false);setStatus('Loaded shared draft');}
  return {target,row,status,blocked,sync,reload,canPublish:!!base.current&&row?.revision===base.current.revision&&!busy.current&&!blocked,start:setTarget,stop:()=>setTarget(undefined)};
}
