#!/usr/bin/env python3
"""UI harness: DOM operations plus a Python HTTP adapter. No browser navigation.
NOT a substitute for native browser origin/cookie/download acceptance.
Real Uvicorn/SQLite HTTP requests; browser fetch/cookies/blob-download adapted.
Uses installed Playwright/Chromium; does not install browsers.
"""
import json
import base64
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
import httpx

ROOT=Path(__file__).resolve().parents[1]
REPORT=Path(os.getenv('CAREOPS_TEST_REPORT_DIR',str(ROOT/'outputs/local-browser'))).resolve();REPORT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[];external=[];processes=[];contexts=[]; proxies={}; bases={}

def mount(page,url):
    bases[page]=url
    if page not in proxies:
        proxies[page]=httpx.Client(timeout=10)
        def bridge(options):
            target=options["url"]
            if not target.startswith("/"):raise ValueError("Harness only allows relative local paths")
            headers=options.get("headers",{})
            if options.get("method","GET")!="GET":headers["Origin"]=bases[page]
            r=proxies[page].request(options.get("method","GET"),bases[page]+target,headers=headers,content=options.get("body"))
            return {"status":r.status_code,"text":r.text,"base64":base64.b64encode(r.content).decode(),"headers":dict(r.headers)}
        page.expose_function("__careopsTestHTTP",bridge)
    shim="""window.fetch=async (url,opts={})=>{const r=await window.__careopsTestHTTP({url:String(url),method:opts.method||'GET',headers:opts.headers||{},body:opts.body});if(window.__loseNextCommandResponse&&String(url)==='/api/commands'&&r.status===200){window.__loseNextCommandResponse=false;throw Error('SIMULATED_RESPONSE_LOSS_AFTER_SERVER_COMMIT');}return {status:r.status,ok:r.status>=200&&r.status<300,json:async()=>JSON.parse(r.text),text:async()=>r.text,arrayBuffer:async()=>Uint8Array.from(atob(r.base64),c=>c.charCodeAt(0)).buffer,headers:new Headers(r.headers)};};
    if(!crypto.randomUUID)crypto.randomUUID=()=>'10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));
    if(!window.__careopsHarnessStorage)window.__careopsHarnessStorage=new Map();Object.defineProperty(window,'sessionStorage',{configurable:true,value:{getItem:k=>window.__careopsHarnessStorage.get(k)||null,setItem:(k,v)=>window.__careopsHarnessStorage.set(k,v),removeItem:k=>window.__careopsHarnessStorage.delete(k)}});
    window.__downloadText=null;window.__download=null;const blobs=new Map();URL.createObjectURL=b=>{const k='blob:harness-'+Math.random();blobs.set(k,b);return k;};URL.revokeObjectURL=k=>blobs.delete(k);HTMLAnchorElement.prototype.click=function(){const b=blobs.get(this.getAttribute('href'));const name=this.download;if(b){b.text().then(t=>window.__downloadText=t);b.arrayBuffer().then(buf=>{let s='';const arr=new Uint8Array(buf);for(let i=0;i<arr.length;i+=8192)s+=String.fromCharCode(...arr.subarray(i,i+8192));window.__download={name,base64:btoa(s)};});}};
    """
    html=(ROOT/"web/index.html").read_text()
    html=html.replace('<link rel="stylesheet" href="/assets/app.css">','<style>'+(ROOT/"web/app.css").read_text()+'</style>')
    for name in ['intents.js','app.js','tabular.js','m3.js']:html=html.replace(f'<script src="/assets/{name}" defer></script>','')
    scripts='\n'.join((ROOT/'web'/name).read_text() for name in ['intents.js','app.js','tabular.js','m3.js'])
    html=html.replace('</body>','<script>(function(){'+shim+scripts+'})();</script></body>')
    page.set_content(html,wait_until="load")

def check(name,condition=True):
    if not condition:raise AssertionError(name)
    checks.append({'name':name,'status':'PASS'})
def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def credentials(path):
    text=path.read_text();return dict(re.findall(r'账号：([^\n]+)\n密码：([^\n]+)',text))
def start_server(folder,port,demo=True,init=True):
    env={**os.environ,'CAREOPS_DATA_DIR':str(folder),'CAREOPS_ORIGIN':f'http://127.0.0.1:{port}'}
    if init:
        r=subprocess.run([sys.executable,'-m','careops.cli','init',*(['--demo'] if demo else [])],cwd=ROOT,env=env,text=True,capture_output=True,timeout=20)
        if r.returncode:raise RuntimeError(r.stderr+r.stdout)
    log=(folder/'server-test.log').open('a')
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','careops.app:create_app','--factory','--host','127.0.0.1','--port',str(port),'--no-access-log','--log-level','error'],cwd=ROOT,env=env,stdout=log,stderr=log)
    processes.append((proc,log))
    url=f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            if urllib.request.urlopen(url+'/healthz',timeout=1).status==200:return proc,url
        except Exception:time.sleep(.1)
    raise RuntimeError('测试服务未启动：'+(folder/'server-test.log').read_text())
