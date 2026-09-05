// Android/slow-device scanner resilience layer.
// Passive UI fallback only: never duplicates the heavy /api/mobile/status request.
(()=>{
  const $=s=>document.querySelector(s);

  function needsFallback(){
    const body=$('#scannerBody');
    if(!body)return false;
    const text=(body.textContent||'').trim();
    return !text||text.includes('안전 스캐너 초기화 중')||text.includes('스캐너 응답 대기')||text.includes('서버 스캐너 계산 중');
  }

  function showProgress(){
    const body=$('#scannerBody');
    if(!body||!needsFallback())return;
    const fast=window.stockClassicFastStart;
    if(fast?.statusPending){
      body.innerHTML='<tr><td colspan="9" class="neutral">서버 스캐너 계산 중... · 이전 요청과 중복 실행하지 않습니다.</td></tr>';
    }else{
      fast?.restore?.();
      if(needsFallback())body.innerHTML='<tr><td colspan="9" class="neutral">스캐너 최신값 동기화 대기...</td></tr>';
    }
  }

  function start(){
    setTimeout(showProgress,3500);
    setTimeout(showProgress,9000);
    // Manual recovery only reuses the local fast-start cache. The normal
    // live-app scan button owns network refresh so one tap cannot create two scans.
    window.stockScannerFallbackRefresh=()=>{window.stockClassicFastStart?.restore?.();showProgress()};
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
})();
