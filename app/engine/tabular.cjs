/* Append-only table validation. No workflow transitions and no trusted identities. */
'use strict';
const W=require('./workflow.js');
function prepare(ws,table,rows){
 W.assertStructure(ws);
 const s=W.C.R.byId[table];if(!s)throw Error('未知表类型');
 if(!Array.isArray(rows)||rows.length>1000)throw Error('每批最多1000行');
 const base=W.C.validate(ws).issues;
 const sig=x=>JSON.stringify([x.table,x.index,x.field,x.rule,x.message]);
 const previous=new Set(base.filter(x=>x.level==='error').map(sig));
 let candidates=rows.map(x=>({line:x.line,row:W.C.normalize(s,x.row)})), rejected=[],warnings=[];
 for(let pass=0;pass<12;pass++){
   const result=structuredClone(ws),start=result.tables[table].length;
   result.tables[table].push(...candidates.map(x=>x.row));
   const checks=W.C.validate(result).issues;
   const external=checks.filter(x=>x.level==='error'&&(x.table!==table||x.index<start)&&!previous.has(sig(x)));
   if(external.length)throw Error('本批新增会破坏已有记录关联；未提交任何行，请先核对来源。');
   const invalid=new Map();
   for(const x of checks){if(x.table===table&&x.index>=start){
     const at=x.index-start;
     if(x.level==='error'){if(!invalid.has(at))invalid.set(at,[]);invalid.get(at).push(x.message);}
   }}
   if(!invalid.size){
     W.assertStructure(result);
     warnings=checks.filter(x=>x.level==='warning'&&x.table===table&&x.index>=start).map(x=>({line:candidates[x.index-start].line,message:x.message}));
     return {accepted:candidates,rejected,warnings};
   }
   rejected.push(...[...invalid.entries()].map(([ix,errors])=>({line:candidates[ix].line,errors})));
   candidates=candidates.filter((_,ix)=>!invalid.has(ix));
 }
 throw Error('关联校验未稳定；请拆分来源，不自动猜测修复');
}
module.exports={prepare};
