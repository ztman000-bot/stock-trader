// Native Android client usability + read-only Shadow research visibility.
// The update action calls only the existing guarded Android updater; the Shadow
// monitor uses GET-only research endpoints and never touches broker/order APIs.
(()=>{
  const qs=new URLSearchParams(location.search);
  if(qs.get('native')!=='1')return;

  const style=document.createElement('style');
  style.id='nativeClientUsabilityFixes';
  style.textContent=`
    html.native-client,html.native-client body{min-height:100%;overflow-y:auto!important;-webkit-overflow-scrolling:touch}
    html.native-client body{padding-bottom:calc(126px + env(safe-area-inset-bottom))!important}
    html.native-client main.wrap{padding-bottom:calc(138px + env(safe-area-inset-bottom))!important}
    html.native-client .mobile-nav{padding-bottom:calc(8px + env(safe-area-inset-bottom))!important}
    html.native-client #nativeShadowPanel .panel-head{flex-wrap:wrap;align-items:center}
    html.native-client .native-shadow-actions{display:flex;gap:6px;align-items:center;margin-left:auto}
    html.native-client #nativeServerUpdateBtn{min-width:72px;white-space:nowrap}
    html.native-client #nativeServerUpdateStatus{margin-top:8px;font-size:11px;line-height:1.45}
    html.native-client #nativeShadowDeep{margin-top:10px;padding-top:10px;border-top:1px solid rgba(148,163,184,.18)}
    html.native-client .native-shadow-deep-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
    html.native-client .native-shadow-deep-head strong{font-size:13px}
    html.native-client .native-shadow-deep-head small{font-size:10px;opacity:.68}
    html.native-client .native-shadow-deep-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
    html.native-client .native-shadow-deep-card{background:#0c1423;border:1px solid rgba(148,163,184,.18);border-radius:9px;padding:8px;min-width:0}
    html.native-client .native-shadow-deep-card small{display:block;font-size:9px;opacity:.68}
    html.native-client .native-shadow-deep-card strong{display:block;margin-top:3px;font-size:14px;overflow:hidden;text-overflow:ellipsis}
    html.native-client .native-shadow-metric-note{margin-top:8px;font-size:10px;line-height:1.45;opacity:.72}
    html.native-client .native-shadow-recent{display:grid;gap:5px;margin-top:8px}
    html.native-client .native-shadow-recent-row{display:flex;justify-content:space-between;gap:8px;padding:7px 8px;border-radius:8px;background:#0c1423;border:1px solid rgba(148,163,184,.14);font-size:10px}
    html.native-client .native-shadow-recent-row b{font-size:11px}
    html.native-client main.wrap>section:last-child{margin-bottom:24px!important}
  `;
  document.head.appendChild(style);

  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const msgOf=(j,fallback='오류')=>j?.error||j?.detail||j?.message||fallback;
  const n=v=>Number.isFinite(Number(v))?Number(v):0;
  const pf=v=>n(v)>=999?'∞':n(v).toFixed(2);
  const pct=(v,d=2)=>`${n(v)>=0?'+':''}${n(v).toFixed(d)}%`;

  async function getJson(url,opts={}){
    const r=await fetch(url,{cache:'no-store',...opts});
    let j={};
    try{j=await r.json()}catch{}
    if(!r.ok){const e=new Error(msgOf(j,`HTTP ${r.status}`));e.payload=j;throw e}
    return j;
  }

  function setUpdateStatus(text,kind='info'){
    const box=document.querySelector('#nativeServerUpdateStatus');
    if(!box)return;
    box.textContent=text;
    box.dataset.kind=kind;
    box.style.color=kind==='ok'?'#5ee0a3':kind==='bad'?'#ff9a9a':'';
  }

  async function runServerUpdate(){
    const btn=document.querySelector('#nativeServerUpdateBtn');
    if(!btn||btn.disabled)return;
    if(!confirm('공기계 Stock Trader 서버를 GitHub 최신 main으로 안전 업데이트할까요?\n\n열린 Paper 포지션이나 안전검사 실패 시 자동 차단됩니다.'))return;

    btn.disabled=true;
    btn.textContent='확인 중';
    setUpdateStatus('현재 서버 상태 확인 중...');

    let beforePid=null;
    try{
      const live=await getJson(`/api/system/liveness?native_update_pre=${Date.now()}`);
      beforePid=Number(live?.pid||0)||null;
    }catch{}

    try{
      const r=await getJson('/api/system/update/run',{method:'POST',headers:{'Accept':'application/json'}});
      setUpdateStatus(r?.message||'업데이트 요청 성공 · 안전검사/테스트/백업을 진행합니다.');
      btn.textContent='업데이트 중';
    }catch(err){
      setUpdateStatus(`업데이트 차단/실패: ${err?.message||err}`,'bad');
      btn.disabled=false;
      btn.textContent='서버 업데이트';
      return;
    }

    let sawOffline=false;
    let lastError='';
    for(let i=0;i<75;i++){
      await sleep(2000);
      try{
        const live=await getJson(`/api/system/liveness?native_update_poll=${Date.now()}`);
        const pid=Number(live?.pid||0)||null;
        if(beforePid&&pid&&pid!==beforePid){
          setUpdateStatus('업데이트 완료 · 새 서버 프로세스 재시작 확인 ✓','ok');
          btn.textContent='완료';
          await sleep(900);
          location.reload();
          return;
        }
        if(sawOffline&&live?.ok){
          setUpdateStatus('서버 재연결 완료 · 최신 화면을 다시 불러옵니다.','ok');
          btn.textContent='완료';
          await sleep(900);
          location.reload();
          return;
        }
        try{
          const st=await getJson(`/api/system/update/status?native_update_status=${Date.now()}`);
          const u=st?.update||{};
          if(u?.lastError){
            lastError=String(u.lastError);
            break;
          }
          if(u?.running)setUpdateStatus('업데이트 진행 중 · 테스트/DB 백업/서버 재시작 대기...');
        }catch{}
      }catch{
        sawOffline=true;
        setUpdateStatus('서버 재시작 중 · 자동 재연결 대기...');
      }
    }

    if(lastError)setUpdateStatus(`업데이트 실패: ${lastError}`,'bad');
    else setUpdateStatus('업데이트 완료 여부 확인 시간이 초과됐습니다. 새로고침 후 다시 확인하세요.','bad');
    btn.disabled=false;
    btn.textContent='서버 업데이트';
  }

  function deepCard(label,value,detail=''){
    return `<div class="native-shadow-deep-card"><small>${label}</small><strong>${value}</strong>${detail?`<small style="margin-top:3px">${detail}</small>`:''}</div>`;
  }

  function flags(row){
    const out=[];
    if(n(row?.daily_lock_continuation))out.push('Lock 이후');
    if(n(row?.reentry_after_shadow_loss))out.push('손실후 재진입');
    if(n(row?.prior_control_loss))out.push('Control손실후');
    return out.join(' · ')||'일반 Shadow';
  }

  async function refreshDeepShadow(){
    const box=document.querySelector('#nativeShadowDeep');
    if(!box)return;
    const status=box.querySelector('#nativeShadowDeepState');
    try{
      const d=await getJson(`/api/research/shadow-continuation?limit=8&t=${Date.now()}`);
      const all=d?.allClosed||{};
      const today=d?.todayClosed||{};
      const re=d?.sameStockReentryAfterLoss||{};
      const lock=d?.dailyLockContinuation||{};
      const ctrl=d?.afterActualControlLoss||{};
      const study=d?.policyStudy||{};
      const open=n(d?.openPositions);
      const total=n(all.trades)+open;
      const target=n(study.minimumReentryLossSample)||50;
      const cur=n(study.currentReentryLossSample);
      if(status){
        status.textContent='연구 장부 연결됨 · 실제 주문 0';
        status.style.color='#5ee0a3';
      }
      const grid=box.querySelector('#nativeShadowDeepGrid');
      if(grid)grid.innerHTML=[
        deepCard('누적 Shadow 표본',`${total}건`,`종료 ${n(all.trades)} · OPEN ${open}`),
        deepCard('오늘 종료 표본',`${n(today.trades)}건`,`승률 ${n(today.winRate).toFixed(1)}%`),
        deepCard('Daily Lock 이후',`${n(lock.trades)}건`,`PF ${pf(lock.profitFactor)} · 기대 ${pct(lock.expectancyPct,3)}`),
        deepCard('손실 후 동일종목 재진입',`${cur} / ${target}`,study.readyForControlReview?'Control 검토 표본 도달':'아직 연구 수집 중'),
        deepCard('재진입 성과',`승률 ${n(re.winRate).toFixed(1)}%`,`PF ${pf(re.profitFactor)} · 기대 ${pct(re.expectancyPct,3)}`),
        deepCard('실제 Control 손실 이후',`${n(ctrl.trades)}건`,`승률 ${n(ctrl.winRate).toFixed(1)}% · PF ${pf(ctrl.profitFactor)}`),
      ].join('');
      const stats=box.querySelector('#nativeShadowDeepStats');
      if(stats)stats.textContent=`전체 종료 ${n(all.trades)}건 · 승률 ${n(all.winRate).toFixed(1)}% · PF ${pf(all.profitFactor)} · 기대값 ${pct(all.expectancyPct,3)} · 누적 정규화 ${pct(all.netNormalizedPct,2)}`;
      const recent=box.querySelector('#nativeShadowRecentTrades');
      if(recent){
        const rows=Array.isArray(d?.recent)?d.recent.slice(0,5):[];
        recent.innerHTML=rows.length?rows.map(r=>{
          const pnl=r?.pnl_pct==null?'OPEN':pct(r.pnl_pct,2);
          const name=String(r?.name||r?.code||'-');
          const state=String(r?.status||'-');
          return `<div class="native-shadow-recent-row"><div><b>${name}</b><br><span>${flags(r)}</span></div><div style="text-align:right"><b>${pnl}</b><br><span>${state}</span></div></div>`;
        }).join(''):'<div class="native-shadow-metric-note">아직 누적된 Shadow 거래가 없습니다.</div>';
      }
    }catch(err){
      if(status){
        status.textContent=`연구 장부 확인 실패: ${err?.message||err}`;
        status.style.color='#ff9a9a';
      }
    }
  }

  function installControls(){
    const panel=document.querySelector('#nativeShadowPanel');
    const detail=document.querySelector('#nativeUiModeBtn');
    const head=panel?.querySelector('.panel-head');
    if(!panel||!detail||!head)return false;

    let actions=head.querySelector('.native-shadow-actions');
    if(!actions){
      actions=document.createElement('div');
      actions.className='native-shadow-actions';
      head.appendChild(actions);
    }

    if(!document.querySelector('#nativeServerUpdateBtn')){
      const update=document.createElement('button');
      update.id='nativeServerUpdateBtn';
      update.type='button';
      update.className='btn secondary-action compact';
      update.textContent='서버 업데이트';
      update.addEventListener('click',runServerUpdate);
      actions.appendChild(update);
    }
    if(detail.parentElement!==actions)actions.appendChild(detail);

    if(!document.querySelector('#nativeServerUpdateStatus')){
      const status=document.createElement('div');
      status.id='nativeServerUpdateStatus';
      status.className='backtest-box';
      status.textContent='업데이트 버튼은 공기계 서버와 서버형 UI를 갱신합니다. 네이티브 셸 자체가 바뀔 때만 새 APK가 필요합니다.';
      head.insertAdjacentElement('afterend',status);
    }

    if(!document.querySelector('#nativeShadowDeep')){
      const deep=document.createElement('div');
      deep.id='nativeShadowDeep';
      deep.innerHTML=`
        <div class="native-shadow-deep-head"><strong>Shadow 누적 연구</strong><small id="nativeShadowDeepState">연구 장부 확인 중...</small></div>
        <div id="nativeShadowDeepGrid" class="native-shadow-deep-grid"></div>
        <div id="nativeShadowDeepStats" class="native-shadow-metric-note">누적 성과 계산 중...</div>
        <div id="nativeShadowRecentTrades" class="native-shadow-recent"></div>
        <div class="native-shadow-metric-note">연구용 정규화 수익률입니다. 실계좌 수익이 아니며 Control v0.8.0 규칙은 자동 변경되지 않습니다.</div>`;
      const list=panel.querySelector('#nativeShadowList');
      if(list)list.insertAdjacentElement('beforebegin',deep);else panel.appendChild(deep);
    }
    return true;
  }

  let shadowTimer=null;
  function bootNativeEnhancements(){
    if(!installControls())return false;
    refreshDeepShadow();
    if(!shadowTimer)shadowTimer=setInterval(refreshDeepShadow,30000);
    return true;
  }

  function start(){
    if(bootNativeEnhancements())return;
    const obs=new MutationObserver(()=>{if(bootNativeEnhancements())obs.disconnect()});
    obs.observe(document.documentElement,{subtree:true,childList:true});
    setTimeout(()=>obs.disconnect(),20000);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
