// v0.17.15 read-only Data Health panel.
// Research/operations visibility only; never sends orders or mutates Control.
const fmt=v=>v==null?'-':`${Number(v).toFixed(1)}%`;

async function getHealth(){
  const r=await fetch(`/api/research/data-health?u=${Date.now()}`,{cache:'no-store'});
  if(!r.ok)throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function ensurePanel(){
  let panel=document.querySelector('#dataHealthPanel');
  if(panel)return panel;
  const anchor=document.querySelector('.connection-panel');
  if(!anchor)return null;
  panel=document.createElement('section');
  panel.id='dataHealthPanel';
  panel.className='card panel';
  panel.style.marginTop='12px';
  panel.innerHTML=`
    <div class="panel-head"><div><h2>Data Health</h2><p>수집 완성도 · 자동복구 · 시점 스냅샷 · Exit Replay · DB</p></div><span id="dataHealthBadge" class="badge">CHECK</span></div>
    <div id="dataHealthSummary" class="backtest-box">데이터 상태 확인 중...</div>
    <div id="dataHealthGrid" class="strategy-grid" style="margin-top:10px"></div>
    <div id="dataHealthNote" class="backtest-box" style="margin-top:10px"></div>`;
  anchor.insertAdjacentElement('afterend',panel);
  return panel;
}

function componentBox(label,value,detail=''){
  return `<div><small>${label}</small><strong>${value}</strong>${detail?`<br><small>${detail}</small>`:''}</div>`;
}

function coverageValue(x){
  return x?.forwardAverageCoveragePct??x?.averageCoveragePct??null;
}

function coverageDetail(x){
  if(x?.forwardAverageCoveragePct!=null)return `Forward ${x.forwardDays||0}일 · 기준 ${x.forwardBaselineDate||'-'} · 과거 ${fmt(x.averageCoveragePct)}`;
  return `${(x?.incompleteDays||[]).length} incomplete day · Forward 기준 ${x?.forwardBaselineDate||'-'}`;
}

function repairText(q5){
  const r=q5?.lastAutoRepair||{};
  if(r.reason==='target-reached')return `AUTO REPAIR 목표 ${fmt(q5.autoRepairTargetPct)} 달성`;
  if(r.skipped)return `AUTO REPAIR 대기: ${r.reason||'-'} · 목표 ${fmt(q5.autoRepairTargetPct)}`;
  if(r.afterPct!=null)return `AUTO REPAIR ${fmt(r.beforePct)} → ${fmt(r.afterPct)} · ${r.attempted||0}건`;
  return `AUTO REPAIR 활성 · 목표 ${fmt(q5?.autoRepairTargetPct)}`;
}

function render(h){
  if(!ensurePanel())return;
  const badge=document.querySelector('#dataHealthBadge');
  const score=Number(h?.score||0);
  if(badge){
    badge.textContent=`${h?.grade||'UNKNOWN'} ${score.toFixed(1)}`;
    badge.className='badge '+(score>=90?'ok':score>=80?'':'badbadge');
  }
  const q5=h?.fiveMinuteOfficial||{};
  const q1=h?.oneMinute||{};
  const sc=h?.scannerSnapshotCoverage||{};
  const dc=h?.decisionSnapshotCoverage||{};
  const ex=h?.exitReplay||{};
  const db=h?.database||{};
  const summary=document.querySelector('#dataHealthSummary');
  if(summary)summary.innerHTML=`<b>DATA HEALTH ${score.toFixed(1)}%</b> · ${h?.grade||'-'} · Data Health v${h?.version||'-'}<br><small>수익성/실전승격 판정과 분리 · Control v0.8.0 LOCKED · REAL ORDER OFF</small>`;
  const grid=document.querySelector('#dataHealthGrid');
  if(grid)grid.innerHTML=[
    componentBox('5m Official GOOD',fmt(q5.officialGoodPct),`목표 ${fmt(q5.autoRepairTargetPct)} · GOOD ${q5?.researchCounts?.GOOD??'-'}`),
    componentBox('1m Complete',fmt(q1.completePct),`${Number(q1.completeBars||0).toLocaleString()} / ${Number(q1.bars||0).toLocaleString()}`),
    componentBox('Scanner Coverage',fmt(coverageValue(sc)),coverageDetail(sc)),
    componentBox('Decision Coverage',fmt(coverageValue(dc)),coverageDetail(dc)),
    componentBox('Exit Replay',ex.ready?'READY':'NOT READY',`${ex.replayableTrades||0} trades · coverage ${fmt(ex.replayCoveragePct)} · reason ${fmt(ex.actualReasonMatchPct)}`),
    componentBox('DB',db.ok?'HEALTHY':'CHECK',`WAL ${(Number(db.walBytes||0)/1048576).toFixed(1)} MB · Backup ${db.backupFresh?'FRESH':db.backupAgeHours==null?'N/A':'OLD'}`),
  ].join('');
  const forwardActive=(sc.forwardDays||0)>0||(dc.forwardDays||0)>0;
  const gaps=forwardActive
    ?[...(sc.forwardIncompleteDays||[]).map(x=>`Scanner ${x}`),...(dc.forwardIncompleteDays||[]).map(x=>`Decision ${x}`)]
    :[...(sc.incompleteDays||[]).map(x=>`Scanner ${x}`),...(dc.incompleteDays||[]).map(x=>`Decision ${x}`)];
  const note=document.querySelector('#dataHealthNote');
  if(note){
    const m=h?.monitor||{};
    const replay=m.lastReplayRepair;
    const replayRepair=replay?.repaired?.length?`Replay 1m 복구 ${replay.repaired.length}건`:'Replay 1m 복구 대기/불필요';
    const partial=m.last1mRepair;
    const partialRepair=partial?.repaired?.length?`PARTIAL 1m 복구 ${partial.repaired.length}건`:'PARTIAL 1m 정상/대기';
    note.textContent=`${repairText(q5)} · ${gaps.length?`Forward INCOMPLETE_DAY: ${gaps.slice(-4).join(' · ')}`:'Forward Snapshot 기준 통과/수집대기'} · ${replayRepair} · ${partialRepair} · Exit ${ex.reason||'-'}`;
  }
}

async function refresh(){
  try{render(await getHealth())}
  catch(e){
    ensurePanel();
    const s=document.querySelector('#dataHealthSummary');
    if(s)s.textContent='Data Health 확인 실패: '+(e?.message||e);
  }
}

// Data Health touches large research tables, so keep it deliberately low-frequency.
function start(){ensurePanel();setTimeout(refresh,5000);setInterval(refresh,300000)}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();