import {CONFIG} from './config.js';

// v0.3→v0.4 전환 중 모바일 PWA/Service Worker가 이전 config.js를 잠시 보유해도
// 앱 전체가 중단되지 않도록 NH 설정을 안전하게 보정한다.
if (!CONFIG.nh) CONFIG.nh = { backendBaseUrl: '' };
if (typeof CONFIG.nh.backendBaseUrl !== 'string') CONFIG.nh.backendBaseUrl = '';

const showBootError = (err) => {
  console.error('Stock Day Trader boot error:', err);
  const body = document.querySelector('#scannerBody');
  if (body) body.innerHTML = `<tr><td colspan="9" class="down" style="text-align:left;white-space:normal">앱 로딩 오류: ${String(err?.message || err)}<br>페이지를 새로고침해 주세요. 문제가 계속되면 브라우저 사이트 데이터를 삭제 후 다시 접속하세요.</td></tr>`;
  const badge = document.querySelector('#systemBadge');
  if (badge) { badge.textContent = 'APP ERROR'; badge.className = 'badge badbadge'; }
};

window.addEventListener('error', e => showBootError(e.error || e.message));
window.addEventListener('unhandledrejection', e => showBootError(e.reason));

import('./app.js').catch(showBootError);