def stop(proc):
    proc.terminate()
    try:proc.wait(timeout=8)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
def login(page,url,name,password):
    mount(page,url)
    if page.locator('#login-form').count():
        page.locator('[name=username]').fill(name);page.locator('[name=password]').fill(password)
        page.get_by_role('button',name='登录',exact=True).click()
    page.locator('h1').filter(has_text='今日工作台').wait_for()
    page.locator('.sidebar button[data-page=flows]').click()
    page.locator('h1').filter(has_text='流程工作台').wait_for()
    page.locator('.stats').wait_for()
def goto_flow(page,title):
    page.locator('.sidebar button[data-page=flows]').click()
    page.get_by_role('button',name=title,exact=True).click()
    page.locator('.task').first.wait_for()
def refresh(page):
    page.get_by_role('button',name='刷新当前状态',exact=True).click()
    page.locator('.task').first.wait_for()
def modal_submit(page):
    page.locator('#dialog button[type=submit]').click()
    page.wait_for_function('!document.getElementById("dialog").open',timeout=10000)
def new_flow(page,kind,title):
    page.locator('.sidebar button[data-page=flows]').click()
    page.locator(f'[data-action="new-flow"][data-kind="{kind}"]').click()
    page.locator('#flow-form [name=title]').fill(title)
    page.locator('#flow-form [name=location]').fill('合成试用区走廊')
    page.locator('#flow-form [name=description]').fill('全部内容为软件测试合成事实，不用于实际处置。')
    page.locator('#flow-form [name=due_at]').fill('2099-10-05 18:00')
    if kind=='incident':
        page.locator('#flow-form [name=resident_id]').select_option('R001')
        page.locator('#flow-form [name=occurred_at]').fill('2026-10-05 08:00')
    if kind=='rectification':page.locator('#flow-form [name=inspection_ref]').fill('SYNTHETIC-INSPECTION')
    modal_submit(page);page.locator('.task').first.wait_for()
def finish_task(page,boss,title,n):
    refresh(page)
    card=page.locator('.task').nth(n)
    card.get_by_role('button',name='接手并开始',exact=True).click()
    page.locator('.task').nth(n).locator('[data-type=submit_task]').wait_for()
    card=page.locator('.task').nth(n)
    if card.get_by_role('button',name='登记结果与依据',exact=True).count():
        card.get_by_role('button',name='登记结果与依据',exact=True).click()
        page.locator('#command-form [name=result]').fill('合成执行完成，由另一账户复核。')
        page.locator('#command-form [name=evidence_value]').fill('合成核验说明，仅供测试')
        modal_submit(page)
    else:
        card.get_by_role('button',name='添加依据',exact=True).click()
        page.locator('#command-form [name=value]').fill('合成核验说明，仅供测试')
        modal_submit(page)
        page.locator('.task').nth(n).get_by_role('button',name='填写结果并提交',exact=True).click()
        page.locator('#command-form [name=result]').fill('合成执行完成，由另一账户复核。')
        modal_submit(page)
    goto_flow(boss,title)
    boss.locator('.task').nth(n).get_by_role('button',name='任务复核',exact=True).click()
    boss.locator('#command-form [name=reason]').fill('独立账户核对合成证据')
    modal_submit(boss)
    check(title+' / task '+str(n+1)+' reviewed',boss.locator('.task').nth(n).locator('.chip').first.inner_text()=='已关闭')
def finish_flow(page,boss,title,kind):
    refresh(page);page.get_by_role('button',name='提交最终复核',exact=True).click()
    if kind=='incident':
        page.locator('#command-form [name=harm_source]').fill('SYNTHETIC-PROFESSIONAL-RECORD')
        modal_submit(page)
    page.locator('section.panel').first.locator('.chip').first.filter(has_text='待最终复核').wait_for()
    goto_flow(boss,title);boss.get_by_role('button',name='最终复核',exact=True).click()
    boss.locator('#command-form [name=reason]').fill('独立复核通过，不构成真实机构批准')
    modal_submit(boss)
    check(title+' / final close',boss.locator('section.panel').first.locator('.chip').first.inner_text()=='已关闭')

