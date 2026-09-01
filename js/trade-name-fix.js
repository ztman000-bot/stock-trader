// v0.17.3 hotfix: enrich Paper trade rows with Korean stock names.
// First use the full collector watchlist. Only after market close, unresolved old codes may use one NH quote lookup and are cached locally.
(()=>{
const STORE='stockTrader.tradeNames.v1';
let names=new Map(),loading=false,lastLoad=0,fallbackBusy=false;
try{const saved=JSON.parse(localStorage.getItem(STORE)||'{}');for(const [c,n] of Object.entries(saved))if(/^\d{6}$/.test(c)&&n)names.set(c,n)}catch{}
function persist(){try{localStorage.setItem(STORE,JSON.stringify(Object.fromEntries(names)))}catch{}}
function outputName(d){
 const roots=[d?.data?.Output_0,d?.data?.output_0,d?.Output_0,d?.output_0,d?.data];
 for(const q of roots){if(q&&typeof q==='object'){const n=String(q.iem_nm||q.hts_kor_isnm||q.prdt_name||q.name||'').trim();if(n)return n}}
 return '';
}
function unresolvedCodes(){
 const body=document.querySelector('#tradesBody');if(!body)return [];
 const out=[];
 for(const tr of body.querySelectorAll('tr')){
  const td=tr.children?.[1];if(!td)continue;
  const text=(td.textContent||'').trim();const m=text.match(/(?:^|\s)(\d{6})(?:$|\s)/)||text.match(/^(\d{6})$/);
  if(m&&!names.get(m[1]))out.push(m[1]);
 }
 return [...new Set(out)];
}
async function fallbackOldNames(){
 if(fallbackBusy)return;const codes=unresolvedCodes();if(!codes.length)return;
 fallbackBusy=true;
 try{
  let live=true;
  try{const h=await fetch('/api/system/runtime-health',{cache:'no-store'});const j=await h.json();live=!!j.liveSession}catch{return}
  if(live)return;
  for(const code of codes.slice(0,12)){
   try{
    const r=await fetch('/api/nh/quote/'+encodeURIComponent(code),{cache:'no-store'});const d=await r.json();
    if(!r.ok)continue;const n=outputName(d);if(n&&n!==code){names.set(code,n);persist();applyNames()}
   }catch{}
  }
 }finally{fallbackBusy=false}
}
async function loadNames(force=false){
 const now=Date.now();if(loading||(!force&&now-lastLoad<60000))return;loading=true;
 try{
  const r=await fetch('/api/market/latest',{cache:'no-store'}),d=await r.json();
  if(!r.ok)throw new Error(d.detail||d.error||`HTTP ${r.status}`);
  for(const q of (d.rows||[])){
   const code=String(q.code||'').trim(),name=String(q.name||'').trim();
   if(/^\d{6}$/.test(code)&&name&&name!==code)names.set(code,name);
  }
  persist();lastLoad=Date.now();
 }catch(e){console.warn('trade-name map load failed',e)}
 finally{loading=false;applyNames();setTimeout(fallbackOldNames,300)}
}
function applyNames(){
 const body=document.querySelector('#tradesBody');if(!body)return;
 for(const tr of body.querySelectorAll('tr')){
  const td=tr.children?.[1];if(!td)continue;
  const text=(td.textContent||'').trim();const m=text.match(/(?:^|\s)(\d{6})(?:$|\s)/)||text.match(/^(\d{6})$/);
  if(!m)continue;const code=m[1],name=names.get(code);if(!name)continue;
  const wanted=`${name} ${code}`;if(text!==wanted)td.textContent=wanted;
 }
}
function start(){
 loadNames(true);const body=document.querySelector('#tradesBody');
 if(body)new MutationObserver(()=>{applyNames();loadNames(false)}).observe(body,{childList:true,subtree:true,characterData:true});
 setInterval(()=>loadNames(false),60000);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(start,800));else setTimeout(start,800);
})();
