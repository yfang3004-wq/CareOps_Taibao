const {chromium}=require('/Users/gaoyuxiang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),path=require('path');
const base=path.resolve(__dirname,'../..');const report=path.join(base,'reports');fs.mkdirSync(report,{recursive:true});
const privateFile=process.env.CAREOPS_TEST_CREDENTIALS_FILE;if(!privateFile)throw Error('Set CAREOPS_TEST_CREDENTIALS_FILE to a private synthetic credential file.');const raw=fs.readFileSync(privateFile,'utf8');let cred;try{cred=JSON.parse(raw);}catch{cred={};for(const m of raw.matchAll(/账号：([^\n]+)\n密码：([^\n]+)/g))cred[m[1]]=m[2];}const checks=[],errors=[],requests=[];let clicks=0;
const check=(name,ok=true)=>{if(!ok)throw Error(name);checks.push({name,status:'PASS'});};
async function click(page,selector){clicks++;await page.locator(selector).click();}
async function submit(page){await click(page,'#dialog button[type=submit]');await page.waitForFunction(()=>!document.getElementById('dialog').open);}
(async()=>{let browser;try{
 browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const make=async user=>{const ctx=await browser.newContext({viewport:{width:1280,height:900}});await ctx.route('**/*',r=>{const u=new URL(r.request().url());u.hostname==='127.0.0.1'?r.continue():r.abort()});let page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/api/commands'))requests.push(JSON.parse(r.postData()||'{}'));});await page.goto((process.env.CAREOPS_TEST_ORIGIN||'http://127.0.0.1:18766'));await page.locator('[name=username]').fill(user);await page.locator('[name=password]').fill(cred.password||cred[user]);await page.locator('#login-form button[type=submit]').click();await page.locator('h1').filter({hasText:'待维修'}).waitFor();await page.locator('[data-action=maint-create]').waitFor();return page;};
 const exec=await make('repair2'),sup=await make('checker2');
 check('独立导航仅待维修/楼层地图/维修记录',JSON.stringify(await exec.locator('.sidebar nav button').allTextContents())===JSON.stringify(['待维修','楼层地图','维修记录']));
 clicks=0;await click(exec,'[data-action=maint-create]');check('新建没有长者关联可见字段',await exec.locator('#flow-form [name=resident_id]').getAttribute('type')==='hidden');
 await exec.locator('#flow-form [name=location]').selectOption('2F公共走廊');await exec.locator('#flow-form [name=description]').fill('合成灯具维修浏览器完整闭环');await submit(exec);await exec.locator('[data-type=start_task]').waitFor();
 await click(exec,'[data-type=start_task]');await exec.locator('[data-type=submit_task]').waitFor();
 await click(exec,'[data-type=submit_task]');await exec.locator('#command-form [name=result]').fill('已更换合成灯具并复测');await exec.locator('#command-form [name=evidence_value]').fill('合成现场核验，无真实照片');await submit(exec);await exec.locator('span.chip').filter({hasText:'修理结果待检查'}).waitFor();
 check('提交结果未整单验收',!(await exec.locator('[data-type=submit_workflow]').count()));
 await click(sup,'[data-action=refresh]');await click(sup,'[data-action=maint-open]');await click(sup,'[data-type=review_task]');await sup.locator('#command-form [name=reason]').fill('独立合成检查通过');await submit(sup);
 check('主管不能代整单提交',!(await sup.locator('[data-type=submit_workflow]').count()));
 await click(exec,'[data-action=refresh]');await exec.locator('[data-type=submit_workflow]').waitFor();await click(exec,'[data-type=submit_workflow]');await exec.locator('span.chip').filter({hasText:'待整单验收'}).waitFor();
 await click(sup,'[data-action=refresh]');await click(sup,'[data-type=review_workflow]');check('通过时退回task字段disabled',await sup.locator('#command-form [name=task_id]').isDisabled());await sup.locator('#command-form [name=reason]').fill('独立合成整单验收通过');await submit(sup);await sup.locator('span.chip').filter({hasText:'已验收'}).waitFor();
 const final=requests.filter(x=>x.type==='review_workflow').at(-1);check('最终通过payload无task_id',!Object.hasOwn(final.data,'task_id'));
 check('未知实际时间未默认提交时间',(await sup.locator('#content').innerText()).includes('未知／未登记'));
 const chainClicks=clicks;
 await sup.locator('#toast').waitFor({state:'hidden',timeout:8000});await sup.screenshot({path:path.join(report,'MAINTENANCE_CLOSED_DESKTOP.png'),fullPage:true});
 await click(exec,'[data-page=maint-locations]');await exec.getByRole('heading',{name:'已授权位置列表'}).waitFor();check('无图位置列表诚实提示',(await exec.locator('#content').innerText()).includes('暂无已确认平面图'));
 await exec.screenshot({path:path.join(report,'MAINTENANCE_LOCATIONS_DESKTOP.png'),fullPage:true});
 await exec.setViewportSize({width:390,height:844});await click(exec,'[data-page=maint-history]');await exec.getByRole('heading',{name:'维修历史与当前记录'}).waitFor();await exec.screenshot({path:path.join(report,'MAINTENANCE_HISTORY_MOBILE.png'),fullPage:true});
 check('移动端页面无横向整页溢出',await exec.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
 check('浏览器无JS异常',errors.length===0);
 fs.writeFileSync(path.join(report,'BROWSER_RESULTS.json'),JSON.stringify({status:'PASS',checks,errors,normal_chain_button_clicks:chainClicks,click_scope:'从两账户已登录且定位待维修首页开始；含新建/保存、切主管刷新/打开、两阶段检查、刷新、整单提交与验收；不含登录、输入、选项、现场工作；非真实员工节时测量',commands:requests.map(x=>({type:x.type,fields:Object.keys(x.data)}))},null,2));
 console.log('browser PASS',checks.length,'chain button clicks',chainClicks);
 }catch(e){fs.writeFileSync(path.join(report,'BROWSER_FAILURE.json'),JSON.stringify({status:'FAIL',message:e.message,checks,errors},null,2));console.error(e);process.exitCode=1;}finally{if(browser)await browser.close();}})();