try:
 with tempfile.TemporaryDirectory(prefix='careops-v04-browser-') as temp:
    data=Path(temp)/'main';data.mkdir();port=free_port();proc,url=start_server(data,port)
    creds=credentials(data/'INITIAL_CREDENTIALS_DEMO-01.txt')
    with sync_playwright() as p:
        executable=os.getenv('CHROMIUM_PATH','/usr/bin/chromium')
        browser=p.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
        def page_for(viewport=None):
            ctx=browser.new_context(viewport=viewport or {'width':1440,'height':1000});contexts.append(ctx)
            page=ctx.new_page();page.set_default_timeout(10000);page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda req:external.append(req.url) if not req.url.startswith('http://127.0.0.1:') and not req.url.startswith('blob:') else None)
            return page
        admin=page_for();boss=page_for();care=page_for()
        mount(admin,url);admin.locator('#login-form').wait_for();admin.screenshot(path=str(REPORT/'v04-login.png'),full_page=True)
        check('Login screen has no default password',not admin.locator('[name=password]').input_value())
        login(admin,url,'admin',creds['admin']);login(boss,url,'supervisor',creds['supervisor']);login(care,url,'caregiver',creds['caregiver'])
        check('Three independent accounts login')
        admin.screenshot(path=str(REPORT/'v04-workspace.png'),full_page=True)
        check('Baseline eleven demo flows visible',admin.locator('.table-wrap tbody tr').count()==11)
        # Handover confirms receipt without completing its task.
        title='浏览器新事件 04';new_flow(admin,'incident',title);check('Incident created from real form')
        admin.locator('.task').first.get_by_role('button',name='建立交接',exact=True).click()
        care_option=admin.locator('[name=receiver_user_id] option').filter(has_text='照护员工').get_attribute('value')
        admin.locator('[name=receiver_user_id]').select_option(care_option)
        admin.locator('[name=observation]').fill('合成现状');admin.locator('[name=request]').fill('合成接续事项')
        modal_submit(admin);goto_flow(care,title);care.get_by_role('button',name='确认接收',exact=True).click()
        care.get_by_text('已接收',exact=True).wait_for();refresh(admin)
        check('Handover acknowledged but task remains pending',admin.locator('.task').first.locator('.chip').first.inner_text()=='待处理')
        check('Caregiver has no approval buttons',care.get_by_role('button',name='任务复核',exact=True).count()==0)
        for n in range(2):finish_task(admin,boss,title,n)
        finish_flow(admin,boss,title,'incident')
        # Maintenance: server validation keeps a failed form open.
        title2='浏览器新维修 04';new_flow(admin,'maintenance',title2);check('Maintenance created from real form')
        admin.locator('.task').first.get_by_role('button',name='接手并开始',exact=True).click()
        admin.locator('.task').first.get_by_role('button',name='登记结果与依据',exact=True).click()
        admin.locator('[name=result]').fill('先故意遗漏依据')
        admin.locator('#dialog button[type=submit]').click()
        check('Missing evidence blocked without losing input',not admin.locator('#command-form').evaluate('(f)=>f.checkValidity()') and admin.locator('[name=result]').input_value()=='先故意遗漏依据' and admin.locator('#dialog').evaluate('(d)=>d.open'))
        # M3 deliberately protects unsaved input: explicitly accept this discard.
        admin.once('dialog',lambda d:d.accept())
        admin.get_by_role('button',name='取消',exact=True).click()
        admin.locator('.task').first.get_by_role('button',name='登记结果与依据',exact=True).click();admin.locator('[name=result]').fill('合成维修结果');admin.locator('[name=evidence_value]').fill('合成维修依据');modal_submit(admin)
        check('Executor cannot self-review UI',admin.get_by_role('button',name='任务复核',exact=True).count()==0)
        goto_flow(boss,title2);boss.get_by_role('button',name='任务复核',exact=True).click();boss.locator('[name=decision]').select_option('reject');boss.locator('[name=reason]').fill('合成退回，补充结果');modal_submit(boss)
        check('Maintenance rejection returns to execution',boss.locator('.task .chip').first.inner_text()=='执行中')
        refresh(admin);admin.get_by_role('button',name='登记结果与依据',exact=True).click();admin.locator('[name=result]').fill('合成补充结果');modal_submit(admin)
        goto_flow(boss,title2);boss.get_by_role('button',name='任务复核',exact=True).click();boss.locator('[name=reason]').fill('补充后合成核验通过');modal_submit(boss)
        finish_flow(admin,boss,title2,'maintenance')
        # Rectification: two dependency-linked task approvals.
        title3='浏览器新整改 04';new_flow(admin,'rectification',title3);check('Rectification created from real form')
        for n in range(2):finish_task(admin,boss,title3,n)
        finish_flow(admin,boss,title3,'rectification')
        boss.screenshot(path=str(REPORT/'v04-flow-detail.png'),full_page=True)
        # Admin creates a new account via the actual form.
        admin.locator('.sidebar button[data-page=users]').click();admin.get_by_role('button',name='创建账户',exact=True).click()
        admin.locator('[name=username]').fill('browser_new');admin.locator('[name=display_name]').fill('浏览器合成账户');admin.locator('[name=password]').fill('Browser-fixture-only-837');modal_submit(admin)
        admin.get_by_text('browser_new',exact=True).wait_for();check('Account created through UI')
        # Field and row filtering is server-side: viewer gets no facts or editing controls.
        view=page_for({'width':390,'height':844});login(view,url,'viewer',creds['viewer'])
        check('Read-only account has no workflow create buttons',view.locator('[data-action=new-flow]').count()==0)
        check('Narrow layout has no page-level horizontal overflow',view.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        view.screenshot(path=str(REPORT/'v04-mobile.png'),full_page=True)
        # Actual download initiated from browser, then parsed.
        admin.locator('.sidebar button[data-page=imports]').click()
        admin.get_by_role('button',name='导出 JSON 业务工作区',exact=True).click()
        admin.wait_for_function('window.__downloadText!==null')
        path=Path(temp)/'browser-export.json';path.write_text(admin.evaluate('window.__downloadText'))
        exported=json.loads(path.read_text());check('Browser-generated JSON blob includes fourteen flows',len(exported['workspace']['flow']['instances'])==14)
        check('Export blob excludes account password',all(v not in path.read_text() for v in creds.values()))
        # Restart a real process: previous data and sessions survive in SQLite.
        stop(proc);proc,url=start_server(data,port,init=False)
        mount(admin,url);admin.locator('h1').filter(has_text='今日工作台').wait_for();admin.locator('.sidebar button[data-page=flows]').click();admin.locator('.stats').wait_for();check('Real server restart preserves proxy-client session and records',admin.locator('.table-wrap tbody tr').count()==14)
        admin.get_by_role('button',name=title3,exact=True).click();check('Closed status survives real server restart',admin.locator('section.panel').first.locator('.chip').first.inner_text()=='已关闭')
        # Fresh DB, original unmodified v0.2 file through browser preview + commit.
        fresh=Path(temp)/'import';fresh.mkdir();p2,url2=start_server(fresh,free_port(),demo=False)
        creds2=credentials(fresh/'INITIAL_CREDENTIALS_DEMO-01.txt');imp=page_for();login(imp,url2,'admin',creds2['admin'])
        imp.locator('.sidebar button[data-page=imports]').click();imp.locator('#import-file').set_input_files(str(ROOT/'fixtures/v02-original-demo.json'))
        imp.get_by_role('button',name='确认导入空工作区',exact=True).wait_for();check('Original v0.2 browser preview succeeds')
        imp.get_by_role('button',name='确认导入空工作区',exact=True).click();imp.locator('.stats').wait_for()
        check('v0.2 import does not invent historical flows',imp.locator('.stat strong').first.inner_text()=='0')
        imp.locator('.sidebar button[data-page=records]').click();imp.locator('#record-table').wait_for()
        check('v0.2 browser migration preserves six residents',imp.locator('tbody tr').count()==6)
        imp.locator('#record-table').select_option('tasks');imp.get_by_role('heading',name='已授权业务记录').wait_for();imp.wait_for_timeout(200)
        check('v0.2 browser migration preserves ten tasks',imp.locator('tbody tr').count()==10)
        check('No uncaught browser errors',not errors);check('No external network requests',not external)
        result={'status':'PASS','checks_passed':len(checks),'checks':checks,'browser':browser.version,'transport':'Chromium page.set_content + Python-bound fetch adapter + real HTTP Uvicorn/SQLite; not native browser navigation/cookies/download/sessionStorage','native_navigation':'BLOCKED_BY_ENVIRONMENT_POLICY','download':'BLOB_CAPTURE_ONLY','web_storage':'IN_MEMORY_SHIM; real sessionStorage pending Mac','mac_safari':'NOT_TESTED','postgresql':'NOT_TESTED','page_errors':errors,'external_requests':external}
        (REPORT/'browser-harness-tests.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({'status':'PASS','checks':len(checks),'browser':browser.version},ensure_ascii=False))
        browser.close()
except BaseException as exc:
    (REPORT/'browser-harness-tests.json').write_text(json.dumps({'status':'FAIL','passed_before_failure':len(checks),'checks':checks,'error':str(exc),'page_errors':errors,'external_requests':external},ensure_ascii=False,indent=2))
    raise
finally:
 for proc,log in processes:
    if proc.poll() is None:stop(proc)
    log.close()
