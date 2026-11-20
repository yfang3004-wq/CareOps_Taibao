/* Adapter for the v0.1 interchange parser; new workflow logic lives in core.js. */
(function(root){'use strict';const text=v=>String(v??'').trim();
function serialDate(n,date1904=false){n=Number(n);if(!Number.isFinite(n))return '';const epoch=Date.UTC(date1904?1904:1899,date1904?0:11,date1904?1:30);const d=new Date(epoch+Math.round(n*86400000));const iso=d.toISOString();return Math.abs(n-Math.round(n))<1e-9?iso.slice(0,10):iso.slice(0,19).replace('T',' ');}
const api={text,serialDate,detect:()=>null};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CareEngine=api;})(globalThis);
