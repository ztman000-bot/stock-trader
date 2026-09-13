import {CONFIG} from './config.js';
if (!CONFIG.nh) CONFIG.nh = { backendBaseUrl: '' };
if (typeof CONFIG.nh.backendBaseUrl !== 'string') CONFIG.nh.backendBaseUrl = '';
const showBootError=err=>{console.error('Stock Day Trader boot error:',err);const body=document.querySelector('#scannerBody');if(body)body.innerHTML=`<tr><td colspan="9" class="down" style="text-align:left;white-space:normal">앱 로딩 오류: ${String(err?.message||err)}<br>페이지를 새로고침해 주세요.</td></tr>`;const badge=document.querySelector('#systemBadge');if(badge){badge.textContent='APP ERROR';badge.className='badge badbadge'}};
window.addEventListener('error',e=>showBootError(e.error||e.message));window.addEventListener('unhandledrejection',e=>showBootError(e.reason));

const ASSET_VERSION='1789311000';
const nativeClient=new URLSearchParams(location.search).get('native')==='1';
const afterFirstPaint=(fn,delay=250)=>{
  const run=()=>setTimeout(fn,delay);
  if(document.readyState==='complete'||document.readyState==='interactive')requestAnimationFrame(()=>requestAnimationFrame(run));
  else window.addEventListener('DOMContentLoaded',()=>requestAnimationFrame(()=>requestAnimationFrame(run)),{once:true});
};
const importLater=(path,delay)=>afterFirstPaint(()=>import(`${path}?v=${ASSET_VERSION}`).catch(showBootError),delay);
const loadClassicScript=(path,id)=>new Promise((resolve,reject)=>{
  if(document.getElementById(id))return resolve();
  const s=document.createElement('script');s.id=id;s.src=`${path}?v=${ASSET_VERSION}`;s.defer=true;s.onload=()=>resolve();s.onerror=()=>reject(new Error(`${path} load failed`));document.head.appendChild(s);
});
let researchScriptsPromise=null;
const loadResearchScripts=()=>{
  if(researchScriptsPromise)return researchScriptsPromise;
  researchScriptsPromise=Promise.all([
    loadClassicScript('/js/history-ui.js','lazyHistoryUi'),
    loadClassicScript('/js/strategy-lab-ui.js','lazyStrategyLabUi'),
    loadClassicScript('/js/market-lab-ui.js','lazyMarketLabUi'),
    loadClassicScript('/js/final-results-ui.js','lazyFinalResultsUi'),
  ]).catch(err=>{researchScriptsPromise=null;showBootError(err);throw err});
  return researchScriptsPromise;
};

// Native APK does not need PWA/service-worker boot work. Skipping it avoids
// WebView startup contention and stale service-worker cache checks.
if(!nativeClient&&'serviceWorker' in navigator){
  navigator.serviceWorker.register(`/sw.js?v=${ASSET_VERSION}`,{updateViaCache:'none'}).catch(()=>{});
}

if(nativeClient){
  // Compact shell is the only native-specific module allowed to compete with
  // first paint. Operational/detail helpers are loaded after the dashboard is visible.
  import(`./native-compact-ui.js?v=${ASSET_VERSION}`).catch(showBootError);
  importLater('./native-client-fixes.js',700);
  importLater('./native-update-button-hotfix.js',900);
  importLater('./native-ops-us-visibility.js',1100);

  const activateResearch=()=>setTimeout(()=>loadResearchScripts().catch(()=>{}),80);
  document.addEventListener('click',e=>{
    if(e.target?.closest?.('.mobile-nav button[data-tab="learn"],#nativeResearchTabBtn'))activateResearch();
  },true);
  const observeResearchTab=()=>{
    if(!document.body)return setTimeout(observeResearchTab,120);
    const obs=new MutationObserver(ms=>{
      if(ms.some(m=>m.type==='attributes'&&m.attributeName==='data-mobile-tab')&&document.body.dataset.mobileTab==='learn')activateResearch();
    });
    obs.observe(document.body,{attributes:true,attributeFilter:['data-mobile-tab']});
    if(document.body.dataset.mobileTab==='learn')activateResearch();
    window.addEventListener('pagehide',()=>obs.disconnect(),{once:true});
  };
  observeResearchTab();
}else{
  // Browser/PWA keeps the full research workspace, but it no longer competes
  // with the shell's first paint.
  afterFirstPaint(()=>loadResearchScripts().catch(()=>{}),450);
}

const liveClassic=location.pathname==='/classic'||location.pathname.startsWith('/classic/');
if(liveClassic){
  // Install cached transport first, then start the dashboard immediately.
  import(`./classic-fast-start.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
    import(`./live-app.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
      if(nativeClient){
        importLater('./ui-polish.js',650);
        importLater('./version-display.js',850);
        importLater('./trade-name-fix.js',1200);
        importLater('./scanner-resilience.js',1400);
        importLater('./data-health-ui.js',2200);
      }else{
        import(`./ui-polish.js?v=${ASSET_VERSION}`).catch(showBootError);
        import(`./version-display.js?v=${ASSET_VERSION}`).catch(showBootError);
        import(`./data-health-ui.js?v=${ASSET_VERSION}`).catch(showBootError);
        import(`./trade-name-fix.js?v=${ASSET_VERSION}`).catch(showBootError);
        import(`./scanner-resilience.js?v=${ASSET_VERSION}`).catch(showBootError);
      }
    });
    if(!nativeClient)import(`./pwa-install.js?v=${ASSET_VERSION}`).catch(showBootError);
  });
}else import(`./app.js?v=${ASSET_VERSION}`).catch(showBootError);
