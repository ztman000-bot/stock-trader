// Native-only visibility for the server stale-while-revalidate dashboard cache.
// Presentation only: never mutates Paper/Control and never sends an order request.
(()=>{
  if(new URLSearchParams(location.search).get('native')!=='1')return;
  if(window.__stockTraderNativeServerCacheState)return;
  window.__stockTraderNativeServerCacheState=true;

  function apply(data){
    const cache=data?.mobileCache;
    if(!cache?.serverCache)return;
    const stale=Boolean(cache.stale||cache.refreshing);
    if(!stale)return;
    document.documentElement.dataset.fastStart='cached';
    const age=Math.max(0,Number(cache.ageSec||0));
    const badge=document.querySelector('#systemBadge');
    if(badge){
      badge.textContent='SNAPSHOT · SYNCING';
      badge.className='badge';
    }
    const status=document.querySelector('#nhStatusText');
    if(status)status.innerHTML=`<b>최근 서버 스냅샷 ${age.toFixed(0)}초 전</b> · 공기계 최신 상태 동기화 중 · 매매 엔진은 계속 실행`;
  }

  const schedule=data=>setTimeout(()=>apply(data||window.stockClassicFastStart?.latest),0);
  window.addEventListener('stocktrader:status-data',e=>schedule(e?.detail?.data));
  window.addEventListener('stocktrader:status-fresh',()=>schedule(window.stockClassicFastStart?.latest));
  schedule(window.stockClassicFastStart?.latest);
})();
