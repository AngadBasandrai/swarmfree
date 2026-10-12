import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

async function tools(page:any) {
  await page.getByRole('button',{name:'Control room',exact:true}).click();
  await page.getByRole('tab',{name:'Execution tools',exact:true}).click();
}

test('task routing controls and typed output contracts are saved',async({page})=>{
  await page.goto('/');
  await page.getByText('Task-aware routing and native tools',{exact:true}).click();
  await page.getByLabel('Task routing policy').selectOption('quality');
  await page.getByLabel('Use native function calling for granted tools').check();
  await page.getByRole('button',{name:'Architectures',exact:true}).click();
  await page.getByText('Contracts, conditions and messages',{exact:true}).click();
  await page.getByText('Typed handoff and validation',{exact:true}).click();
  await page.getByLabel('Handoff JSON schema').fill(JSON.stringify({type:'object',required:['summary'],properties:{summary:{type:'string'}}}));
  await page.getByLabel('Agent validation checks').fill(JSON.stringify([{kind:'contains',value:'summary'}]));
  await page.getByRole('button',{name:'Apply output contract',exact:true}).click();
  await page.getByLabel('Architecture name').fill('Typed output fixture');
  await page.getByRole('button',{name:'Save custom copy',exact:true}).click();
  await expect(page.getByLabel('Architecture template')).toContainText('Typed output fixture');
  const response=await page.request.get('/api/snapshot');const data=await response.json();
  const architecture=data.architectures.find((a:any)=>a.name==='Typed output fixture');
  expect(architecture.nodes[0].output_schema.required).toEqual(['summary']);
});

test('catalog search, execution panels and narrow screen controls are usable',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');await tools(page);
  await expect(page.getByRole('heading',{name:'Free-model catalog',exact:true})).toBeVisible();
  await page.getByLabel('Search model catalog').fill('embedding');
  await expect(page.locator('summary').filter({hasText:'Qwen3 Embedding'})).toHaveCount(1);
  await expect(page.locator('summary').filter({hasText:'Qwen3 14B'})).toHaveCount(0);
  await page.getByRole('tab',{name:'Semantic search',exact:true}).click();
  await expect(page.getByLabel('Embedding model')).toHaveValue('qwen3-embedding:0.6b');
  await page.getByRole('tab',{name:'Browser agents',exact:true}).click();
  await expect(page.getByRole('button',{name:'Install headless Chromium',exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'Execution workers',exact:true}).click();
  await expect(page.getByText(/--execution-image/)).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('tab',{name:'Encrypted recovery',exact:true}).click();
  await expect(page.getByLabel('Backup passphrase',{exact:false})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2)).toBeTruthy();
  await page.screenshot({path:'test-results/workbench-tools-mobile.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('automated evaluations recommend the smallest passing team',async({page})=>{
  await page.goto('/');await tools(page);await page.getByRole('tab',{name:'Evaluations',exact:true}).click();
  await page.getByLabel('Evaluation name').fill('Repeatable comparison');
  await page.getByRole('button',{name:'Run evaluation suite',exact:true}).click();
  await expect(page.getByText('Recommended: Small team · 1 agents',{exact:false})).toBeVisible({timeout:20000});
  await expect(page.locator('.studio-row').filter({hasText:'Small team: passed'})).toHaveCount(1);
  await page.screenshot({path:'test-results/workbench-evaluations.png',fullPage:true});
});

test('a real agent clarification appears in the inbox and resumes its mission',async({page})=>{
  const directory=await mkdtemp(join(tmpdir(),'swarm-question-ui-'));
  let profileId='';
  const provider=createServer(async(req,res)=>{
    const chunks:Buffer[]=[];for await(const c of req)chunks.push(c);
    const body=JSON.parse(Buffer.concat(chunks).toString());
    const answered=body.messages.some((m:any)=>m.content?.includes('Tool result (data)'));
    const content=answered?'Owner selected JSON. Clarification complete.':JSON.stringify({tool:'user.ask',arguments:{question:'Which output format should this agent use?',options:['JSON','Text']}});
    res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({choices:[{index:0,delta:{content},finish_reason:'stop'}],usage:{prompt_tokens:30,completion_tokens:20}})+'\n\ndata: [DONE]\n\n');
  });
  await new Promise<void>(resolve=>provider.listen(0,'127.0.0.1',resolve));
  try{
    await page.goto('/');await expect(page.getByLabel('THE MISSION')).toBeVisible();
    const project=await (await page.request.post('/api/projects',{data:{name:'Question fixture',root:directory,tools:['user.ask'],checks:{}}})).json();
    const profile=await (await page.request.post('/api/profiles',{data:{provider:'compatible',label:'Owned fixture',model:'fixture',base_url:'http://127.0.0.1:'+(provider.address() as any).port+'/v1',input_usd_per_million:0,output_usd_per_million:0}})).json();
    profileId=profile.id;
    const run=await (await page.request.post('/api/runs',{data:{task:'Clarification UI mission',architecture_id:'baseline',project_id:project.id,tools_enabled:true,free_only:false,route:{kind:'profile',profile_id:profile.id}}})).json();
    await tools(page);await page.getByRole('tab',{name:/Agent questions/}).click();
    await expect(page.getByText('Which output format should this agent use? · pending',{exact:false})).toBeVisible({timeout:10000});
    await page.getByRole('button',{name:'JSON',exact:true}).click();
    await page.getByRole('button',{name:'Answer agent',exact:true}).click();
    await page.getByRole('button',{name:'Open requesting mission',exact:true}).click();
    await expect(page.locator('.run-title .badge')).toHaveText('completed',{timeout:10000});
    await expect(page.locator('.mission-budget')).toContainText('2/100 requests');
    const saved=await (await page.request.get('/api/runs/'+run.id)).json();
    expect(saved.usage.reported_requests).toBe(2);
  }finally{if(profileId)await page.request.delete('/api/profiles/'+profileId);await new Promise<void>(resolve=>provider.close(()=>resolve()));await rm(directory,{recursive:true,force:true});}
});

test('encrypted recovery previews paths before restoring into a separate directory',async({page})=>{
  const directory=await mkdtemp(join(tmpdir(),'swarm-recovery-ui-'));
  try{
    await page.goto('/');await tools(page);await page.getByRole('tab',{name:'Encrypted recovery',exact:true}).click();
    await page.getByLabel('Backup passphrase',{exact:false}).fill('UI recovery passphrase');
    await page.getByRole('button',{name:'Create encrypted backup',exact:true}).click();
    await page.locator('summary').filter({hasText:/files.*MiB/}).last().click();
    await expect(page.getByRole('button',{name:'Save encrypted backup elsewhere',exact:true})).toBeVisible({timeout:10000});
    const next=await (await page.request.get('/api/next')).json();const path=next.backups.at(-1).path;
    await page.getByLabel('Restore backup path').fill(path);
    await page.getByLabel('Restore passphrase',{exact:true}).fill('UI recovery passphrase');
    await page.getByLabel('Restore target directory').fill(join(directory,'restored'));
    await page.getByRole('button',{name:'Preview restore paths',exact:true}).click();
    await expect(page.getByRole('button',{name:'Restore reviewed snapshot',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Restore reviewed snapshot',exact:true}).click();
    await expect(page.locator('pre').filter({hasText:'"restored": true'})).toBeVisible();
  }finally{await rm(directory,{recursive:true,force:true});}
});
