/* Restricted XLSX/CSV interchange. No network, formulas are NEVER evaluated. */
(function(root){
'use strict';
const E=typeof module!=='undefined'&&module.exports?require('./engine.js'):root.CareEngine;
const LIMIT={file:10*1024*1024,expanded:40*1024*1024,rows:5000,cols:80,sheets:24,entries:600};
function esc(s){return String(s??'').replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g,'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));}
function colName(n){let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
function colIndex(s){let n=0;for(const c of s)n=n*26+c.charCodeAt(0)-64;return n-1;}
function csvCell(v){let s=String(v??'');if(typeof v==='string'&&/^[\s]*[=+\-@\t\r]/.test(v))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}
function csv(rows){return '\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n');}
function parseCSV(s,delimiter){
 s=s.replace(/^\ufeff/,'');if(!delimiter){
  const counts={',':0,'\t':0,';':0};let q=false,nl=0;
  for(let i=0;i<s.length&&nl<8;i++){const c=s[i];if(c==='"'){if(q&&s[i+1]==='"'){i++;continue;}q=!q;}else if(!q){if(c==='\n')nl++;if(Object.hasOwn(counts,c))counts[c]++;}}
  delimiter=Object.entries(counts).sort((a,b)=>b[1]-a[1])[0][0];
 }
 let rows=[],lines=[],r=[],v='',quoted=false,after=false,physical=1,start=1;
 function addCell(){r.push(v);v='';after=false;}
 function addRow(){addCell();if(r.length>LIMIT.cols)throw Error('列数超过80列，请拆分后导入。');rows.push(r);lines.push(start);r=[];if(rows.length>LIMIT.rows+30)throw Error('单表超过5000条数据，请先分表或按期间导出。');}
 for(let i=0;i<s.length;i++){const c=s[i];
  if(quoted){if(c==='"'){if(s[i+1]==='"'){v+='"';i++;}else{quoted=false;after=true;}}else{v+=c;if(c==='\n')physical++;}continue;}
  if(c==='"'){if(v!=='')throw Error('CSV 引号格式不完整，请重新另存为 CSV。');quoted=true;}
  else if(c===delimiter)addCell();
  else if(c==='\r'||c==='\n'){if(c==='\r'&&s[i+1]==='\n')i++;addRow();physical++;start=physical;}
  else{if(after&&c.trim())throw Error('CSV 结束引号后存在额外文本。');v+=c;}
 }
 if(quoted)throw Error('CSV 存在未闭合的双引号。');if(v!==''||r.length||after)addRow();
 return {name:'数据',grid:rows,lineNumbers:lines,delimiter};
}
function xml(s){if(/<!DOCTYPE/i.test(s))throw Error('不接受带外部实体定义的 XML。');const d=new DOMParser().parseFromString(s,'application/xml');if(d.getElementsByTagName('parsererror').length)throw Error('Excel 内部 XML 格式错误。');return d;}
function tags(node,name){return [...node.getElementsByTagNameNS('*',name)];}
function first(node,name){return tags(node,name)[0];}
function resolve(base,target){if(/^[a-z]+:/i.test(target))throw Error('不读取外部工作表。');const parts=(target.startsWith('/')?target.slice(1):base+target).split('/'),out=[];for(const p of parts){if(p==='..'){if(!out.length)throw Error('非法工作表路径。');out.pop();}else if(p&&p!=='.')out.push(p);}return out.join('/');}
async function readXLSX(buf){
 const zip=await root.JSZip.loadAsync(buf);const files=Object.values(zip.files);if(files.length>LIMIT.entries)throw Error('工作簿内部项目过多，请复制所需数据到简单工作簿。');
 let expanded=0;for(const f of files){expanded+=f._data?.uncompressedSize||0;if(expanded>LIMIT.expanded)throw Error('解压后超过40MB，已拒绝导入。');}
 async function get(path,required=true){const f=zip.file(path);if(!f){if(required)throw Error('缺少 Excel 部件：'+path);return null;}const s=await f.async('string');if(s.length>LIMIT.expanded)throw Error('工作簿 XML 过大。');return xml(s);}
 const wb=await get('xl/workbook.xml'),rels=await get('xl/_rels/workbook.xml.rels');const relation={};
 for(const r of tags(rels,'Relationship'))if(r.getAttribute('TargetMode')!=='External')relation[r.getAttribute('Id')]=resolve('xl/',r.getAttribute('Target'));
 const st=await get('xl/sharedStrings.xml',false);const shared=st?tags(st,'si').map(si=>tags(si,'t').map(t=>t.textContent).join('')):[];
 const style=await get('xl/styles.xml',false),formats={};if(style)for(const f of tags(style,'numFmt'))formats[+f.getAttribute('numFmtId')]=f.getAttribute('formatCode');
 const xfs=style?Array.from(first(style,'cellXfs')?.children||[]):[];
 const dateStyles=xfs.map(x=>{const id=+x.getAttribute('numFmtId'),fmt=(formats[id]||'').replace(/"[^"]*"|\[[^\]]*\]|\\./g,'');return (id>=14&&id<=17)||id===22||(id>=27&&id<=36)||(id>=50&&id<=58)||/[yd]/i.test(fmt);});
 const date1904=['1','true'].includes(first(wb,'workbookPr')?.getAttribute('date1904'));
 const defs=tags(wb,'sheet');if(defs.length>LIMIT.sheets)throw Error('工作表超过24张，请拆分。');
 const sheets=[],warnings=[];let formulaCount=0,uncached=0,hidden=0;
 for(const sh of defs){
  if(sh.getAttribute('state')==='hidden'||sh.getAttribute('state')==='veryHidden'){hidden++;continue;}
  const name=sh.getAttribute('name'),rid=sh.getAttribute('r:id')||sh.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships','id');
  if(!relation[rid]){warnings.push(name+'：非普通工作表或外部关系，未读取。');continue;}
  const doc=await get(relation[rid]);if(!first(doc,'sheetData')){warnings.push(name+'：没有普通单元格区域，未读取。');continue;}
  const grid=[],lineNumbers=[];
  for(const row of tags(doc,'row')){
   const out=[];let populated=false;
   for(const c of Array.from(row.children).filter(c=>c.localName==='c')){
    const match=(c.getAttribute('r')||'').match(/^([A-Z]+)\d+$/);if(!match)continue;
    const i=colIndex(match[1]),t=c.getAttribute('t'),s=+(c.getAttribute('s')||0),vn=first(c,'v'),f=first(c,'f');
    let v=vn?.textContent??'';
    if(t==='s')v=shared[Number(v)]??'';
    else if(t==='inlineStr')v=tags(c,'t').map(t=>t.textContent).join('');
    else if(t==='b')v=v==='1'?'TRUE':'FALSE';
    else if(t==='e')v='#单元格错误:'+v;
    else if(v!==''&&t!=='str'&&t!=='d'){const n=Number(v);if(Number.isFinite(n)){const format=formats[+(xfs[s]?.getAttribute('numFmtId')||0)]||'';v=dateStyles[s]?E.serialDate(n,date1904):/^0{2,}$/.test(format)?String(n).padStart(format.length,'0'):n;}}
    if(f){formulaCount++;if(!vn||vn.textContent===''){uncached++;v='#无缓存公式';}}
    if(v!==''&&v!=null){if(i>=LIMIT.cols)throw Error(name+'含80列以外的数据，请精简后导入。');populated=true;out[i]=v;}
   }
   if(populated){grid.push(Array.from({length:out.length},(_,i)=>out[i]??''));lineNumbers.push(+(row.getAttribute('r')||grid.length));if(grid.length>LIMIT.rows+30)throw Error(name+'超过5000条数据，请拆分。');}
  }
  if(first(doc,'mergeCells'))warnings.push(name+'有合并单元格，仅读取左上角，不自动填充合并值。');
  if(grid.length)sheets.push({name,grid,lineNumbers});
 }
 if(formulaCount)warnings.push(`发现${formulaCount}个公式单元格：只读取保存的缓存，不重新计算；${uncached}个无缓存，标为不可计算。请先在表格软件重算并另存为值后导入。`);
 if(hidden)warnings.push(`未读取${hidden}张隐藏工作表。`);
 return {sheets,warnings,encoding:'XLSX'};
}
async function readFile(file){
 if(file.size>LIMIT.file)throw Error(file.name+'超过10MB，请先精简或分表。');
 const buf=await file.arrayBuffer();let data;
 if(/\.xlsx$/i.test(file.name)){try{data=await readXLSX(buf);}catch(e){throw Error('XLSX 导入失败：'+e.message+'（不支持加密文件、旧版 .xls、宏和复杂透视输出。）');}}
 else if(/\.(csv|tsv)$/i.test(file.name)){
  let s,encoding='UTF-8';const bytes=new Uint8Array(buf);
  if(bytes[0]===255&&bytes[1]===254){s=new TextDecoder('utf-16le').decode(buf);encoding='UTF-16LE';}
  else try{s=new TextDecoder('utf-8',{fatal:true}).decode(buf);}catch{try{s=new TextDecoder('gb18030',{fatal:true}).decode(buf);encoding='GB18030（自动回退，请核对中文）';}catch{throw Error('无法识别文本编码，请另存为 UTF-8 CSV。');}}
  data={sheets:[parseCSV(s,/\.tsv$/i.test(file.name)?'\t':undefined)],warnings:[],encoding};
 }else throw Error('支持 .xlsx、.csv、.tsv；旧 .xls 请先转换。');
 return {...data,fileName:file.name};
}
function dataset(sheet,headerAt=null){
 const grid=sheet.grid;if(!grid.length)throw Error('工作表为空。');
 if(headerAt==null){const ranked=grid.slice(0,20).map((r,i)=>({i,b:E.detect(r)[0],filled:r.filter(v=>E.text(v)).length}));ranked.sort((a,b)=>b.b.score-a.b.score||b.filled-a.filled||a.i-b.i);headerAt=ranked[0].i;}
 const maxcols=Math.max(...grid.map(r=>r.length));if(maxcols>LIMIT.cols)throw Error('超过80列。');
 const seen={};const headers=Array.from({length:maxcols},(_,i)=>{const v=E.text(grid[headerAt]?.[i])||'未命名列'+(i+1);seen[v]=(seen[v]||0)+1;return seen[v]>1?v+' [列'+(i+1)+']':v;});
 const rows=[],lineNumbers=[];let empty=0;
 for(let i=headerAt+1;i<grid.length;i++){const r=Array.from({length:maxcols},(_,k)=>grid[i][k]??'');if(r.some(v=>E.text(v))){rows.push(r);lineNumbers.push(sheet.lineNumbers?.[i]??i+1);}else empty++;}
 if(rows.length>LIMIT.rows)throw Error('单表超过5000条数据，请拆分。');
 return {headers,rows,lineNumbers,headerAt,headerSourceRow:sheet.lineNumbers?.[headerAt]??headerAt+1,emptyRows:empty};
}
async function writeXLSX(sheets){
 const z=new root.JSZip(),names=new Set();const safe=sheets.map((s,i)=>{let n=String(s.name||'Sheet'+(i+1)).replace(/[\[\]:*?\/\\]/g,'_').slice(0,31);while(names.has(n))n=n.slice(0,26)+'_'+i;names.add(n);return {...s,name:n};});
 const pre='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
 z.file('[Content_Types].xml',pre+'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+safe.map((s,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')+'</Types>');
 z.file('_rels/.rels',pre+'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>');
 z.file('xl/workbook.xml',pre+'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+safe.map((s,i)=>`<sheet name="${esc(s.name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')+'</sheets></workbook>');
 z.file('xl/_rels/workbook.xml.rels',pre+'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+safe.map((s,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')+`<Relationship Id="rId${safe.length+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`);
 z.file('xl/styles.xml',pre+'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF174C46"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"><alignment wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>');
 safe.forEach((sh,i)=>{
  const data=sh.rows.length?sh.rows:[['无记录']],cols=Math.max(...data.map(r=>r.length));
  if(data.some(r=>r.some(v=>typeof v==='string'&&v.length>32767)))throw Error('导出有单元格超过Excel文本长度限制，请减少数据范围或改用HTML/CSV。');
  let body=data.map((r,j)=>`<row r="${j+1}"${j===0?' ht="32" customHeight="1"':''}>`+r.map((v,k)=>{const a=colName(k)+(j+1),style=j===0?' s="1"':'';if(typeof v==='number'&&Number.isFinite(v))return `<c r="${a}"${style}><v>${v}</v></c>`;return `<c r="${a}" t="inlineStr"${style}><is><t xml:space="preserve">${esc(v)}</t></is></c>`;}).join('')+'</row>').join('');
  z.file(`xl/worksheets/sheet${i+1}.xml`,pre+'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'+`<cols><col min="1" max="${cols}" width="23" customWidth="1"/></cols><sheetData>${body}</sheetData>${data.length>1?`<autoFilter ref="A1:${colName(cols-1)}${data.length}"/>`:''}</worksheet>`);
 });
 return z.generateAsync({type:'blob',compression:'DEFLATE',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
function download(blob,name){if(!(blob instanceof Blob))blob=new Blob([blob],{type:'text/plain;charset=utf-8'});const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),10000);}
const api={LIMIT,esc,colName,colIndex,csvCell,csv,parseCSV,readFile,readXLSX,dataset,writeXLSX,download};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CareIO=api;
})(typeof globalThis!=='undefined'?globalThis:this);
