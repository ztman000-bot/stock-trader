import {CONFIG} from './config.js';
if (!CONFIG.nh) CONFIG.nh = { backendBaseUrl: '' };
if (typeof CONFIG.nh.backendBaseUrl !== 'string') CONFIG.nh.backendBaseUrl = '';
const showBootError=err=>{console.error('Stock Day Trader boot error:',err);const body=document.querySelector('#scannerBody');if(body)body.innerHTML=`<tr><td colspan="9" class="down" style="text-align:left;white-space:normal">앱 로딩 오류: ${String(err?.message||err)}<br>페이지를 새로고침해 주세요.</td></tr>`;const badge=document.querySelector('#systemBadge');if(badge){badge.textContent='APP ERROR';badge.className='badge badbadge'}};
window.addEventListener('error',e=>showBootError(e.error||e.message));window.addEventListener('unhandledrejection',e=>showBootError(e.reason));

const ASSET_VERSION='1789226000';
const nativeClient=new URLSearchParams(location.search).get('native')==='1';
const afterFirstPaint=(fn,delay=250)=>{
  const run=()=>setTimeout(fn,delay);
  if(document.readyState==='complete'||document.readyState==='interactive')requestAnimationFrame(()=>requestAnimationFrame(run));
  else window.addEventListener('DOMContentLoaded',()=>requestAnimationFrame(()=>requestAnimationFrame(run)),{once:true});
};

if('serviceWorker' in navigator){
  navigator.serviceWorker.register(`/sw.js?v=${ASSET_VERSION}`,{updateViaCache:'none'}).catch(()=>{});
}

if(nativeClient){
  // Start compact layout from the server immediately instead of waiting for Android
  // WebView onPageFinished. The APK may inject the same file later; duplicate guards
  // inside the module make that harmless.
  import(`./native-compact-ui.js?v=${ASSET_VERSION}`).catch(showBootError);
  afterFirstPaint(()=>{
    import(`./native-client-fixes.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
      import(`./native-update-button-hotfix.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
        import(`./native-ops-us-visibility.js?v=${ASSET_VERSION}`).catch(showBootError);
      });
    });
  },120);
}

const liveClassic=location.pathname==='/classic'||location.pathname.startsWith('/classic/');
if(liveClassic){
  // Transport/cache layer must install before live-app makes its first status request.
  import(`./classic-fast-start.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
    import(`./ui-polish.js?v=${ASSET_VERSION}`).catch(showBootError);
    if(!nativeClient)import(`./pwa-install.js?v=${ASSET_VERSION}`).catch(showBootError);
    import(`./live-app.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
      import(`./version-display.js?v=${ASSET_VERSION}`).catch(showBootError);
      if(nativeClient)afterFirstPaint(()=>import(`./data-health-ui.js?v=${ASSET_VERSION}`).catch(showBootError),500);
      else import(`./data-health-ui.js?v=${ASSET_VERSION}`).catch(showBootError);
    });
    if(nativeClient){
      afterFirstPaint(()=>{
        import(`./trade-name-fix.js?v=${ASSET_VERSION}`).catch(showBootError);
        import(`./scanner-resilience.js?v=${ASSET_VERSION}`).catch(showBootError);
      },350);
    }else{
      import(`./trade-name-fix.js?v=${ASSET_VERSION}`).catch(showBootError);
      import(`./scanner-resilience.js?v=${ASSET_VERSION}`).catch(showBootError);
    }
  });
}else import(`./app.js?v=${ASSET_VERSION}`).catch(showBootError);
