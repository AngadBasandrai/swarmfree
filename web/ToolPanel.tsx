import { useEffect, useRef, useState } from 'react';
import { api } from './api';
type Row=Record<string,any>;
type Action=<T>(fn:()=>Promise<T>)=>Promise<T|undefined>;
export function ToolPanel({url,panel,action}:{url:string;panel:Row;action:Action}){
 const frame=useRef<HTMLIFrameElement>(null);const [pending,setPending]=useState<Row>();const waiting=useRef(false);const [error,setError]=useState('');
 const send=(message:Row)=>frame.current?.contentWindow?.postMessage({jsonrpc:'2.0',...message},'*');
 useEffect(()=>{
  let initialized=false;let requests=0;
  const receive=(event:MessageEvent)=>{
   if(event.source!==frame.current?.contentWindow||event.origin!=='null')return;
   const m=event.data;if(!m||m.jsonrpc!=='2.0'||typeof m.method!=='string')return;
   if(JSON.stringify(m).length>64000||++requests>200){setError('Panel message limit reached');return;}
   if(m.method==='ui/initialize'){send({id:m.id,result:{protocolVersion:'2026-01-26',hostInfo:{name:'Swarm Workbench',version:'0.6.0'},hostCapabilities:{serverTools:{}},hostContext:{displayMode:'inline',availableDisplayModes:['inline'],theme:'light'}}});return;}
   if(m.method==='ui/notifications/initialized'){initialized=true;return;}
   if(!initialized){if(m.id!==undefined)send({id:m.id,error:{code:-32000,message:'Initialize this panel first'}});return;}
   if(m.method==='ping'){send({id:m.id,result:{}});return;}
   if(m.method==='ui/notifications/size-changed'){const h=Number(m.params?.height);if(Number.isFinite(h)&&frame.current)frame.current.style.height=Math.max(240,Math.min(900,h))+'px';return;}
   if(m.method==='tools/call'&&m.id!==undefined&&!waiting.current){waiting.current=true;setPending(m);return;}
   if(m.id!==undefined)send({id:m.id,error:{code:-32601,message:'This host does not grant that panel capability'}});
  };
  window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);
 },[panel.id]);
 const deny=()=>{if(pending)send({id:pending.id,error:{code:-32000,message:'Owner declined this tool interaction'}});setPending(undefined);waiting.current=false;};
 return <section className="tool-panel"><iframe ref={frame} title="Isolated MCP tool panel" src={url} sandbox="allow-scripts" referrerPolicy="no-referrer" allow="camera 'none'; microphone 'none'; geolocation 'none'"/>{error&&<p role="alert">{error}</p>}{pending&&<div className="panel-review"><h3>Review tool interaction: {pending.params?.name}</h3><pre>{JSON.stringify(pending.params?.arguments||{},null,2)}</pre><button onClick={()=>void action(async()=>{const p=pending;try{const result=await api('/ops/apps/'+panel.id+'/call','POST',{tool_name:p.params?.name,arguments:p.params?.arguments||{}});send({id:p.id,result});}catch(e){send({id:p.id,error:{code:-32000,message:e instanceof Error?e.message:'Tool interaction failed'}});throw e;}finally{setPending(undefined);waiting.current=false;}})}>Allow reviewed tool interaction</button><button onClick={deny}>Decline interaction</button></div>}</section>;
}
