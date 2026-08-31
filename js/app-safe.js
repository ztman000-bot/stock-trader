import {CONFIG} from './config.js';

// v0.3→v0.4 전환 중 모바일 PWA/Service Worker가 이전 config.js를 잠시 보유해도
// 앱 전체가 중단되지 않도록 NH 설정을 안전하게 보정한다.
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

// Lenovo 서버의 /classic 경로에서는 기존 Day Trader 화면을 그대로 사용하되
// 데이터/상태는 실제 NH + 서버 Paper 엔진에 연결한다. GitHub Pages에서는
// 기존 mock/demo 앱을 유지해 HTTPS→로컬 HTTP 혼합 콘텐츠 문제를 피한다.
const liveClassic = location.pathname === '/classic' || location.pathname.startsWith('/classic/');
(liveClassic ? import('./live-app.js') : import('./app.js')).catch(showBootError);
