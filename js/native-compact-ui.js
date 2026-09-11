// Native Android client compact monitor + Shadow visibility.
// Read-only UX: only GETs existing local/Tailscale API endpoints and never mutates Paper/Control.
(()=>{
  const qs=new URLSearchParams(location.search);
  if(qs.get('native')!=='1')return;

  const root=document.documentElement;
  root.classList.add('native-client','native-compact');

  const style=document.createElement('style');
  style.id='nativeCompactStyle';
  style.textContent=`
    html.native-compact .connection-panel,
    html.native-compact #pwaInstallStatus,
    html.native-compact footer{display:none!important}
    html.native-compact .topbar .sub{display:none!important}
    html.native-compact .topbar{padding-top:8px!important;padding-bottom:8px!important}
    html.native-compact .main-grid aside .riskbox,
    html.native-compact .main-grid aside #killBtn,
    html.native-compact .main-grid aside #resetBtn{display:none!important}
    html.native-compact .main-grid aside .panel-head p{display:none!important}
    html.native-client #nativeShadowPanel{margin-top:12px}
    html.native-client .native-shadow-summary{line-height:1.55}
    html.native-client .native-shadow-list{margin-top:10px;display:grid;gap:7px}
    html.native-client .native-shadow-row{display:flex;justify-content:space-between;gap:10px;padding:8px 10px;border:1px solid rgba(148,163,184,.18);border-radius:10px}
    html.native-client .native-shadow-row small{opacity:.72}
    html.native-client .native-shadow-tag{font-size:11px;font-weight:700;white-space:nowrap}
    html.native-client .native-detail-note{margin-top:8px;font-size:12px;opacity:.72;line-height:1.5}
    html.native-client #nativeUiModeBtn{min-width:74px}
    html.native-client #nativeShadowRecent[hidden]{display:none!important}

    /* Information architecture v2: each bottom tab has one job. */
    body[data-mobile-tab="home"] #nativeShadowPanel,
    body[data-mobile-tab="home"] #dataHealthPanel{display:block!important}
    body[data-mobile-tab="home"] #nativeShadowGrid,
    body[data-mobile-tab="home"] #nativeShadowList,
    body[data-mobile-tab="home"] #nativeShadowDeep,
    body[data-mobile-tab="home"] #nativeShadowPanel .native-detail-note,
    body[data-mobile-tab="home"] #dataHealthGrid,
    body[data-mobile-tab="home"] #dataHealthNote{display:none!important}
    body[data-mobile-tab="home"] #nativeResearchTabBtn{display:inline-flex!important}

    body[data-mobile-tab="top"] #nativeShadowPanel,
    body[data-mobile-tab="top"] #dataHealthPanel{display:none!important}

    body[data-mobile-tab="chart"] #nativeShadowPanel,
    body[data-mobile-tab="chart"] #dataHealthPanel{display:none!important}
    body[data-mobile-tab="chart"] .native-trade-grid{display:grid!important}
    body[data-mobile-tab="chart"] .native-trade-grid>[data-native-role="research"]{display:none!important}
    body[data-mobile-tab="chart"] .native-paper-history{display:block!important}

    body[data-mobile-tab="learn"] #nativeShadowPanel,
    body[data-mobile-tab="learn"] #dataHealthPanel{display:block!important}
    html.native-compact body[data-mobile-tab="learn"] #dataHealthGrid{display:grid!important}
    html.native-compact body[data-mobile-tab="learn"] #dataHealthNote{display:block!important}
    body[data-mobile-tab="learn"] #nativeShadowGrid{display:grid!important}
    body[data-mobile-tab="learn"] #nativeShadowList{display:grid!important}
    body[data-mobile-tab="learn"] #nativeShadowDeep{display:block!important}
    body[data-mobile-tab="learn"] #nativeShadowPanel .native-detail-note{display:block!important}
    body[data-mobile-tab="learn"] .native-trade-grid>[data-native-role="trade"]{display:none!important}
    body[data-mobile-tab="learn"] .native-paper-history{display:none!important}
    body[data-mobile-tab="learn"] #nativeResearchTabBtn{display:none!important}

    /* Old global 상세 보기 is replaced by clear tab roles. */
    #nativeUiModeBtn{display:none!important}
    body[data-mobile-tab="home"] #nativeShadowPanel .panel-head,
    body[data-mobile-tab="home"] #dataHealthPanel .panel-head{margin-bottom:7px}
    body[data-mobile-tab="home"] #nativeShadowSummary,
    body[data-mobile-tab="home"] #dataHealthSummary{font-size:11px;line-height:1.45}
    #nativeResearchTabBtn{white-space:nowrap}
  `;
  document.head.appendChild(style);

  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[c]));
  const num=(v,d=1)=>Number.isFinite(Number(v))?Number(v).toFixed(d):'-';
  const won=v=>Number.isFinite(Number(v))?`${Number(v)>=0?'+':''}₩${Math.round(Number(v)).toLocaleString()}`:'-';

  function setDetailed(on){
    root.classList.toggle('native-compact',!on);
    const b=document.querySelector('#nativeUiModeBtn');
    if(b)b.textContent=on?'간편 보기':'상세 보기';
    try{localStorage.setItem('stock-trader-native-detail',on?'1':'0')}catch{}
  }

  function switchTab(tab){
    document.body.dataset.mobileTab=tab;
    try{localStorage.setItem('daytrader-mobile-tab',tab)}catch{}
    document.querySelectorAll('.mobile-nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
    try{scrollTo({top:0,behavior:'instant'})}catch{scrollTo(0,0)}
  }

  function ensurePanel(){
    let panel=document.querySelector('#nativeShadowPanel');
    if(panel)return panel;
    const main=document.querySelector('main.wrap');
    if(!main)return null;
    panel=document.createElement('section');
    panel.id='nativeShadowPanel';
    panel.className='card panel';
    panel.innerHTML=`
      <div class="panel-head">
        <div><h2>Shadow 연구 모니터</h2><p>실제 주문 아님 · Control v0.8.0 변경 없음</p></div>
        <button id="nativeUiModeBtn" class="btn ghost compact" type="button">상세 보기</button>
      </div>
      <div id="nativeShadowSummary" class="backtest-box native-shadow-summary">Shadow 상태 확인 중...</div>
      <div id="nativeShadowGrid" class="strategy-grid" style="margin-top:10px"></div>
      <div id="nativeShadowList" class="native-shadow-list"></div>
      <div class="native-detail-note">손실/일손실/거래횟수 Lock으로 Paper 진입이 막혀도 조건을 만족한 신호는 SHADOW_ONLY로 관찰합니다. 별도 Same-stock Reentry Shadow 연구는 같은 종목 손실 후 재진입이 실제로 유리한지 판단하기 위해 백그라운드에서 표본을 계속 축적합니다.</div>`;
    const metrics=main.querySelector('.metrics');
    if(metrics)metrics.insertAdjacentElement('afterend',panel);
    else main.prepend(panel);
    panel.querySelector('#nativeUiModeBtn')?.addEventListener('click',()=>setDetailed(root.classList.contains('native-compact')));
    return panel;
  }

  function classifySections(){
    const positions=document.querySelector('#positionsBody')?.closest('article');
    const risk=document.querySelector('#riskState')?.closest('article');
    const grid=positions?.closest('.lower-grid');
    if(grid)grid.classList.add('native-trade-grid');
    if(positions)positions.dataset.nativeRole='trade';
    if(risk)risk.dataset.nativeRole='research';
    const trades=document.querySelector('#tradesBody')?.closest('section');
    if(trades)trades.classList.add('native-paper-history');
  }

  function tuneNavigation(){
    const learn=document.querySelector('.mobile-nav button[data-tab="learn"]');
    if(learn&&learn.dataset.iaV2!=='1'){
      learn.dataset.iaV2='1';
      const icon=learn.querySelector('b')?.outerHTML||'<b>▣</b>';
      learn.innerHTML=`${icon}연구·학습`;
    }
  }

  function ensureResearchShortcut(){
    const panel=ensurePanel();
    if(!panel)return;
    let actions=panel.querySelector('.native-shadow-actions');
    if(!actions){
      actions=document.createElement('div');
      actions.className='native-shadow-actions';
      panel.querySelector('.panel-head')?.appendChild(actions);
    }
    if(actions&&!document.querySelector('#nativeResearchTabBtn')){
      const btn=document.createElement('button');
      btn.id='nativeResearchTabBtn';
      btn.type='button';
      btn.className='btn ghost compact';
      btn.textContent='연구 보기';
      btn.addEventListener('click',()=>switchTab('learn'));
      actions.appendChild(btn);
    }
  }

  function lockReason(d){
    const out=[];
    if(Number(d?.consecutiveLosses||0)>=2)out.push('2연속 손실');
    if(d?.lossLimitHit)out.push('일손실 한도');
    if(Number(d?.closedTrades||0)>=Number(d?.maxDailyTrades||8))out.push('일 최대 거래수');
    return out.length?out.join(' · '):'Paper Gate 정상';
  }

  function box(label,value,detail=''){
    return `<div><small>${esc(label)}</small><strong>${esc(value)}</strong>${detail?`<br><small>${esc(detail)}</small>`:''}</div>`;
  }

  async function getJson(url){
    const r=await fetch(url,{cache:'no-store'});
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function refreshShadow(){
    const panel=ensurePanel();
    if(!panel)return;
    try{
      const [scan,mobile]=await Promise.all([
        getJson(`/api/paper/scan?native_shadow=${Date.now()}`),
        getJson(`/api/mobile/status?native_shadow=${Date.now()}`),
      ]);
      const rows=Array.isArray(scan?.rows)?scan.rows:[];
      const shadow=rows.filter(x=>x?.action==='SHADOW_ONLY');
      const daily=scan?.daily||mobile?.daily||rows[0]?.daily||{};
      const loop=mobile?.paperLoop||{};
      const locked=Boolean(daily?.locked);
      const summary=panel.querySelector('#nativeShadowSummary');
      if(summary){
        summary.innerHTML=`<b>${locked?'PAPER LOCK → SHADOW 관찰 중':'PAPER GATE 정상'}</b> · ${esc(lockReason(daily))}<br><small>현재 SHADOW_ONLY ${shadow.length}종목 · 실제 주문 0 · 자동 Control 변경 0</small>`;
      }
      const grid=panel.querySelector('#nativeShadowGrid');
      if(grid)grid.innerHTML=[
        box('현재 Shadow 후보',`${shadow.length}종목`,locked?'BUY 조건 충족 신호를 가상 기록':'현재 Lock 전환 없음'),
        box('Shadow 신호 기록',`${Number(loop.shadowSignals||0)}건`,'현재 서버 프로세스 누적'),
        box('연속 손실',`${Number(daily.consecutiveLosses||0)} / 2`,daily.locked?'Lock 판정 반영':'정상'),
        box('오늘 Paper 손익',won(daily.pnl),`${Number(daily.closedTrades||0)}건 종료`),
        box('동일종목 재진입 연구','수집 중','손실 후 재진입 성과 비교'),
        box('Control','v0.8.0 LOCKED','REAL ORDER OFF'),
      ].join('');
      const list=panel.querySelector('#nativeShadowList');
      if(list){
        if(!shadow.length){
          list.innerHTML='<div class="backtest-box">현재 SHADOW_ONLY 종목은 없습니다. Daily Lock이 발생한 뒤에도 BUY 조건이 다시 나오면 이곳에 종목·점수·판정 이유가 표시됩니다.</div>';
        }else{
          list.innerHTML=shadow.slice(0,5).map(x=>{
            const why=(x?.blockedReasons||[]).join(' · ')||lockReason(daily);
            return `<div class="native-shadow-row"><div><strong>${esc(x?.name||x?.code||'-')}</strong> <small>${esc(x?.code||'')}</small><br><small>${esc(why)}</small></div><div class="native-shadow-tag">SHADOW · ${num(x?.score,1)}</div></div>`;
          }).join('');
        }
      }
    }catch(err){
      const s=panel.querySelector('#nativeShadowSummary');
      if(s)s.textContent=`Shadow 상태 확인 실패: ${err?.message||err}`;
    }
  }

  function organize(){
    root.classList.add('native-compact');
    try{localStorage.setItem('stock-trader-native-detail','0')}catch{}
    classifySections();
    tuneNavigation();
    ensureResearchShortcut();
  }

  function start(){
    ensurePanel();
    setDetailed(false);
    organize();
    const obs=new MutationObserver(organize);
    obs.observe(document.documentElement,{childList:true,subtree:true});
    setTimeout(()=>obs.disconnect(),25000);
    refreshShadow();
    setInterval(refreshShadow,30000);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
