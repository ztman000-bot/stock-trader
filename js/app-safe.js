import {CONFIG} from './config.js';
if (!CONFIG.nh) CONFIG.nh = { backendBaseUrl: '' };
if (typeof CONFIG.nh.backendBaseUrl !== 'string') CONFIG.nh.backendBaseUrl = '';
const showBootError=err=>{console.error('Stock Day Trader boot error:',err);const body=document.querySelector('#scannerBody');if(body)body.innerHTML=`<tr><td colspan="9" class="down" style="text-align:left;white-space:normal">앱 로딩 오류: ${String(err?.message||err)}<br>페이지를 새로고침해 주세요.</td></tr>`;const badge=document.querySelector('#systemBadge');if(badge){badge.textContent='APP ERROR';badge.className='badge badbadge'}};
window.addEventListener('error',e=>showBootError(e.error||e.message));window.addEventListener('unhandledrejection',e=>showBootError(e.reason));

// Register immediately instead of waiting for the window load event. The UI
// does not wait for this promise; the worker prepares the next fast launch in
// the background. updateViaCache:none prevents a stale worker script.
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js?v=1771',{updateViaCache:'none'}).catch(()=>{});
}

const liveClassic=location.pathname==='/classic'||location.pathname.startsWith('/classic/');
if(liveClassic){import('./ui-polish.js?v=1771').catch(showBootError);import('./live-app.js?v=1771').catch(showBootError);import('./trade-name-fix.js?v=1771').catch(showBootError)}else import('./app.js?v=1771').catch(showBootError);
