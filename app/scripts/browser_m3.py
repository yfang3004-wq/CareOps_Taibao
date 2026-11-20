#!/usr/bin/env python3
"""M3 real DOM actions + HTTP to a real service. Native transport probed once.
No user data, no credentials or cookies in output; all servers and temp files
are owned by this test and stopped/removed. See transport field in report.
"""
import json,os,sys,tempfile,traceback,base64,sqlite3,hashlib
from pathlib import Path
import browser_support as H
from playwright.sync_api import sync_playwright
ROOT=H.ROOT
OUT=Path(os.getenv('CAREOPS_TEST_REPORT_DIR',str(ROOT/'reports/browser-m3')));OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[];requests=[];navigation={'status':'NOT_TESTED'}
def ck(name,condition=True):
    if not condition:raise AssertionError(name)
    checks.append({'name':name,'status':'PASS'})
def fill(page,values):
    for k,v in values.items():page.locator('#command-form [name='+k+']').fill(v)
def login_form(p,name,password):
    p.locator('#login-form [name=username]').fill(name);p.locator('#login-form [name=password]').fill(password);p.get_by_role('button',name='登录',exact=True).click();p.locator('h1').filter(has_text='今日工作台').wait_for()
def logout(p):
    p.get_by_role('button',name='退出',exact=True).click();p.locator('#login-form').wait_for()
