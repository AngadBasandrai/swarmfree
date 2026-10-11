import { test, expect } from '@playwright/test';

test('review an automation trigger, inspect exact context, and replay one stage',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await page.getByRole('button',{name:'Control room',exact:true}).click();
  await page.getByLabel('Automation name').fill('Reviewed daily helper');
  await page.getByLabel('Automation mission').fill('Produce a small checklist');
  await page.getByRole('button',{name:'Save automation',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Reviewed daily helper',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Trigger now',exact:true}).click();
  await page.locator('summary').filter({hasText:'Reviewed daily helper · pending'}).click();
  await expect(page.getByRole('button',{name:'Approve mission',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Approve mission',exact:true}).click();
  await expect(page.locator('.run-title .badge')).toHaveText('completed',{timeout:15000});
  await expect(page.locator('.mission-budget')).toHaveCount(1);
  await expect(page.locator('.mission-budget')).toContainText('1/100 requests');
  await page.getByRole('button',{name:'Context',exact:true}).click();
  await page.route('**/api/agents/*/context',async route=>{await new Promise(resolve=>setTimeout(resolve,400));await route.continue();});
  await page.getByRole('button',{name:'Preview next model context',exact:true}).click();
  await expect(page.getByLabel('Context usage')).toBeVisible();
  await page.getByLabel('Handoff summary').fill('Decision: keep the checklist short. TODO: verify sources.');
  await expect(page.getByText('Exact portable messages for the next request',{exact:true})).toBeVisible();
  await expect(page.getByLabel('Handoff summary')).toHaveValue('Decision: keep the checklist short. TODO: verify sources.');
  await page.getByRole('button',{name:'Save handoff summary',exact:true}).click();
  await page.getByRole('button',{name:'Debugger',exact:true}).click();
  await page.locator('.timeline-request').first().click();
  await expect(page.locator('.debugger-grid article pre')).toContainText('Produce a small checklist');
  await page.screenshot({path:'test-results/workbench-debugger.png',fullPage:true});
  await page.getByRole('button',{name:'Rerun in a new branch',exact:true}).click();
  await expect(page.locator('.run-title .badge')).toHaveText('completed',{timeout:15000});
  await expect(page.locator('.dock-agent')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('preview permissions before importing a portable swarm package',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'Control room',exact:true}).click();
  await page.getByRole('tab',{name:'Swarm packages',exact:true}).click();
  const value={name:'Imported research crew',version:'1.2.0',architecture:{id:'example',name:'Imported research crew',nodes:[{id:'researcher',name:'Researcher',role:'Review evidence',tools:['research.fetch']}],edges:[]},requested_permissions:['research.fetch'],evaluations:[{task:'Evaluate a claim',rubric:'Cite actual source passages'}]};
  await page.getByLabel('Package JSON',{exact:true}).fill(JSON.stringify(value));
  await page.getByRole('button',{name:'Preview requested permissions',exact:true}).click();
  await expect(page.getByText('Permissions: research.fetch',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Import reviewed package',exact:true}).click();
  await expect(page.locator('summary').filter({hasText:'Imported research crew · 1.2.0'})).toBeVisible();
  await page.getByRole('button',{name:'Architectures',exact:true}).click();
  await expect(page.getByLabel('Architecture template')).toContainText('Imported research crew');
});

test('configure bounded mission limits and attach a text document',async({page})=>{
  await page.goto('/');
  await page.getByText('Budgets, adaptive teams and attachments',{exact:true}).click();
  await page.getByLabel('Request limit',{exact:true}).fill('1');
  await page.getByLabel('Adaptive team policy').selectOption('resources');
  await expect(page.getByText(/The selected graph is replaced/)).toBeVisible();
  await page.getByLabel('Adaptive team policy').selectOption('off');
  await page.getByLabel('Mission attachment').setInputFiles({name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('Owner-provided project notes.')});
  await expect(page.getByText('notes.txt',{exact:true})).toBeVisible();
  await page.getByLabel('THE MISSION').fill('Use these notes for a checklist');
  await page.getByLabel('Execution mode').selectOption('demo');
  await page.getByRole('button',{name:'Run offline demo',exact:true}).click();
  await expect(page.locator('.run-title .badge')).toHaveText('budget_exceeded',{timeout:15000});
  await page.getByRole('button',{name:'Debugger',exact:true}).click();
  await page.locator('.timeline-request').first().click();
  await expect(page.locator('.debugger-grid article pre')).toContainText('Owner-provided project notes.');
});

test('control-room forms remain usable on a small screen without desktop permissions',async({page})=>{
  await page.setViewportSize({width:390,height:844});await page.goto('/');
  await page.getByRole('button',{name:'Control room',exact:true}).click();
  await page.getByRole('tab',{name:'Hardware & media',exact:true}).click();
  await expect(page.getByLabel('Idle unload seconds')).toBeVisible();
  await page.getByLabel('Idle unload seconds').fill('60');
  await page.getByRole('button',{name:'Save idle unloading',exact:true}).click();
  await expect(page.getByLabel('PDF render command')).toBeVisible();
  await page.getByRole('tab',{name:'Internet links & workers',exact:true}).click();
  await expect(page.getByLabel('Relay origin')).toBeVisible();
  await page.screenshot({path:'test-results/workbench-control-mobile.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});
