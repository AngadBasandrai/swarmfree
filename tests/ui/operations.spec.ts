import {test,expect} from '@playwright/test';
test.beforeEach(async({request})=>{await request.post('/api/session',{data:{}});});

test('command center plans a campaign, accepts work and continues to the next milestone',async({page,request})=>{
 const projects=(await (await request.get('/api/snapshot')).json()).projects;
 let project=projects[0];
 if(!project){const created=await request.post('/api/projects',{data:{name:'Operations fixture',root:process.cwd(),tools:[],checks:{}}});expect(created.ok()).toBeTruthy();project=await created.json();}
 await page.goto('/');await page.getByRole('button',{name:'Command center',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Your projects, agents and devices.'})).toBeVisible();
 await page.getByLabel('Command center project').selectOption(project.id);
 await page.getByRole('tab',{name:'Campaigns',exact:true}).click();
 await page.getByLabel('Campaign name').fill('Public release campaign');
 await page.getByLabel('Milestone task 1').fill('Plan a useful desktop feature');
 await page.getByRole('button',{name:'Create campaign',exact:true}).click();
 const campaign=page.locator('.ops-record').filter({hasText:'Public release campaign'});
 await expect(campaign).toBeVisible();await campaign.getByRole('button',{name:'Launch next milestone',exact:true}).click();
 await expect(page.locator('.run-title .badge')).toHaveText('completed',{timeout:25000});
 await page.getByRole('button',{name:'Command center',exact:true}).click();await page.getByRole('tab',{name:'Campaigns',exact:true}).click();
 const record=page.locator('.ops-record').filter({hasText:'Public release campaign'});
 await expect(record.getByRole('button',{name:'Accept milestone'})).toBeEnabled({timeout:10000});
 await record.getByRole('button',{name:'Accept milestone'}).click();
 await expect(record).toContainText('Plan · accepted');
 await record.getByRole('button',{name:'Launch next milestone',exact:true}).click();
 await expect(page.locator('.run-title .badge')).toHaveText('completed',{timeout:25000});
});

test('preflight makes no inference calls and knowledge updates invalidate dependent facts',async({page,request})=>{
 const projects=(await (await request.get('/api/snapshot')).json()).projects;const project=projects[0];
 await page.goto('/');await page.getByRole('button',{name:'Command center',exact:true}).click();
 await page.getByLabel('Command center project').selectOption(project.id);
 await page.getByRole('tab',{name:'Preflight',exact:true}).click();await page.getByRole('button',{name:'Simulate architectures'}).click();
 await expect(page.getByRole('table')).toContainText('Unknown');
 await page.getByRole('tab',{name:'Knowledge graph'}).click();
 await page.getByLabel('Knowledge title').fill('API contract');await page.getByLabel('Knowledge content').fill('Version one');await page.getByRole('button',{name:'Add knowledge'}).click();
 const api=page.locator('.knowledge-node').filter({has:page.getByText('API contract',{exact:true})});await expect(api).toBeVisible();
 const rows=await (await request.get('/api/ops/knowledge/'+project.id)).json();const first=rows.find((r:{title:string})=>r.title==='API contract');
 await page.getByLabel('Knowledge title').fill('Dependent client');await page.getByLabel('Knowledge content').fill('Uses version one');await page.getByLabel('Dependency IDs, comma separated').fill(first.id);await page.getByRole('button',{name:'Add knowledge'}).click();
 await expect(page.locator('.knowledge-node').filter({has:page.getByText('Dependent client',{exact:true})})).toBeVisible();
 await api.getByRole('button',{name:'Edit knowledge'}).click();await page.getByLabel('Knowledge content').fill('Version two');await page.getByRole('button',{name:'Save new revision'}).click();
 await expect(page.locator('.knowledge-node').filter({hasText:'Dependent client'})).toContainText('Dependency changed');
 await page.getByRole('tab',{name:'Notebooks',exact:true}).click();await page.getByLabel('Notebook title').fill('Mean calculation');await page.getByRole('button',{name:'Save notebook',exact:true}).click();
 const book=page.locator('.ops-record').filter({hasText:'Mean calculation'});await expect(book).toBeVisible();const event=page.waitForEvent('download');await book.getByRole('button',{name:'Export standard notebook'}).click();expect((await event).suggestedFilename()).toMatch(/\.ipynb$/);
});

test('microphone stays off until clicked and invalid audio is rejected',async({page})=>{
 let requests=0;page.on('request',r=>{if(r.url().includes('/voice/chunk'))requests++;});
 await page.goto('/');await page.getByRole('button',{name:'Command center',exact:true}).click();await page.getByRole('tab',{name:'Live voice',exact:true}).click();
 await expect(page.getByRole('button',{name:'Start microphone'})).toBeDisabled();await page.getByLabel('Voice transcript').fill('A reviewed instruction');
 await expect(page.getByRole('button',{name:'Send reviewed transcript'})).toBeDisabled();expect(requests).toBe(0);
 await page.getByRole('tab',{name:'Compatibility',exact:true}).click();await expect(page.getByRole('button',{name:'Run live compatibility pack'})).toBeDisabled();
});

test('microphone setup failure releases its simulated stream',async({page})=>{
 await page.route('**/api/ops',async route=>{const response=await route.fetch();const body=await response.json();body.voice={trusted:true,argv:['fixture']};await route.fulfill({response,json:body});});
 await page.addInitScript(()=>{
  (window as any).__stopped=0;
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>({getTracks:()=>[{stop:()=>{(window as any).__stopped++;}}]})});
  (window as any).AudioContext=class{constructor(){throw new Error('Simulated audio setup failure');}};
 });
 await page.goto('/');await page.getByRole('button',{name:'Command center',exact:true}).click();await page.getByRole('tab',{name:'Live voice',exact:true}).click();
 await page.getByRole('button',{name:'Start microphone'}).click();
 await expect(page.getByRole('status').filter({hasText:'Simulated audio setup failure'})).toBeVisible();
 await expect.poll(()=>page.evaluate(()=>(window as any).__stopped)).toBe(1);
 await expect(page.getByRole('button',{name:'Stop microphone'})).toBeDisabled();
});

