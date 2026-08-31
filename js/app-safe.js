import {CONFIG} from './config.js';

if (!CONFIG.nh) CONFIG.nh = { backendBaseUrl: '' };
if (typeof CONFIG.nh.backendBaseUrl !== 'string') CONFIG.nh.backendBaseUrl = '';

const showBootError = (err) => {
  console.error('Stock Day Trader boot error:', err);
  const body = document.querySelector('#scannerBody');
  if (body) body.innerHTML = `<tr><td colspan="9" class="down" style="text-align:left;white-space:normal">앱 로딩 오류: ${String(err?.message || err)}<br>페이지를 새로고침해 주세요.</td></tr>`;
  const badge = document.querySelector('#systemBadge');
  if (badge) { badge.textContent = 'APP ERROR'; badge.className = 'badge badbadge'; }
};

window.addEventListener('error', e => showBootError(e.error || e.message));
window.addEventListener('unhandledrejection', e => showBootError(e.reason));

const liveClassic = location.pathname === '/classic' || location.pathname.startsWith('/classic/');
if (liveClassic) {
  import('./ui-polish.js?v=110').catch(()=>{});
  import('./live-app.js?v=110').catch(showBootError);
} else {
  import('./app.js').catch(showBootError);
}
