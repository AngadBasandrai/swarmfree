import {test,expect} from '@playwright/test';
async function desk(page:any){await page.goto('/');await page.getByRole('button',{name:'Control room',exact:true}).click();await page.getByRole('tab',{name:'Team & setup',exact:true}).click();}
test('guided setup and validated architecture creation work',async({page})=>{
  await desk(page);await expect(page.getByRole('heading',{name:'Set up this device'})).toBeVisible();
  await page.getByRole('tab',{name:'Architecture copilot',exact:true}).click();
  await page.getByLabel('Team description').fill('Planner, researcher, reviewer and writer in parallel');
  await page.getByRole('button',{name:'Design offline preview'}).click();
  await expect(page.getByLabel('Architecture JSON')).toContainText('planner');
  await expect(page.locator('.react-flow__node')).toHaveCount(4);
  await expect(page.locator('.react-flow')).toBeVisible();
  await page.screenshot({path:'test-results/team-copilot.png',fullPage:true});
  await page.getByRole('button',{name:'Save new architecture'}).click();
  await expect(page.getByText('Saved as a new architecture. Select it when creating a mission.')).toBeVisible();
  const data=await (await page.request.get('/api/snapshot')).json();expect(data.architectures.some((a:any)=>a.id.startsWith('custom_')&&a.nodes.length===4)).toBeTruthy();
});
test('all team panels render without page errors',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await desk(page);
  for(const [tab,heading] of [['Agent channels','Bounded agent conversations'],['Team rooms','Share selected projects'],['Data rules','Choose where project data can go'],['Result cache','Reuse validated pure responses'],['Failure lab','Controlled failure simulations'],['External agents','Connect A2A agents'],['Sharding lab','Experimental Exo sharding lab'],['Community registry','Signed community packages'],['Public releases','Downloadable public releases']]){
    await page.getByRole('tab',{name:tab,exact:true}).click();await expect(page.getByRole('heading',{name:heading,exact:true})).toBeVisible();
  }
  expect(errors).toEqual([]);
});
test('failure lab runs an owned disconnected-stream fixture',async({page})=>{
  await desk(page);await page.getByRole('tab',{name:'Failure lab',exact:true}).click();
  for(const box of await page.getByRole('checkbox').all())await box.uncheck();
  await page.getByRole('checkbox',{name:'Interrupted output stays a draft; resume makes a fresh response'}).check();
  await page.getByRole('button',{name:'Run selected simulations'}).click();
  await expect(page.locator('pre').filter({hasText:'stream_disconnect'})).toContainText('"passed": true',{timeout:20000});
});
