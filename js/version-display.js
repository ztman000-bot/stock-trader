// Release/version display for Classic UI.
// Keeps release, UI, reliability, and locked Control versions distinct.
const FALLBACK_RELEASE_VERSION='0.17.14';
const FALLBACK_UI_VERSION='0.17.10';
const CONTROL_VERSION='0.8.0';

async function getJson(path){
  const r=await fetch(path,{cache:'no-store'});
  if(!r.ok)throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function readVersions(){
  let releaseVersion=FALLBACK_RELEASE_VERSION;
  let uiVersion=FALLBACK_UI_VERSION;
  let reliabilityVersion='';

  try{
    const status=await getJson('/api/system/update/status');
    if(status?.releaseVersion)releaseVersion=String(status.releaseVersion);
    if(status?.uiVersion)uiVersion=String(status.uiVersion);
  }catch{}

  try{
    const live=await getJson('/api/system/liveness');
    if(live?.reliabilityVersion)reliabilityVersion=String(live.reliabilityVersion);
  }catch{}

  return {
    releaseVersion,
    uiVersion,
    reliabilityVersion:reliabilityVersion||'N/A',
  };
}

function applyVersionDisplay(v){
  const brand=document.querySelector('.brand');
  if(brand)brand.innerHTML=`Stock Day Trader <span>v${v.releaseVersion} RELEASE</span>`;

  const sub=document.querySelector('.sub');
  if(sub)sub.textContent=`UI v${v.uiVersion} · Reliability ${v.reliabilityVersion==='N/A'?'N/A':`v${v.reliabilityVersion}`} · Control v${CONTROL_VERSION} LOCKED · REAL ORDER OFF`;

  const footer=document.querySelector('footer');
  if(footer)footer.textContent=`Stock Day Trader v${v.releaseVersion} RELEASE · UI v${v.uiVersion} · Reliability ${v.reliabilityVersion==='N/A'?'N/A':`v${v.reliabilityVersion}`} · Control v${CONTROL_VERSION} LOCKED · REAL ORDER OFF`;

  document.title=`Stock Day Trader v${v.releaseVersion}`;
}

async function refreshVersionDisplay(){
  try{
    applyVersionDisplay(await readVersions());
  }catch{
    applyVersionDisplay({
      releaseVersion:FALLBACK_RELEASE_VERSION,
      uiVersion:FALLBACK_UI_VERSION,
      reliabilityVersion:'N/A',
    });
  }
}

function start(){
  refreshVersionDisplay();
  // live-app sets its legacy brand asynchronously; re-apply after startup races.
  setTimeout(refreshVersionDisplay,800);
  setTimeout(refreshVersionDisplay,2500);
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
else start();