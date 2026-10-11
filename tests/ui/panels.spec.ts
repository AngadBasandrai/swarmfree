import {test,expect} from '@playwright/test';
import {createServer} from 'node:http';

test('MCP panel initializes in an opaque frame and tools require visible owner review',async({page,request})=>{
 let calls=0;
 const html=`<!doctype html><html><body><p id="state">Starting</p><button id="call">Calculate</button><script>
 window.addEventListener('message',e=>{if(e.data.id===1){parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized'},'*');document.querySelector('#state').textContent='Connected';}if(e.data.id===2)document.querySelector('#state').textContent=e.data.result?.content?.[0]?.text||'Denied';});
 parent.postMessage({jsonrpc:'2.0',id:1,method:'ui/initialize',params:{protocolVersion:'2026-01-26',appCapabilities:{}}},'*');
 document.querySelector('#call').onclick=()=>parent.postMessage({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:'calculate',arguments:{value:4}}},'*');
 </script></body></html>`;
 const tool={name:'calculate',inputSchema:{type:'object',properties:{value:{type:'number'}},required:['value']},_meta:{ui:{resourceUri:'ui://fixture/panel'}}};
 const server=createServer(async(req,res)=>{let text='';for await(const chunk of req)text+=chunk;const m=JSON.parse(text);res.setHeader('Content-Type','application/json');
  if(m.method==='notifications/initialized'){res.statusCode=202;res.end();return;}
  let result={};if(m.method==='initialize')result={protocolVersion:'2025-11-25',capabilities:{}};
  if(m.method==='tools/list')result={tools:[tool]};if(m.method==='resources/read')result={contents:[{uri:'ui://fixture/panel',mimeType:'text/html;profile=mcp-app',text:html}]};
  if(m.method==='tools/call'){calls++;result={content:[{type:'text',text:'Result: '+m.params.arguments.value*2}]};}res.end(JSON.stringify({jsonrpc:'2.0',id:m.id,result}));});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));const address=server.address() as {port:number};
 try{
  await request.post('/api/session',{data:{}});
  const created=await request.post('/api/extensions',{data:{name:'Interactive fixture',kind:'mcp',transport:'http',url:'http://127.0.0.1:'+address.port,enabled:true}});const extension=await created.json();expect(created.ok()).toBeTruthy();
  const p=await (await request.post('/api/projects',{data:{name:'Panel fixture',root:process.cwd(),tools:[],checks:{}}})).json();
  await request.put('/api/ops/apps/'+extension.id,{data:{enabled:true}});await request.post('/api/extensions/'+extension.id+'/discover',{data:{project_id:p.id}});
  const grant=await request.put('/api/projects/'+p.id,{data:{name:p.name,root:process.cwd(),tools:['mcp.'+extension.id+'.calculate'],checks:{}}});expect(grant.ok()).toBeTruthy();
  await page.goto('/');await page.getByRole('button',{name:'Command center',exact:true}).click();await page.getByRole('tab',{name:'Tool panels'}).click();
  await page.getByLabel('Command center project').selectOption(p.id);await page.getByLabel('Panel extension').selectOption(extension.id);await page.getByLabel('Panel tool').selectOption('calculate');
  await page.getByRole('button',{name:'Inspect panel resource'}).click();await page.getByRole('button',{name:'Open reviewed panel'}).click();
  const frame=page.frameLocator('iframe[title="Isolated MCP tool panel"]');await expect(frame.locator('#state')).toHaveText('Connected');await frame.getByRole('button',{name:'Calculate'}).click();
  await expect(page.getByRole('heading',{name:'Review tool interaction: calculate'})).toBeVisible();expect(calls).toBe(0);
  await page.getByRole('button',{name:'Allow reviewed tool interaction'}).click();await expect(frame.locator('#state')).toHaveText('Result: 8');expect(calls).toBe(1);
 }finally{await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
