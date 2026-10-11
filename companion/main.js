import {invoke} from '@tauri-apps/api/core';
import {scan,Format,cancel} from '@tauri-apps/plugin-barcode-scanner';
import './style.css';
const app=document.querySelector('#app');let connected=false,selected='',snapshot,mission,questions=[],artifacts=[],timer,generation=0;
const h=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
const button=(text,fn)=>{const el=h('button',text);el.addEventListener('click',()=>act(fn));return el;};
const remote=(path,method='GET',body=null)=>invoke('remote_request',{path:'/api/team'+path,method,body});
const alert=h('p','','error');alert.setAttribute('role','alert');
async function act(fn){try{alert.textContent='';await fn();}catch(e){alert.textContent=String(e);}}
function shell(){app.replaceChildren(h('h1','Swarm Companion'),alert);}
async function preview(text){const bundle=JSON.parse(text);const review=await invoke('inspect_pair',{bundle});shell();app.append(h('h2','Review coordinator'),h('p',review.origin),h('p','Certificate SHA-256: '+review.certificate_sha256),h('p','Verify this origin and certificate with the desktop owner before pairing.'));app.append(button('Pair with this coordinator',async()=>{await invoke('pair',{bundle});connected=true;await refresh();}),button('Cancel pairing',()=>login()));}
function login(){connected=false;snapshot=undefined;selected='';mission=undefined;questions=[];artifacts=[];generation++;clearTimeout(timer);shell();app.append(h('p','Link a project-scoped invitation from your desktop. Credentials stay in native session memory and are cleared when the application closes.'));
 app.append(button('Scan pairing QR',async()=>{try{const result=await scan({formats:[Format.QRCode],windowed:false});await preview(result.content);}finally{await cancel().catch(()=>{});}}));
 const file=h('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Import pairing bundle');file.addEventListener('change',()=>act(async()=>{const selected=file.files?.[0];if(!selected||selected.size>20000)throw Error('Select a pairing JSON file below 20 KB');await preview(await selected.text());}));app.append(file);
 const paste=h('textarea');paste.placeholder='Or paste the pairing JSON';paste.setAttribute('aria-label','Pairing JSON');app.append(paste,button('Inspect pasted invitation',()=>preview(paste.value)));}
function render(){shell();app.append(h('p',snapshot.actor.name+' · '+snapshot.actor.role),button('Disconnect and clear credential',async()=>{await invoke('disconnect');login();}),button('Refresh',refresh));
 if(!selected){app.append(h('h2','Missions'));for(const r of snapshot.runs.slice().reverse()){const card=h('section',undefined,'card');card.append(h('h3',r.task),h('p',r.status),button('Open mission',async()=>{selected=r.id;await refresh();}));app.append(card);}if(!snapshot.runs.length)app.append(h('p','No shared project missions yet.'));return;}
 app.append(button('Back to missions',async()=>{selected='';mission=undefined;await refresh();}));if(!mission)return;
 app.append(h('h2',mission.task),h('p',mission.status));if(snapshot.actor.role==='operator')app.append(button(mission.status==='running'?'Pause mission':'Resume mission',async()=>{await remote('/runs/'+selected+'/'+(mission.status==='running'?'pause':'resume'),'POST');await refresh();}));
 for(const a of mission.agents){const card=h('section',undefined,'card');card.append(h('h3',a.name+' · '+a.status),h('p',a.role));const output=a.result?.content||a.messages?.filter(m=>m.role==='assistant'&&m.complete&&!m.interrupted).at(-1)?.content;const details=h('details');details.append(h('summary','Inspect agent work'),h('pre',output||'No complete output yet.'));card.append(details);
 if(snapshot.actor.role==='operator'){const message=h('textarea');message.setAttribute('aria-label','Message '+a.name);card.append(message,button('Send direction',async()=>{await remote('/agents/'+a.id+'/chat','POST',{content:message.value});message.value='';await refresh();}));}app.append(card);}
 for(const q of questions.filter(q=>q.run_id===selected)){const card=h('section',undefined,'card');card.append(h('h3',q.question));if(snapshot.actor.role==='operator'){const answer=h('textarea');answer.setAttribute('aria-label','Answer agent question');card.append(answer,button('Send answer',async()=>{await remote('/questions/'+q.id+'/answer','POST',{answer:answer.value});await refresh();}));}app.append(card);}
 if(snapshot.actor.role==='reviewer')for(const a of artifacts.filter(a=>a.status==='staged'&&mission.agents.some(n=>n.id===a.agent_id))){const card=h('section',undefined,'card');card.append(h('h3',a.path),h('pre',a.diff||a.content),button('Apply reviewed change',async()=>{await remote('/artifacts/'+a.id+'/apply','POST');await refresh();}));app.append(card);}
}
async function refresh(){clearTimeout(timer);if(!connected)return;const current=generation;const next=await remote('/snapshot');const data=selected?await remote('/runs/'+selected):undefined;const qs=await remote('/questions');const changes=next.actor.role==='reviewer'?await remote('/artifacts'):[];if(!connected||current!==generation)return;snapshot=next;mission=data;questions=qs;artifacts=changes;
 // Keep an edited message visible during polling. The owner can explicitly refresh.
 if(!document.querySelector('textarea:focus'))render();timer=setTimeout(()=>act(refresh),4000);}
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else if(connected)act(refresh);});
login();
