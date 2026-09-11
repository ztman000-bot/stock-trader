import {CONFIG} from './config.js';
if (!CONFIG.nh) CONFIG.nh = { backendBaseUrl: '' };
if (typeof CONFIG.nh.backendBaseUrl !== 'string') CONFIG.nh.backendBaseUrl = '';
const showBootError=err=>{console.error('Stock Day Trader boot error:',err);const body=document.querySelector('#scannerBody');if(body)body.innerHTML=`<tr><td colspan="9" class="down" style="text-align:left;white-space:normal">앱 로딩 오류: ${String(err?.message||err)}<br>페이지를 새로고침해 주세요.</td></tr>`;const badge=document.querySelector('#systemBadge');if(badge){badge.textContent='APP ERROR';badge.className='badge badbadge'}};
window.addEventListener('error',e=>showBootError(e.error||e.message));window.addEventListener('unhandledrejection',e=>showBootError(e.reason));

const ASSET_VERSION='1789005000';
const nativeClient=new URLSearchParams(location.search).get('native')==='1';

if('serviceWorker' in navigator){
  navigator.serviceWorker.register(`/sw.js?v=${ASSET_VERSION}`,{updateViaCache:'none'}).catch(()=>{});
}

if(nativeClient){
  import('./native-client-fixes.js?v=1789137600').catch(showBootError);
}

const liveClassic=location.pathname==='/classic'||location.pathname.startsWith('/classic/');
if(liveClassic){
  // Install the transport/cache layer before live-app starts its first heavy status request.
  import(`./classic-fast-start.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
    import(`./ui-polish.js?v=${ASSET_VERSION}`).catch(showBootError);
    import(`./pwa-install.js?v=${ASSET_VERSION}`).catch(showBootError);
    import(`./live-app.js?v=${ASSET_VERSION}`).catch(showBootError).finally(()=>{
      import(`./version-display.js?v=${ASSET_VERSION}`).catch(showBootError);
      import(`./data-health-ui.js?v=${ASSET_VERSION}`).catch(showBootError);
    });
    import(`./trade-name-fix.js?v=${ASSET_VERSION}`).catch(showBootError);
    import(`./scanner-resilience.js?v=${ASSET_VERSION}`).catch(showBootError);
  });
}else import(`./app.js?v=${ASSET_VERSION}`).catch(showBootError);