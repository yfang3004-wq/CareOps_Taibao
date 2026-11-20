/* Data ingress/egress UI. File bytes stay in memory; all parsing and permission
   decisions are repeated on the server. Uses existing app.js helpers. */
'use strict';
function residentExcelButtons(writable){return `<div class="resident-excel-actions">${btn('resident-template','下载Excel模板')}${writable?btn('resident-excel-import','导入Excel'):''}${btn('download-table','导出Excel（授权完整档案）','data-format="xlsx"')}</div>`;}
async function downloadResidentTemplate(){const response=await fetch('/api/tabular/residents/template?format=xlsx',{credentials:'same-origin'});if(!response.ok){const result=await response.json();throw Error(result.detail||'模板下载失败');}saveBlob(await response.blob(),'CareOps_老人档案_空模板.xlsx');toast('已触发空Excel模板下载，请确认保存；模板不含任何老人业务资料。');}
function tableImportPanel(target=''){
 S.residentImport=target==='residents';
 return `<section class="panel"><h2>${target==='residents'?'老人Excel导入：先核对，再新增 / 更新':'表格导入：先核对，再追加'}</h2><p class="sub">CSV / TSV / 普通 XLSX，2MB以内；一次选择一张表，最多1000条数据。${target==='residents'?'老人档案按机构与长者编号新增或更新，不按姓名猜合并；未映射旧字段保留，映射为空表示明确清空。':'只新增，不覆盖同号记录。'}不把表格里的人员姓名当作认证审批。</p><input id="table-file" class="file" type="file" accept=".csv,.tsv,.xlsx"><div id="table-mapping"></div><div id="table-preview"></div></section>`;
}
async function inspectTableFile(file){
 S.tablePreview=null;S.tableFile=null;S.inspectedFile=null;
 if(!file)return;if(file.size>2_000_000)throw Error('文件超过2MB，请拆分后导入。');
 const bytes=new Uint8Array(await file.arrayBuffer());let chars='';for(let i=0;i<bytes.length;i+=8192)chars+=String.fromCharCode(...bytes.subarray(i,i+8192));
 const payload={filename:file.name,file_base64:btoa(chars)};
 const result=await api('/tabular/inspect','POST',payload);S.tableFile=payload;S.inspectedFile=result;
 renderMapping(0,null,S.residentImport?'residents':null);
}
function renderMapping(sheetIndex=0,headerRow=null,target=null){
 S.tablePreview=null;$('table-preview').innerHTML='';
 const file=S.inspectedFile,sh=file.sheets[sheetIndex];S.importSheetIndex=sheetIndex;
 headerRow=headerRow??sh.suggested_header_row;
 const header=sh.sample_rows.find(r=>r.line===Number(headerRow));
 if(!header)throw Error('请选择预览范围内的表头行');
 const best=S.schemas.map(s=>{const mapping={};for(const f of s.fields){const found=header.values.map((v,i)=>({v,i})).filter(x=>[f.key,f.label,...(s.id==='residents'?({display_name:['显示称呼','老人姓名','长者姓名'],id_number:['身份证号码','身份证号'],guardian_phone:['监护人电话','联系电话'],guardian_name:['监护人姓名'],care_level:['护理等级'],dementia_status:['认知症','是否认知症']}[f.key]||[]):[])].some(z=>z.replace(/[\s_]+/g,'').toLowerCase()===String(x.v).replace(/[\s_]+/g,'').toLowerCase()));if(found.length===1)mapping[f.key]=found[0].i;}return {s,mapping,score:Object.keys(mapping).length+(s.idKey in mapping?3:0)};}).sort((a,b)=>b.score-a.score);
 const choice=target?best.find(b=>b.s.id===target):best[0],schema=choice.s;
 $('table-mapping').innerHTML=`<div class="notice"><p>文件 ${esc(file.filename)} · ${esc(file.encoding)} · 尚未写入</p>${file.warnings.map(w=>`<p class="sub">${esc(w)}</p>`).join('')}</div><div class="form-grid"><label>工作表<select id="table-sheet">${file.sheets.map((s,i)=>`<option value="${i}" ${i===sheetIndex?'selected':''}>${esc(s.name)}（${s.nonempty_rows}非空行）</option>`).join('')}</select></label><label>表头行<select id="table-header">${sh.sample_rows.map(r=>`<option value="${r.line}" ${r.line===Number(headerRow)?'selected':''}>第${r.line}行 · ${esc(r.values.slice(0,3).join(' / '))}</option>`).join('')}</select></label><label>目标台账<select id="table-target">${S.schemas.map(s=>`<option value="${s.id}" ${s.id===schema.id?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label></div><details open><summary>字段映射（*必填）</summary><div class="form-grid">${schema.fields.map(f=>`<label>${esc(f.label)}${f.required&&f.key!=='facility_id'?' *':''}<select data-map-field="${f.key}"><option value="">${f.key==='facility_id'?'使用当前机构 '+esc(S.facility.id):'不映射'}</option>${header.values.map((h,i)=>`<option value="${i}" ${choice.mapping[f.key]===i?'selected':''}>${i+1} · ${esc(h||'空表头')}</option>`).join('')}</select></label>`).join('')}</div></details><p class="sub">日期使用YYYY-MM-DD；时间使用YYYY-MM-DD HH:mm。公式单元格不计算、不采用缓存；不合格行保留在核对记录。</p><div class="form-footer">${btn('preview-table','生成校验预览','','primary')}</div>`;
}
async function previewTable(){
 const mapping={};document.querySelectorAll('[data-map-field]').forEach(el=>{if(el.value!=='')mapping[el.dataset.mapField]=Number(el.value);});
 const body={...S.tableFile,sheet:S.inspectedFile.sheets[Number($('table-sheet').value)].name,header_row:Number($('table-header').value),table:$('table-target').value,mapping,...($('table-target').value==='residents'?{mode:'upsert'}:{})};
 const r=await api('/tabular/preview','POST',body);S.tablePreview=r;
 renderTablePreview(r);
}
function renderTablePreview(p){
 const r=p.report,operation=r.mode==='upsert'?'新增 / 更新':'追加';
 $('table-preview').innerHTML=`<div class="notice ${r.rejected?'warn':''}"><h3>预览：${r.accepted}行可${operation}，${r.rejected}行拒绝，尚未提交</h3>${r.mode==='upsert'?`<p>新增 ${(r.rows||[]).filter(x=>x.status==='ACCEPTED'&&x.operation==='create').length} 行 · 更新 ${(r.rows||[]).filter(x=>x.status==='ACCEPTED'&&x.operation==='update').length} 行；更新按编号匹配，不按姓名合并。</p>`:''}<p>${esc(r.table_name)} · ${esc(r.sheet)} · 工作区版本 ${p.revision}</p><p class="sub break">来源SHA256 ${esc(r.source_sha256)}</p>${r.warnings.map(w=>`<p class="sub">${esc(w)}</p>`).join('')}<p class="sub">${esc(r.note)}</p></div><div class="table-wrap preview-scroll"><table><thead><tr><th>源行</th><th>编号</th><th>校验</th><th>原因/提示</th><th>映射内容</th></tr></thead><tbody>${r.rows.slice(0,100).map(x=>`<tr><td>${x.line}</td><td>${esc(x.record_id)}</td><td>${x.status==='ACCEPTED'?(r.mode==='upsert'?(x.operation==='update'?'更新':'新增'):'可追加'):'拒绝'}</td><td>${esc([...x.errors,...x.warnings].join('；'))}</td><td><details><summary>查看本行</summary><pre>${esc(JSON.stringify(x.values,null,2))}</pre></details></td></tr>`).join('')}</tbody></table></div><p class="sub">页面最多显示前100行；完整结果可下载核对记录。</p>${r.rejected?'<label class="checkline"><input type="checkbox" id="table-skip-invalid"> 我确认仅导入通过校验的行，拒绝行保留在核对记录，不进入业务台账</label>':''}<div class="form-footer">${btn('download-table-report','下载校验记录 JSON')}${btn('discard-table-preview','移除未提交预览')}${r.accepted?btn('commit-table','确认'+operation+' '+r.accepted+' 行','','primary'):''}</div><div data-pending-placeholder></div>`;
 updatePendingNotices();
}
async function loadImportHistory(){
 const r=await api('/tabular/history');if(!$('import-history'))return;
 $('import-history').innerHTML=`<p class="sub">${esc(r.scope)}</p><div class="table-wrap"><table><thead><tr><th>来源文件</th><th>台账</th><th>可写入 / 拒绝</th><th>状态</th><th>核对</th></tr></thead><tbody>${r.items.map(x=>`<tr><td>${esc(x.filename)}</td><td>${esc(x.table_name)}</td><td>${x.accepted} / ${x.rejected}</td><td>${x.committed?'已提交':x.expired?'已过期':'待确认'}</td><td>${btn('open-import-report','查看核对记录',`data-preview="${esc(x.preview_id)}"`,'small')}</td></tr>`).join('')||'<tr><td colspan="5">暂无与当前授权一致的记录</td></tr>'}</tbody></table></div>`;
}
async function openImportReport(id){
 const p=await api('/tabular/previews/'+encodeURIComponent(id));S.tablePreview=p;renderTablePreview(p);
 if(p.committed||p.expired){
  $('table-preview').querySelectorAll('[data-action="commit-table"],[data-action="discard-table-preview"],.checkline').forEach(el=>el.remove());
  $('table-preview').querySelector('h3').textContent=p.committed?`已提交：${p.report.mode==='upsert'?'新增 / 更新':'追加'}${p.report.accepted}行，拒绝${p.report.rejected}行（仅核对，不重复提交）`:'预览已过期（只供核对，需重新选择文件）';
 }
 $('table-preview').scrollIntoView({block:'start',behavior:'instant'});
}
function saveBlob(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}
async function commitTable(){
 const p=S.tablePreview;if(!p)throw Error('请重新预览');
 const skip=!!$('table-skip-invalid')?.checked;if(p.report.rejected&&!skip)throw Error('请先确认拒绝行的处理方式');
 const result=await S.tracker.submit('/tabular/commit',{preview_id:p.preview_id,expected_workspace_revision:p.revision,plan_sha256:p.plan_sha256,confirm:true,skip_invalid:skip},'table');
 S.revision=result.revision;S.tableFile=null;S.inspectedFile=null;S.tablePreview=null;toast(p.report.mode==='upsert'?`已新增 ${result.created??'未返回'} 行 / 更新 ${result.updated??'未返回'} 行；${result.rejected} 行保留于核对记录`:`已追加${result.imported}行；${result.rejected}行仅保留于核对记录`);S.page='records';S.table=p.report.table;await loadPage();
}
function exportPanel(schema){
 const fields=schema.fields;
 const status=fields.find(f=>f.key==='status'),due=fields.some(f=>['due_at','followup_due','review_due'].includes(f.key));
 return `<section class="panel"><h2>${S.table==='residents'?'导出授权范围内完整老人档案':'导出当前授权范围的台账'}</h2><p class="sub">先由服务器检查机构、照料区和字段权限，再应用下列筛选。${S.table==='residents'?'导出当前授权字段的完整档案，不仅是列表显示的几列。':''}不是整库备份；导出含业务资料，按敏感文件保管。</p><div class="form-grid">${status?selectField('export_status','状态筛选',[['','全部可见状态'],...status.values.map(v=>[v,v])],'',false):''}${fields.some(f=>f.key==='owner')&&S.user.role!=='viewer'?selectField('export_owner','负责人筛选',[['','全部可见负责人'],...S.directory.map(u=>['account:'+u.id,u.display_name])],'',false):''}${due?field('export_due_from','期限起始日期（含）','','date',false)+field('export_due_to','期限结束日期（含）','','date',false):''}</div><div class="form-footer">${btn('download-table','导出 XLSX','data-format="xlsx"','primary')}${btn('download-table','导出 CSV','data-format="csv"')}</div></section>`;
}
async function downloadTable(format){
 const q=new URLSearchParams({format});for(const key of ['status','owner','due_from','due_to']){const value=document.querySelector(`[name="export_${key}"]`)?.value;if(value)q.set(key,value);}
 let r;try{r=await fetch('/api/reports/'+encodeURIComponent(S.table)+'?'+q,{credentials:'same-origin'});}catch{throw Error('无法连接本机服务，未取得导出文件');}
 if(!r.ok){const x=await r.json();throw Error(x.detail||'导出失败');}
 const bytes=await r.arrayBuffer();saveBlob(new Blob([bytes],{type:format==='xlsx'?'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'text/csv;charset=utf-8'}),`CareOps_${S.table}.${format}`);
 toast('已触发下载，请确认保存；数据范围见XLSX“导出说明”或CSV元数据列。');
}
async function continueExisting(index){
 const row=S.records[index],kind={incidents:'incident',workorders:'maintenance',corrections:'rectification'}[S.table],schema=S.schemas.find(s=>s.id===S.table);
 if(!kind)throw Error('此台账需通过对应业务入口继续处理');
 await newFlow(kind);const form=$('flow-form');
 for(const [k,v] of Object.entries({title:row.title||row.category||'接续历史事项',description:row.description||row.title||'根据来源台账接续办理，历史身份未经认证',resident_id:row.resident_id||'',location:row.location||'',due_at:row.due_at||row.followup_due||'',occurred_at:row.occurred_at||'',category:row.category||'其他'})){if(form.elements[k])form.elements[k].value=v;}
 const hidden=document.createElement('input');hidden.type='hidden';hidden.name='existing_subject_id';hidden.value=row[schema.idKey];form.appendChild(hidden);
 const hint=document.createElement('p');hint.className='notice warn';hint.textContent='接续来源 '+row[schema.idKey]+'。不会把旧姓名认证为当前账号；请明确指定执行账户。原始事实以台账为准，已托管或已终结记录会被拒绝。';form.prepend(hint);
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('[data-action]');if(!b)return;const a=b.dataset.action;
 if(!['preview-table','commit-table','discard-table-preview','download-table-report','download-table','continue-row','open-import-report','resident-template','resident-excel-import'].includes(a))return;
 b.disabled=true;
 try{
 if(a==='resident-template')await downloadResidentTemplate();
 else if(a==='resident-excel-import'){S.residentImport=true;$('resident-excel-import').innerHTML=tableImportPanel('residents');$('table-file').focus();$('resident-excel-import').scrollIntoView({block:'start',behavior:'instant'});}
 else if(a==='open-import-report')await openImportReport(b.dataset.preview);
 else if(a==='preview-table'){await previewTable();await loadImportHistory();}
 else if(a==='commit-table')await commitTable();
 else if(a==='discard-table-preview'){if(S.tablePreview){await api('/tabular/previews/'+S.tablePreview.preview_id,'DELETE',{});S.tablePreview=null;$('table-preview').innerHTML='';}}
 else if(a==='download-table-report'){if(!S.tablePreview)throw Error('请先生成预览');saveBlob(new Blob([JSON.stringify(S.tablePreview,null,2)],{type:'application/json'}),'CareOps_table_validation.json');}
 else if(a==='download-table')await downloadTable(b.dataset.format);
 else if(a==='continue-row')await continueExisting(Number(b.dataset.index));
 }catch(err){toast(err.message);if($('table-preview')&&a==='preview-table')$('table-preview').innerHTML=`<p class="error">${esc(err.message)}</p>`;}
 finally{if(b.isConnected)b.disabled=false;updatePendingNotices();}
});
document.addEventListener('change',async e=>{
 try{
 if(e.target.id==='table-file')await inspectTableFile(e.target.files[0]);
 else if(e.target.id==='table-sheet')renderMapping(Number(e.target.value));
 else if(['table-header','table-target'].includes(e.target.id))renderMapping(Number($('table-sheet').value),Number($('table-header').value),$('table-target').value);
 else if(e.target.matches('[data-map-field]')){S.tablePreview=null;$('table-preview').innerHTML='<p class="sub">映射已变更，请重新生成校验预览。</p>';}
 }catch(err){toast(err.message);if($('table-mapping'))$('table-mapping').innerHTML=`<p class="error">${esc(err.message)}</p>`;}
});
