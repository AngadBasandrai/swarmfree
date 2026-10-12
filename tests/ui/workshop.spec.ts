import {test,expect,type Page} from '@playwright/test';
const node=(id:string,extra={})=>({id,name:id,role:'Complete work',instructions:'',x:0,y:0,...extra});
const unique=()=>Math.random().toString(36).slice(2,10);
async function connect(page:Page){await page.goto('/');await expect(page.getByRole('button',{name:'Architectures',exact:true})).toBeVisible();}
async function team(page:Page,nodes:any[],edges:any[]=[],extra={}){
  const value={id:'ui_'+unique(),name:'Workshop '+unique(),description:'',builtin:false,nodes,edges,...extra};
  const response=await page.request.post('/api/architectures',{data:value});expect(response.ok(),await response.text()).toBeTruthy();return response.json();
}
async function launch(page:Page,architecture:any,extra={}){
  const task='Workshop mission '+unique();const response=await page.request.post('/api/runs',{data:{task,architecture_id:architecture.id,...extra}});expect(response.ok(),await response.text()).toBeTruthy();const run=await response.json();
  await page.getByRole('button',{name:'Workbench',exact:true}).click();await page.locator('.run-list').getByRole('button').filter({hasText:task}).click();return run;
}

test('palette drop, branch controls and personal agent blueprints persist',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await connect(page);await page.getByRole('button',{name:'Architectures',exact:true}).click();
  const count=await page.locator('.react-flow__node').count();
  const transfer=await page.evaluateHandle(()=>new DataTransfer());await transfer.evaluate(t=>t.setData('application/swarm-node','router'));
  await page.locator('.react-flow').dispatchEvent('drop',{dataTransfer:transfer,clientX:700,clientY:600});await expect(page.locator('.react-flow__node')).toHaveCount(count+1);
  await expect(page.getByLabel('Node behavior')).toHaveValue('router');await page.getByLabel('Agent name',{exact:true}).fill('Saved specialist');
  await page.getByText('Branch controls and model preference',{exact:true}).click();await page.getByLabel('Breakpoint before node').check();await page.getByLabel('Branch request allowance').fill('3');await page.getByLabel('Branch token allowance').fill('5000');
  await page.getByText('Personal agent library',{exact:true}).click();await page.getByLabel('Blueprint name').fill('Research specialist');await page.getByRole('button',{name:'Save selected agent',exact:true}).click();
  await page.locator('.blueprint-list').getByRole('button',{name:'Research specialist',exact:true}).click();await expect(page.locator('.react-flow__node')).toHaveCount(count+2);await expect(page.getByLabel('Branch request allowance')).toHaveValue('3');
  expect(errors).toEqual([]);await page.screenshot({path:'test-results/workshop-palette.png',fullPage:true});
});

test('typed bindings, block extraction, parameters and revision restore are editable',async({page})=>{
  await connect(page);await page.getByRole('button',{name:'Architectures',exact:true}).click();await page.getByLabel('Architecture template').selectOption('pipeline');
  await page.getByRole('button',{name:'Select agent Builder',exact:true}).click();await page.getByText('Typed connection fields',{exact:true}).click();await page.getByRole('button',{name:'Map field from Planner',exact:true}).click();await page.getByLabel('Output selector planner 1').fill('findings');await page.getByLabel('Input name planner 1').fill('evidence');
  await page.getByLabel('Include Builder in block').check();await page.getByLabel('Block name').fill('Writing block');await page.getByRole('button',{name:'Extract 1 into block',exact:true}).click();await expect(page.getByLabel('Node behavior')).toHaveValue('subswarm');
  await page.getByText('Parameters and reusable block interfaces',{exact:true}).click();await page.getByLabel('Architecture parameter schema').fill(JSON.stringify({type:'object',properties:{topic:{type:'string',default:'Systems'}},required:['topic']}));
  await page.getByLabel('Architecture name').fill('Versioned '+unique());await page.getByRole('button',{name:'Save custom copy'}).click();await expect(page.getByRole('button',{name:'Save architecture',exact:true})).toBeVisible();
  const first=await page.getByLabel('Architecture name').inputValue();await page.getByLabel('Architecture name').fill(first+' revised');await page.getByRole('button',{name:'Save architecture',exact:true}).click();await expect(page.getByText('Saved version history · revision 2',{exact:true})).toBeVisible();
  await page.getByText('Saved version history · revision 2',{exact:true}).click();await page.getByRole('button',{name:'Inspect changes',exact:true}).first().click();await expect(page.locator('.workshop-panel pre')).toContainText('name');await page.getByRole('button',{name:'Restore revision 1',exact:true}).click();await expect(page.getByLabel('Architecture name')).toHaveValue(first);
});