test('queued transcription finishes before a transcript can be reviewed and sent',async({page,request})=>{
 const created=await request.post('/api/runs',{data:{task:'Voice fixture',architecture_id:'baseline',mode:'demo'}});expect(created.ok()).toBeTruthy();const mission=await created.json();
 let release:()=>void=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});let chunk=false;
 await page.route('**/api/ops',async route=>{const response=await route.fetch();const body=await response.json();body.voice={trusted:true,argv:['fixture']};await route.fulfill({response,json:body});});
 await page.route('**/api/ops/voice/chunk',async route=>{chunk=true;await gate;await route.fulfill({json:{text:'Simulated local transcript'}});});
 await page.addInitScript(()=>{
  const node=()=>({connect:()=>{},disconnect:()=>{}});
  Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>({getTracks:()=>[{stop:()=>{}}]})});
  (window as any).AudioContext=class{
   sampleRate=16000;destination=node();async resume(){}async close(){}createMediaStreamSource(){return node();}createGain(){return {...node(),gain:{value:1}};}
   createScriptProcessor(){const processor={...node(),onaudioprocess:null as any};setTimeout(()=>processor.onaudioprocess?.({inputBuffer:{getChannelData:()=>new Float32Array(1600)}}),50);return processor;}
  };
 });
 await page.goto('/');await page.getByRole('button',{name:'Command center',exact:true}).click();await page.getByRole('tab',{name:'Live voice',exact:true}).click();
 await page.getByLabel('Voice mission').selectOption(mission.id);await expect(page.getByLabel('Voice agent')).not.toHaveValue('');
 await page.getByLabel('Voice transcript').fill('An already reviewed sentence');await page.getByRole('button',{name:'Start microphone'}).click();
 await page.waitForTimeout(150);await page.getByRole('button',{name:'Stop microphone'}).click();
 await expect.poll(()=>chunk).toBe(true);await expect(page.getByRole('button',{name:'Send reviewed transcript'})).toBeDisabled();
 release();await expect(page.getByLabel('Voice transcript')).toHaveValue(/Simulated local transcript/);await expect(page.getByRole('button',{name:'Send reviewed transcript'})).toBeEnabled();
});
