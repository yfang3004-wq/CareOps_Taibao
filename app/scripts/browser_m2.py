#!/usr/bin/env python3
"""M2 DOM + live-HTTP synthetic scenario. Adapter, not native-browser acceptance."""
import base64
import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ.setdefault('CAREOPS_TEST_REPORT_DIR',str(Path(__file__).resolve().parents[1]/'reports/browser-m2'))
from browser_support import *
sys.path.insert(0,str(ROOT))
from careops.tabular_codec import decode_xlsx

try:
 with tempfile.TemporaryDirectory(prefix='careops-m2-browser-') as tmp:
  data=Path(tmp)/'data';data.mkdir();port=free_port();proc,url=start_server(data,port)
  creds=credentials(data/'INITIAL_CREDENTIALS_DEMO-01.txt')
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
   def page_for(width=1440,height=1000):
    ctx=browser.new_context(viewport={'width':width,'height':height});contexts.append(ctx)
    page=ctx.new_page();page.set_default_timeout(10000)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda req:external.append(req.url) if not req.url.startswith(('http://127.0.0.1:','blob:')) else None)
    return page
   admin=page_for();boss=page_for();login(admin,url,'admin',creds['admin']);login(boss,url,'supervisor',creds['supervisor'])
   check('Two authenticated synthetic accounts; no credentials written to report')
   admin.locator('.sidebar [data-page=imports]').click()
   admin.locator('#table-file').set_input_files(str(ROOT/'examples/M2_维修导入_含错.xlsx'))
   admin.locator('#table-target').wait_for()
   check('XLSX inspected and columns auto-mapped',admin.locator('#table-target').input_value()=='workorders' and admin.locator('#table-header').input_value()=='1')
   admin.get_by_role('button',name='生成校验预览',exact=True).click()
   admin.get_by_role('heading',name='预览：1行可追加，3行拒绝，尚未提交',exact=True).wait_for()
   check('Preview reports missing fact and both duplicate rows')
   baseline=proxies[admin].get(url+'/api/records/workorders').json()['items']
   check('Preview has not appended valid or rejected rows',not any(r['workorder_id'].startswith('M2-DEMO-') for r in baseline))
   admin.get_by_role('button',name='确认追加 1 行',exact=True).click()
   admin.locator('#toast').filter(has_text='先确认拒绝行').wait_for()
   check('Partial import requires explicit rejected-row consent')
   admin.evaluate("window.__download=null")
   admin.get_by_role('button',name='下载校验记录 JSON',exact=True).click();admin.wait_for_function('window.__download!==null')
   report=json.loads(base64.b64decode(admin.evaluate('window.__download.base64')))
   check('Full validation download preserves all four source outcomes',len(report['report']['rows'])==4)
   (REPORT/'validation-synthetic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
   admin.screenshot(path=str(REPORT/'m2-import-preview.png'),full_page=True)
   admin.locator('#table-skip-invalid').check();admin.get_by_role('button',name='确认追加 1 行',exact=True).click()
   admin.locator('#record-table').wait_for();admin.get_by_text('M2-DEMO-WO-001',exact=True).wait_for()
   new=proxies[admin].get(url+'/api/records/workorders').json()['items']
   check('Exactly one accepted row appended',len(new)==len(baseline)+1 and sum(x['workorder_id']=='M2-DEMO-WO-001' for x in new)==1)
   check('Rejected rows absent from business table',not any(x['workorder_id'] in ['M2-DEMO-BAD-001','M2-DEMO-DUP-001'] for x in new))
   admin.locator('.sidebar [data-page=imports]').click();admin.get_by_role('button',name='查看核对记录',exact=True).click()
   admin.get_by_role('heading',name='已提交：追加1行，拒绝3行（仅核对，不重复提交）',exact=True).wait_for()
   check('Committed history remains accessible with rejected rows but no second commit button',admin.locator('#table-preview tbody tr').count()==4 and admin.locator('#table-preview [data-action=commit-table]').count()==0)
   admin.locator('.sidebar [data-page=records]').click();admin.locator('#record-table').select_option('workorders');admin.get_by_text('M2-DEMO-WO-001',exact=True).wait_for()
   card=admin.locator('tbody tr').filter(has_text='M2-DEMO-WO-001');card.get_by_role('button',name='转入协作流程',exact=True).click()
   admin.locator('#flow-form [name=existing_subject_id]').wait_for(state='attached')
   check('Imported subject selected without replacing historical identity',admin.locator('#flow-form [name=existing_subject_id]').input_value()=='M2-DEMO-WO-001')
   modal_submit(admin);admin.locator('.task').first.wait_for();title='公共扶手维修'
   finish_task(admin,boss,title,0);finish_flow(admin,boss,title,'maintenance')
   check('Historical workorder reused, not duplicated',len(proxies[admin].get(url+'/api/records/workorders').json()['items'])==len(new))
   admin.locator('.sidebar [data-page=records]').click();admin.locator('#record-table').select_option('workorders');admin.get_by_role('button',name='导出 XLSX',exact=True).wait_for()
   admin.locator('[name=export_status]').select_option('已关闭')
   admin.evaluate('window.__download=null');admin.get_by_role('button',name='导出 XLSX',exact=True).click();admin.wait_for_function('window.__download!==null')
   raw=base64.b64decode(admin.evaluate('window.__download.base64'));(REPORT/'CareOps_workorders.xlsx').write_bytes(raw)
   book=decode_xlsx(raw);grid=[r['values'] for r in book['sheets'][0]['rows']];h=grid[0]
   check('XLSX contains closed imported row and no open rows',any(r[h.index('维修单编号')]=='M2-DEMO-WO-001' for r in grid[1:]) and all(r[h.index('办理状态')]=='已关闭' for r in grid[1:]))
   check('XLSX metadata includes authorization and filter scope',book['sheets'][1]['name']=='导出说明' and '筛选条件' in str(book['sheets'][1]))
   admin.evaluate('window.__download=null');admin.get_by_role('button',name='导出 CSV',exact=True).click();admin.wait_for_function('window.__download!==null')
   raw=base64.b64decode(admin.evaluate('window.__download.base64'));(REPORT/'CareOps_workorders.csv').write_bytes(raw)
   text=raw.decode('utf-8-sig');csvrows=list(csv.reader(io.StringIO(text)))
   check('CSV and XLSX identify the same closed records',{r[1] for r in csvrows[1:]}=={r[1] for r in grid[1:]})
   admin.screenshot(path=str(REPORT/'m2-records-and-export.png'),full_page=True)
   # Real server commits a request; adapter deliberately discards only its response.
   admin.locator('.sidebar [data-page=flows]').click();admin.locator('[data-action=new-flow][data-kind=maintenance]').click()
   admin.locator('#flow-form [name=title]').fill('M2 响应丢失合成事项')
   admin.locator('#flow-form [name=location]').fill('合成公共区');admin.locator('#flow-form [name=description]').fill('SECRET-FORM-TEXT-NOT-IN-STORAGE')
   admin.locator('#flow-form [name=due_at]').fill('2099-01-01 18:00')
   admin.evaluate('window.__loseNextCommandResponse=true');admin.locator('#dialog button[type=submit]').click()
   admin.locator('#dialog [data-action=check-submission]').wait_for()
   flows=proxies[admin].get(url+'/api/workflows').json()['items']
   check('Dropped response does not mean failed transaction',sum(f['title']=='M2 响应丢失合成事项' for f in flows)==1)
   stored=admin.evaluate('JSON.stringify([...window.__careopsHarnessStorage.entries()])')
   check('Unconfirmed marker stores no business text or secrets','SECRET-FORM' not in stored and all(p not in stored for p in creds.values()))
   check('Pending intent message explains recovery',admin.locator('#dialog').inner_text().find('尚未核对结果')>=0)
   # Remount recreates app state, while adapter storage simulates same-tab persistence.
   mount(admin,url);admin.locator('.stats').wait_for();admin.get_by_role('button',name='核对上次提交',exact=True).click();admin.locator('.task').first.wait_for()
   check('Reloaded UI queries receipt and navigates to original flow',admin.locator('.panel h2').first.inner_text()=='M2 响应丢失合成事项')
   flows=proxies[admin].get(url+'/api/workflows').json()['items'];check('Recovery has not created a second flow',sum(f['title']=='M2 响应丢失合成事项' for f in flows)==1)
   check('Resolved marker removed',admin.evaluate('window.__careopsHarnessStorage.size')==0)
   stop(proc);proc,url=start_server(data,port,init=False)
   saved=proxies[admin].get(url+'/api/records/workorders').json()['items'];check('Actual process restart keeps imported workorder closed',next(r for r in saved if r['workorder_id']=='M2-DEMO-WO-001')['status']=='已关闭')
   small=page_for(390,844);login(small,url,'admin',creds['admin']);small.locator('.sidebar [data-page=imports]').click();small.locator('#table-file').set_input_files(str(ROOT/'examples/M2_维修导入_含错.csv'));small.locator('#table-target').wait_for()
   check('Small-screen import mapping does not overflow viewport',small.evaluate('document.documentElement.scrollWidth<=innerWidth'))
   small.screenshot(path=str(REPORT/'m2-mobile-mapping.png'),full_page=True)
   viewer=page_for();login(viewer,url,'viewer',creds['viewer']);check('Viewer has no import navigation',viewer.locator('.sidebar [data-page=imports]').count()==0)
   check('No uncaught browser script errors',not errors);check('No external requests',not external)
   result={'status':'PASS','checks_passed':len(checks),'checks':checks,'browser':browser.version,
    'transport':'Chromium DOM + Python HTTP bridge + real Uvicorn and SQLite. Native loopback navigation blocked by environment policy.',
    'native_cookie_and_download':'NOT_TESTED','sessionStorage':'In-memory same-tab adapter; real native storage needs Mac smoke','loss_injection':'Actual server commit then adapter rejects response; no change to product backend',
    'page_errors':errors,'external_requests':external,'mac':'NOT_TESTED','production_ready':False}
   (REPORT/'m2-browser-tests.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'status':'PASS','checks':len(checks)},ensure_ascii=False));browser.close()
except BaseException as e:
 (REPORT/'m2-browser-tests.json').write_text(json.dumps({'status':'FAIL','checks':checks,'passed_before_failure':len(checks),'error':str(e),'page_errors':errors,'external_requests':external},ensure_ascii=False,indent=2));raise
finally:
 for proc,logfile in processes:
  if proc.poll() is None:stop(proc)
  logfile.close()
 for client in proxies.values():client.close()