test('live recursion tree approves child work and edits a held branch without replaying siblings',async({page})=>{
  await connect(page);const architecture=await team(page,[node('held',{break_before:true}),node('sibling')]);const run=await launch(page,architecture);await expect(page.locator('.run-title .badge')).toHaveText('waiting');
  await page.getByRole('button',{name:'Recursion tree',exact:true}).click();const held=page.locator('.tree-agent').filter({has:page.getByRole('button',{name:'held',exact:true})});await expect(held.getByText('Breakpoint before this node · before')).toBeVisible();
  await held.getByRole('button',{name:'Edit paused branch',exact:true}).click();await page.getByLabel('Paused branch instructions').fill('Use the new instructions');await page.getByRole('button',{name:'Save branch changes',exact:true}).click();await held.getByRole('button',{name:'Approve / resume',exact:true}).click();await expect(page.locator('.run-title .badge')).toHaveText('completed');
  const saved=await (await page.request.get('/api/runs/'+run.id)).json();expect(saved.agents.find((a:any)=>a.node_id==='held').instructions).toBe('Use the new instructions');expect(saved.agents.find((a:any)=>a.node_id==='sibling').messages.filter((m:any)=>m.role==='assistant'&&m.complete)).toHaveLength(1);
  await page.screenshot({path:'test-results/workshop-tree.png',fullPage:true});
});

test('dynamic fanout creates actual variable child missions with branch allowances',async({page})=>{
  await connect(page);const child=await team(page,[node('worker')]);const root=await team(page,[node('fanout',{kind:'fanout',subswarm_id:child.id,max_fanout:3,request_budget:3,token_budget:10000,tasks_path:'tasks'})]);const run=await launch(page,root,{inputs:{tasks:['Task one','Task two']}});
  await expect(page.locator('.run-title .badge')).toHaveText('completed',{timeout:20000});await page.getByRole('button',{name:'Recursion tree',exact:true}).click();await expect(page.locator('.task-tree-node')).toHaveCount(3);
  await expect(page.locator('.tree-task').filter({hasText:'Task one'})).toBeVisible();const tree=await (await page.request.get('/api/execution-control/tree/'+run.id)).json();expect(tree.runs.filter((r:any)=>r.depth===1)).toHaveLength(2);expect(tree.runs.find((r:any)=>r.id===run.id).agents[0].request_budget.used_requests).toBe(2);
});

test('shared editor merges live changes before owner publication',async({page,context})=>{
  await connect(page);const project=await (await page.request.post('/api/projects',{data:{name:'Shared designs '+unique(),root:'.',tools:[],checks:{}}})).json();expect(project.id).toBeTruthy();
  const architecture=await team(page,[node('first'),node('second')]);const session=await (await page.request.post('/api/architecture-workbench/sessions',{data:{project_id:project.id,architecture}})).json();
  const open=async(p:Page)=>{await connect(p);await p.getByRole('button',{name:'Control room',exact:true}).click();await p.getByRole('tab',{name:'Team & setup',exact:true}).click();await p.getByRole('tab',{name:'Shared designs',exact:true}).click();await p.locator('.revision-row').filter({hasText:architecture.name}).getByRole('button',{name:'Edit shared design'}).click();await expect(p.getByLabel('Architecture name')).toHaveValue(architecture.name);};
  await open(page);const peer=await context.newPage();await open(peer);await page.getByRole('button',{name:'Select agent first',exact:true}).click();await page.getByLabel('Agent instructions').fill('Peer one edited');await peer.getByRole('button',{name:'Select agent second',exact:true}).click();await peer.getByLabel('Agent instructions').fill('Peer two edited');
  await expect.poll(async()=>{const row=await (await page.request.get('/api/architecture-workbench/sessions/'+session.id)).json();return row.draft.nodes.map((n:any)=>n.instructions);}).toEqual(['Peer one edited','Peer two edited']);
  await expect(page.getByLabel('Agent instructions')).toHaveValue('Peer one edited');await expect(page.getByRole('button',{name:'Publish shared draft',exact:true})).toBeEnabled();await expect(page.getByText('Shared draft · revision 2',{exact:true})).toBeVisible();await page.getByRole('button',{name:'Publish shared draft',exact:true}).click();await expect(page.getByText('Shared architecture published to your mission library.')).toBeVisible();await peer.close();
});

test('conflicting shared edits preserve local text until explicit reload',async({page})=>{
  await connect(page);const project=await (await page.request.post('/api/projects',{data:{name:'Conflict project '+unique(),root:'.',tools:[],checks:{}}})).json();const architecture=await team(page,[node('worker')]);const session=await (await page.request.post('/api/architecture-workbench/sessions',{data:{project_id:project.id,architecture}})).json();
  await page.getByRole('button',{name:'Control room',exact:true}).click();await page.getByRole('tab',{name:'Team & setup',exact:true}).click();await page.getByRole('tab',{name:'Shared designs',exact:true}).click();await page.locator('.revision-row').filter({hasText:architecture.name}).getByRole('button',{name:'Edit shared design'}).click();await expect(page.getByLabel('Architecture name')).toHaveValue(architecture.name);
  await page.getByLabel('Agent instructions').fill('Keep my local text');const other=structuredClone(session.draft);other.nodes[0].instructions='Remote text';expect((await page.request.post('/api/architecture-workbench/sessions/'+session.id+'/edits',{data:{base_revision:0,architecture:other}})).ok()).toBeTruthy();
  await expect(page.getByRole('status')).toContainText('Conflicting edits');await expect(page.getByLabel('Agent instructions')).toHaveValue('Keep my local text');await expect(page.getByRole('button',{name:'Publish shared draft',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Export my draft',exact:true})).toBeVisible();await page.getByRole('button',{name:'Reload shared draft',exact:true}).click();await expect(page.getByLabel('Agent instructions')).toHaveValue('Remote text');
});