def openflows(p):p.locator('.sidebar button[data-page=flows]').click();p.locator('.stats').wait_for()
def command(p,typ):p.locator('[data-action=command][data-type='+typ+']').first.click()
def req(p,path):return H.proxies[p].get(H.bases[p]+path)
status='FAIL'
with tempfile.TemporaryDirectory(prefix='careops-m3-ui-') as tmp:
 try:
  folder=Path(tmp);proc,url=H.start_server(folder,H.free_port());creds=H.credentials(folder/'INITIAL_CREDENTIALS_DEMO-01.txt')
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
   probe=browser.new_page()
   try:
    probe.goto(url,timeout=10000);probe.locator('#login-form').wait_for(timeout=10000)
    navigation={'status':'PASS','scope':'Native local navigation only; main scripted checks below use explicit HTTP adapter.'}
   except Exception as exc:navigation={'status':'BLOCKED_ENVIRONMENT','error':str(exc)[:500]}
   probe.close()
   accept_dialog=lambda d:d.accept()
   contexts=[browser.new_context(viewport={'width':1280,'height':940}) for _ in range(3)]
   p,b,m=[c.new_page() for c in contexts]
   for page in [p,b,m]:
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('dialog',accept_dialog)
    page.on('request',lambda r:requests.append(r.url) if not r.url.startswith(('http://127.0.0.1:','blob:')) else None)
   H.login(p,url,'caregiver',creds['caregiver']);H.login(b,url,'supervisor',creds['supervisor']);H.login(m,url,'maintenance',creds['maintenance'])
   p.locator('.sidebar [data-page=today]').click();p.locator('#today-filter').wait_for();ck('Workday HTML filter controls rendered')
   p.screenshot(path=str(OUT/'m3-today.png'),full_page=True)
   openflows(p);p.locator('[data-action=new-flow][data-kind=maintenance]').click()
   p.locator('#flow-form [name=resident_id]').select_option('R001')
   p.locator('#flow-form [name=title]').fill('M3 房间维修与接续')
   p.locator('#flow-form [name=description]').fill('PRIVATE-RAW-NOTE not for maintenance')
   p.locator('#flow-form [name=location]').fill('PRIVATE-ORIGINAL-LOCATION')
   p.locator('#flow-form [name=due_at]').fill('2099-10-05 18:00')
   p.remove_listener('dialog',accept_dialog);p.once('dialog',lambda d:d.dismiss())
   p.get_by_role('button',name='取消',exact=True).click()
   ck('Declining discard preserves unsaved business form',p.locator('#dialog').evaluate('(d)=>d.open') and p.locator('#flow-form [name=description]').input_value()=='PRIVATE-RAW-NOTE not for maintenance')
   p.on('dialog',accept_dialog)
   p.locator('[data-action=save-draft]').click();p.wait_for_function('document.getElementById("draft-status").textContent.includes("草稿已保存到本机")')
   ck('Explicit new-flow draft saved via real API')
   all_before=req(p,'/api/workflows').json()['items'];ck('Saving draft does not create a workflow',not any(x.get('title')=='M3 房间维修与接续' for x in all_before))
   ck('Draft body absent from browser Web Storage',not p.evaluate("JSON.stringify([...window.__careopsHarnessStorage])").find('PRIVATE-RAW-NOTE')>=0)
   p.get_by_role('button',name='取消',exact=True).click();logout(p)
   ck('Logout removes old modal plaintext from DOM','PRIVATE-RAW-NOTE' not in p.content())
   login_form(p,'supervisor',creds['supervisor']);p.locator('.sidebar [data-page=drafts]').click();p.locator('h2').filter(has_text='仅属于当前账户').wait_for()
   ck('Another account has no access to saved draft',p.locator('[data-action=restore-draft]').count()==0)
   logout(p);login_form(p,'caregiver',creds['caregiver']);p.locator('.sidebar [data-page=drafts]').click();p.locator('[data-action=restore-draft]').first.click()
   p.locator('#flow-form').wait_for();ck('Original draft restored after authentication',p.locator('#flow-form [name=description]').input_value()=='PRIVATE-RAW-NOTE not for maintenance')
   ck('Restoration requires explicit recheck',not p.locator('#flow-form').evaluate('(el)=>el.checkValidity()'))
   p.locator('[data-draft-recheck]').check();H.modal_submit(p);p.locator('.task').first.wait_for()
   ck('Draft submitted without duplicate new workflow',len([x for x in req(p,'/api/workflows').json()['items'] if x.get('title')=='M3 房间维修与接续'])==1)
   wid=next(x['id'] for x in req(p,'/api/workflows').json()['items'] if x.get('title')=='M3 房间维修与接续')
   ck('Draft body consumed in same business transaction',req(p,'/api/drafts').json()['items'][0]['submitted'])
   ck('Missing mandatory closure items shown with disabled final button',p.get_by_role('button',name='提交最终复核',exact=True).is_disabled())
   command(p,'share_repair')
   maint_id=next(x['id'] for x in req(p,'/api/directory').json() if x['role']=='maintenance')
   p.locator('#command-form [name=recipient_user_id]').select_option(maint_id)
   fill(p,{'location':'二层 201卫生间','equipment':'扶手','fault':'固定件松动','access_window':'请先联系值班台确认入室','contact_role':'二层值班台','reason':'只发布维修所需信息'})
   p.locator('#command-form input[type=checkbox]').check();H.modal_submit(p)
   # Explicit handover on full view; maintenance receives only approved projection.
   command(p,'create_handover');p.locator('[name=receiver_user_id]').select_option(maint_id)
   fill(p,{'observation':'PRIVATE-HANDOVER-HISTORY','request':'PRIVATE-HANDOVER-INSTRUCTIONS','shift':'夜班'});H.modal_submit(p)
   m.locator('.sidebar [data-page=today]').click();m.locator('[data-action=today-queue][data-queue=handover]').click();m.locator('[data-action=open-flow][data-id="'+wid+'"]').click();m.locator('.task').first.wait_for()
   ck('Maintenance sees room work in incoming handover queue',m.locator('h3').filter(has_text='只提供维修必要信息').count()==1)
   safe=req(m,'/api/workflows/'+wid).text
   ck('Original notes and resident identifiers excluded from API',all(x not in safe for x in ['PRIVATE-','R001','resident_id']))
   ck('Raw notes excluded from rendered DOM','PRIVATE-' not in m.content())
   m.screenshot(path=str(OUT/'m3-maintenance-minimal.png'),full_page=True)
   m.locator('[data-action=ack]').click();m.locator('[data-action=takeover]').wait_for()
   detail=req(m,'/api/workflows/'+wid).json()['item'];ck('Acknowledgment alone does not transfer task',detail['tasks'][0]['owner']!=('account:'+maint_id) and detail['tasks'][0]['status']=='待处理')
   m.locator('[data-action=takeover]').click();fill(m,{'reason':'早班明确接续此维修任务'});H.modal_submit(m)
   ck('Explicit continuation changes accountable owner only',req(m,'/api/workflows/'+wid).json()['item']['tasks'][0]['owner']=='account:'+maint_id)
   command(m,'start_task');m.locator('[data-type=submit_task]').wait_for()
   command(m,'submit_task');fill(m,{'result':'合成维修完成，交由另一账户核对','evidence_value':'合成维修依据：固定件已核对'});H.modal_submit(m)
   b.locator('.sidebar [data-page=today]').click();b.locator('[data-action=today-queue][data-queue=review]').click();b.locator('[data-action=open-flow][data-id="'+wid+'"]').click();b.locator('.task').first.wait_for()
   command(b,'review_task');fill(b,{'reason':'独立复核合成维修依据'});H.modal_submit(b)
   H.refresh(m);command(m,'submit_workflow');m.locator('section.panel').first.locator('.chip').first.filter(has_text='待最终复核').wait_for()
   H.refresh(b);command(b,'review_workflow');fill(b,{'reason':'独立最终复核通过'});H.modal_submit(b)
   ck('Cross-role room repair completes final close',req(b,'/api/workflows/'+wid).json()['item']['status']=='closed')
   H.refresh(p);p.locator('[data-action=resident]').click();p.locator('.timeline-item').first.wait_for()
   ck('Resident timeline includes responsibility and review',p.get_by_role('button',name='确认接续责任',exact=True).count()==1 and '复核记录' in p.locator('#content').inner_text())
   ck('Human record details replace JSON blob',p.locator('.fields dt').filter(has_text='本人目标与偏好').count()==1 and p.locator('#content pre').count()==0)
   p.screenshot(path=str(OUT/'m3-resident-timeline.png'),full_page=True)
   p.set_viewport_size({'width':390,'height':844});p.screenshot(path=str(OUT/'m3-mobile-profile.png'),full_page=True)
   ck('390px view has no document-level horizontal overflow',p.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'))
   p.set_viewport_size({'width':1280,'height':940})
   # Persist a separate unfinished draft; restart the REAL server and recover it.
   openflows(p);p.locator('[data-action=new-flow][data-kind=maintenance]').click();p.locator('#flow-form [name=title]').fill('未完成的重启草稿')
   p.locator('[data-action=save-draft]').click();p.wait_for_function('document.getElementById("draft-status").textContent.includes("草稿已保存到本机")');p.get_by_role('button',name='取消',exact=True).click()
   # Force real server session invalidation, then product API handles 401.
   p.locator('.sidebar [data-page=drafts]').click();p.locator('[data-action=restore-draft]').first.click();p.locator('#flow-form').wait_for()
   p.locator('#flow-form [name=description]').fill('PRIVATE-UNSAVED-AFTER-SAVE')
   token_digest=hashlib.sha256(H.proxies[p].cookies.get('careops_session').encode()).hexdigest()
   with sqlite3.connect(folder/'careops.sqlite3') as db:db.execute('UPDATE sessions SET expires=0 WHERE token_hash=?',(token_digest,))
   p.locator('[data-action=save-draft]').click()
   p.locator('#login-form').wait_for()
   ck('Authentication loss clears even unsaved modal plaintext','PRIVATE-UNSAVED-AFTER-SAVE' not in p.content() and not p.locator('#dialog').evaluate('(d)=>d.open'))
   login_form(p,'caregiver',creds['caregiver']);p.locator('.sidebar [data-page=drafts]').click();p.locator('[data-action=restore-draft]').first.click();p.locator('#flow-form').wait_for()
   ck('Reauthentication restores saved version only, never unsaved edits',p.locator('#flow-form [name=title]').input_value()=='未完成的重启草稿' and p.locator('#flow-form [name=description]').input_value()=='')
   p.screenshot(path=str(OUT/'m3-private-draft.png'),full_page=True)
   p.get_by_role('button',name='取消',exact=True).click()
   H.stop(proc);proc,url=H.start_server(folder,int(url.rsplit(':',1)[1]),init=False)
   H.mount(p,url);p.locator('h1').filter(has_text='今日工作台').wait_for();p.locator('.sidebar [data-page=drafts]').click();p.locator('[data-action=restore-draft]').first.click();p.locator('#flow-form').wait_for()
   ck('Server restart preserves account-private draft',p.locator('#flow-form [name=title]').input_value()=='未完成的重启草稿')
   ck('Server restart preserves original closed workflow',req(p,'/api/workflows/'+wid).json()['item']['status']=='closed')
   ck('No uncaught page errors',not errors)
   ck('No external network requests',not requests)
   status='PASS';browser.close()
 except Exception:
  (OUT/'failure.txt').write_text(traceback.format_exc());print(traceback.format_exc())
 finally:
  for proc,log in H.processes:
   if proc.poll() is None:H.stop(proc)
   log.close()
  for proxy in H.proxies.values():proxy.close()
  result={'status':status,'checks_passed':len(checks),'checks':checks,'page_errors':errors,'external_requests':requests,'native_navigation':navigation,'transport':'Chromium DOM + Python HTTP adapter to real Uvicorn/SQLite. sessionStorage memory adapter; Cookie/Origin via Python. Not Mac native acceptance.','real_service_restarted':status=='PASS','synthetic_only':True}
  (OUT/'m3-browser-tests.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'status':status,'checks':len(checks)},ensure_ascii=False))
sys.exit(0 if status=='PASS' else 1)
