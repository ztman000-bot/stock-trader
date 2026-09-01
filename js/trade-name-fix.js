// v0.17.3 hotfix: enrich Paper trade rows with Korean stock names from the full collector watchlist.
(()=>{
let names=new Map(),loading=false,lastLoad=0;
async function loadNames(force=false){
  const now=Date.now();
  if(loading||(!force&&now-lastLoad<60000))return;
  loading=true;
  try{
    const r=await fetch('/api/market/latest',{cache:'no-store'});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||`HTTP ${r.status}`);
    for(const q of (d.rows||[])){
      const code=String(q.code||'').trim();
      const name=String(q.name||'').trim();
      if(/^\d{6}$/.test(code)&&name&&name!==code)names.set(code,name);
    }
    lastLoad=Date.now();
  }catch(e){console.warn('trade-name map load failed',e)}
  finally{loading=false;applyNames()}
}
function applyNames(){
  const body=document.querySelector('#tradesBody');
  if(!body)return;
  for(const tr of body.querySelectorAll('tr')){
    const td=tr.children?.[1];
    if(!td)continue;
    const text=(td.textContent||'').trim();
    const m=text.match(/(?:^|\s)(\d{6})(?:$|\s)/)||text.match(/^(\d{6})$/);
    if(!m)continue;
    const code=m[1],name=names.get(code);
    if(!name)continue;
    const wanted=`${name} ${code}`;
    if(text!==wanted)td.textContent=wanted;
  }
}
function start(){
  loadNames(true);
  const body=document.querySelector('#tradesBody');
  if(body)new MutationObserver(()=>{applyNames();loadNames(false)}).observe(body,{childList:true,subtree:true,characterData:true});
  setInterval(()=>loadNames(false),60000);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(start,800));else setTimeout(start,800);
})();
