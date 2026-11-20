#!/usr/bin/env python3
"""B0/B1 real DOM + HTTP acceptance for the public maintenance MVP path.
Synthetic test data only. Starts/stops an isolated local CareOps server.
"""
import json, os, tempfile, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright
import browser_support as H

ROOT=H.ROOT
OUT=Path(os.getenv('CAREOPS_TEST_REPORT_DIR',str(ROOT/'reports/browser-mvp-refactor')))
OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[];external=[];status='FAIL'
def ck(name,condition=True):
    if not condition: raise AssertionError(name)
    checks.append({'name':name,'status':'PASS'});print('PASS',name,flush=True)
def login_form(page,name,password):
    page.locator('#login-form [name=username]').fill(name)
    page.locator('#login-form [name=password]').fill(password)
    page.get_by_role('button',name='登录',exact=True).click()
    page.locator('h1').filter(has_text='今日工作台').wait_for()
def logout(page):
    page.get_by_role('button',name='退出',exact=True).click();page.locator('#login-form').wait_for()

def req(page,path): return H.proxies[page].get(H.bases[page]+path)

with tempfile.TemporaryDirectory(prefix='careops-mvp-ui-') as tmp:
    proc=None
    try:
        folder=Path(tmp);print('STAGE start-server',flush=True);proc,url=H.start_server(folder,H.free_port());print('STAGE server-ready',flush=True);creds=H.credentials(folder/'INITIAL_CREDENTIALS_DEMO-01.txt')
        print('STAGE playwright',flush=True)
        with sync_playwright() as pw:
            print('STAGE launch',flush=True)
            browser=pw.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            ectx=browser.new_context(viewport={'width':1280,'height':900});vctx=browser.new_context(viewport={'width':1280,'height':900})
            e=ectx.new_page();v=vctx.new_page()
            for p in [e,v]:
                p.on('pageerror',lambda err:errors.append(str(err)))
                p.on('request',lambda r:external.append(r.url) if not r.url.startswith(('http://127.0.0.1:','blob:')) else None)
            print('STAGE login',flush=True)
            H.login(e,url,'caregiver',creds['caregiver']);H.login(v,url,'supervisor',creds['supervisor'])

            e.locator('.sidebar [data-page=today]').click();e.locator('#today-filter').wait_for()
            e.get_by_role('button',name='快速报修',exact=True).click();e.locator('#flow-form').wait_for()
            ck('Quick repair starts from Today')
            ck('Quick repair remains public and omits resident selector',e.locator('#flow-form [name=resident_id]').count()==0)
            ck('Quick repair exposes optional due date',not e.locator('#flow-form [name=due_at]').evaluate('(x)=>x.required'))
            e.locator('[name=location]').fill('2F公共走廊')
            e.locator('[name=title]').fill('走廊灯具')
            e.locator('[name=equipment_id]').fill('EQ-LIGHT-2F-01')
            e.locator('[name=description]').fill('灯具不亮；纯合成测试')
            e.locator('#flow-form button[type=submit]').click();e.locator('.task').first.wait_for()
            ck('Repair saves with no fabricated due date','期限 未约定' in e.locator('.task').first.inner_text())
            wid=req(e,'/api/workflows').json()['items'][-1]['id']
            detail=req(e,'/api/workflows/'+wid).json()['item']
            ck('Stable equipment id stored when explicitly supplied',detail['subject'].get('equipment_id')=='EQ-LIGHT-2F-01')
            ck('Unknown due remains blank in API',detail['subject'].get('due_at')=='' and detail['tasks'][0].get('due_at')=='')

            e.get_by_role('button',name='接手并开始',exact=True).click();e.get_by_role('button',name='登记结果与依据',exact=True).wait_for()
            detail=req(e,'/api/workflows/'+wid).json()['item'];task=detail['tasks'][0]
            ck('Explicit start records acceptance',bool(task.get('accepted_at')) and bool(task.get('accepted_by')))
            ck('Start does not fabricate performed time',not task.get('performed_at'))

            e.get_by_role('button',name='登记结果与依据',exact=True).click();e.locator('#command-form').wait_for()
            ck('Result and evidence share one form',e.locator('[name=result]').count()==1 and e.locator('[name=evidence_value]').count()==1)
            ck('Required maintenance evidence enforced in first result form',e.locator('[name=evidence_value]').evaluate('(x)=>x.required'))
            e.locator('[name=result]').fill('灯具恢复照明')
            e.locator('[name=evidence_value]').fill('现场复测正常')
            e.locator('#command-form button[type=submit]').click();e.locator('.task .chip').first.filter(has_text='待复核').wait_for()
            detail=req(e,'/api/workflows/'+wid).json()['item'];task=detail['tasks'][0]
            ck('Result submission time is distinct field',bool(task.get('completed_at')) and not task.get('performed_at'))
            ck('Evidence persisted as separate fact',len([x for x in detail['evidence'] if x['task_id']==task['task_id']])==1)

            v.locator('.sidebar [data-page=today]').click();v.locator('[data-action=today-queue][data-queue=review]').click()
            v.locator('[data-action=open-flow][data-id="'+wid+'"]').click();v.locator('.task').first.wait_for()
            v.get_by_role('button',name='任务复核',exact=True).click();v.locator('[name=reason]').fill('独立核对合成维修结果');H.modal_submit(v)
            ck('Task review closes task but not workflow',req(v,'/api/workflows/'+wid).json()['item']['status']=='active')

            e.locator('.sidebar [data-page=today]').click();e.locator('#today-filter').wait_for()
            ck('Ready repair appears in default Today as closure card',e.locator('[data-action=today-submit][data-id="'+wid+'"]').count()==1)
            before=req(e,'/api/workflows/'+wid).json()['item']['status'];ck('Reading closure card does not auto-submit',before=='active')
            e.locator('[data-action=today-submit][data-id="'+wid+'"]').click();e.locator('section.panel').first.locator('.chip').first.filter(has_text='待最终复核').wait_for()
            ck('Owner explicitly submits final review from Today',req(e,'/api/workflows/'+wid).json()['item']['status']=='pending_review')

            v.locator('.sidebar [data-page=today]').click();v.locator('[data-action=today-queue][data-queue=review]').click();v.locator('[data-action=open-flow][data-id="'+wid+'"]').click()
            v.get_by_role('button',name='最终复核',exact=True).click();v.locator('#command-form').wait_for()
            target=v.locator('[data-reject-only]');task_select=v.locator('[name=task_id]')
            ck('Approve hides irrelevant rework selector',target.is_hidden() and task_select.is_disabled())
            v.locator('[name=decision]').select_option('reject');ck('Reject reveals rework selector',target.is_visible() and task_select.is_enabled())
            v.locator('[name=decision]').select_option('approve');ck('Switching back to approve removes rework payload',target.is_hidden() and task_select.is_disabled())
            v.locator('[name=reason]').fill('最终独立复核通过');H.modal_submit(v)
            ck('Public maintenance normal chain closes',req(v,'/api/workflows/'+wid).json()['item']['status']=='closed')
            v.screenshot(path=str(OUT/'mvp-maintenance-closed.png'),full_page=True)
            ck('No browser page errors',not errors)
            ck('No external network requests',not external)
            status='PASS'
            browser.close()
    except Exception as exc:
        errors.append(str(exc));errors.append(traceback.format_exc(limit=6))
    finally:
        if proc:H.stop(proc)

report={'status':status,'checks':checks,'errors':errors,'external_requests':external}
(OUT/'browser-mvp-refactor.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'status':status,'passed':len(checks),'errors':errors[:2]},ensure_ascii=False))
raise SystemExit(0 if status=='PASS' else 1)
