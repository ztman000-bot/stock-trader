// Android/slow-device scanner resilience layer.
// UI-only fallback: never places orders and never changes Control v0.8.0 strategy state.
(()=>{
  const $=s=>document.querySelector(s);
  const won=n=>'₩'+Math.round(Number(n||0)).toLocaleString('ko-KR');
  const pct=n=>(Number(n||0)>=0?'+':'')+Number(n||0).toFixed(2)+'%';
  let loading=false;

  function needsFallback(){
    const body=$('#scannerBody');
    if(!body)return false;
    const text=(body.textContent||'').trim();
    return !text || text.includes('안전 스캐너 초기화 중') || text.includes('스캐너 응답 대기');
  }

  function renderRows(d){
    const body=$('#scannerBody');
    if(!body)return;
    const rows=(d?.scanner||[]).slice(0,10);
    body.innerHTML=rows.map((s,i)=>{
      const x=s.indicators||{},m=s.market||{};
      const activity=m.activityScore!=null?`활성 ${Number(m.activityScore).toFixed(0)} · ${Number(m.turnoverEok||0).toFixed(0)}억`:'';
      return `<tr class="pick-row" data-code="${s.code}"><td>${i+1}</td><td><b>${s.name||s.code}</b><br><small>${s.code}</small></td><td>${x.price?won(x.price):'-'}</td><td>${m.changeRate!=null?pct(m.changeRate):'-'}</td><td><span class="score">${Number(s.score||0).toFixed(1)}</span><br><small>${(s.blockedReasons||[]).slice(0,2).join(' · ')||'서버 스캐너 정상'}</small><br><small class="neutral">${activity}</small></td><td>${x.vwap?won(x.vwap):'-'}</td><td>${x.volumeRatio?Number(x.volumeRatio).toFixed(2)+'x':'-'}</td><td>${s.action||'WATCH'}</td><td>서버 자동</td></tr>`;
    }).join('')||'<tr><td colspan="9" class="neutral">활성 후보 준비 중</td></tr>';

    const desc=document.querySelector('.main-grid article .panel-head p');
    if(desc){
      const u=d?.universe||{};
      desc.textContent=`공식 마스터 안전후보 ${u.selectedRows||0} · 활성 ${(d?.scanner||[]).length} · Top10 · Android fallback`;
    }
  }

  async function refresh(force=false){
    if(loading)return;
    if(!force&&!needsFallback())return;
    const body=$('#scannerBody');
    if(!body)return;
    loading=true;
    const ctl=new AbortController();
    const timer=setTimeout(()=>ctl.abort(),20000);
    try{
      if(needsFallback())body.innerHTML='<tr><td colspan="9" class="neutral">스캐너 응답 대기...</td></tr>';
      const r=await fetch('/api/mobile/status?u='+Date.now(),{cache:'no-store',signal:ctl.signal});
      const d=await r.json();
      if(!r.ok)throw new Error(d.detail||d.error||`HTTP ${r.status}`);
      renderRows(d);
      const badge=$('#systemBadge');
      if(badge&&d?.collector?.running){badge.textContent='SYSTEM RUNNING';badge.className='badge ok'}
    }catch(e){
      const msg=e?.name==='AbortError'?'20초 응답시간 초과':String(e?.message||e);
      body.innerHTML=`<tr><td colspan="9" class="down" style="text-align:left;white-space:normal">스캐너 표시 오류: ${msg}<br>서버 상태는 별도로 유지됩니다.</td></tr>`;
      console.warn('scanner resilience fallback failed',e);
    }finally{
      clearTimeout(timer);
      loading=false;
    }
  }

  function start(){
    setTimeout(()=>refresh(false),3500);
    const btn=$('#scanBtn');
    if(btn&&!btn.dataset.resilienceBound){
      btn.dataset.resilienceBound='1';
      btn.addEventListener('click',()=>setTimeout(()=>refresh(true),1200));
    }
    window.stockScannerFallbackRefresh=()=>refresh(true);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
})();
