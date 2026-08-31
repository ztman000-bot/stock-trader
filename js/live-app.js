// Day Trader LIVE v0.11.0 - NH master safety filter + broad activity scanner + ORB30 Paper engine.
const $=s=>document.querySelector(s);
const won=n=>'₩'+Math.round(Number(n||0)).toLocaleString('ko-KR');
const pct=n=>(Number(n||0)>=0?'+':'')+Number(n||0).toFixed(2)+'%';
let selectedCode='005930',lastData=null,updating=false,scanning=false,uiVersion='0.11.0';

function log(msg){const box=$('#logBox');if(!box)return;const d=document.createElement('div');d.className='logline';d.innerHTML=`<span>${new Date().toLocaleTimeString('ko-KR',{hour12:false})}</span> ${msg}`;box.prepend(d);while(box.children.length>60)box.lastChild.remove()}
function actionClass(a){return a==='BUY_CANDIDATE'?'up':a==='SETUP'||a==='SHADOW_ONLY'?'warn':a==='BLOCKED'||a==='SAFETY_WAIT'?'down':'neutral'}
function nameOf(code){const s=lastData?.scanner?.find(x=>x.code===code),q=lastData?.quotes?.find(x=>x.code===code);return s?.name||q?.name||code}
function labelOf(code){const n=nameOf(code);return n===code?code:`${n} ${code}`}
function firstBlock(e){return (e?.blockedReasons||[])[0]||''}
function decisionText(e){
  if(e.action==='SAFETY_WAIT')return '안전 마스터 검증 대기';
  if(e.action==='BLOCKED')return firstBlock(e)||'안전필터 제외';
  if(e.action==='WAIT_DATA')return `5분봉 준비 ${e.indicators?.bars||0}/${e.indicators?.need||35}`;
  const c=e.indicators?.checks||{},m=e.market||{},miss=[];
  if(!c.session_ready)miss.push('개장30분 미완료');
  if(!m.liquidityOk)miss.push('유동성 기준 미달');
  if(!c.above_vwap)miss.push('VWAP 아래');
  if(!c.ema_bull)miss.push('EMA9≤20');
  if(!c.trigger_gate)miss.push('ORB30/강한돌파 없음');
  if(!c.rsi_ok)miss.push('RSI 범위 밖');
  if(!c.dmi_ok)miss.push('ADX/DMI 약함');
  if((e.marketBreadth??1)<0.35&&e.score<90)miss.push('시장 breadth 약함');
  return miss.length?'보류: '+miss.slice(0,3).join(' · '):'진입 조건 충족';
}
async function api(path,opt){const r=await fetch(path,{cache:'no-store',...opt});let j={};try{j=await r.json()}catch{}if(!r.ok)throw new Error(j.detail||`HTTP ${r.status}`);return j}
function setBrand(){const brand=document.querySelector('.brand');if(brand)brand.innerHTML=`Stock Day Trader <span>v${uiVersion} LIVE</span>`;const footer=document.querySelector('footer');if(footer)footer.textContent=`Day Trader v${uiVersion} LIVE · Lenovo/NH · SAFE MASTER · REAL ORDER OFF`}
function addCollapse(panel,label){if(!panel)return;const b=document.createElement('button');b.className='btn mobile-collapse-btn';b.textContent='펼쳐보기 · '+label;b.onclick=()=>{panel.classList.toggle('mobile-collapsed');b.textContent=(panel.classList.contains('mobile-collapsed')?'펼쳐보기 · ':'접기 · ')+label};panel.appendChild(b);panel.classList.add('mobile-collapsed')}

async function bootstrapUi(){
  try{const s=await api('/api/system/update/status');if(s.uiVersion)uiVersion=s.uiVersion}catch{}
  setBrand();
  const sub=document.querySelector('.sub');if(sub)sub.textContent='NH 실시간 · 안전필터 · 광역 스캔 · 활성30 · Top10 · Paper';
  const panel=$('.connection-panel .panel-head');
  if(panel&&!$('#remoteUpdateBtn')){const b=document.createElement('button');b.id='remoteUpdateBtn';b.className='btn secondary-action';b.textContent='↻ 업데이트';panel.appendChild(b);b.onclick=runUpdate}
  if($('#backendUrl')){$('#backendUrl').value=location.origin;$('#backendUrl').readOnly=true}
  if($('#saveBackendBtn'))$('#saveBackendBtn').style.display='none';
  if($('#nhCheckBtn')){$('#nhCheckBtn').onclick=checkConnection;$('#nhCheckBtn').classList.add('utility-action');$('#nhCheckBtn').textContent='✓ 연결 확인'}
  if($('#scanBtn')){$('#scanBtn').onclick=runScan;$('#scanBtn').classList.add('scan-action');$('#scanBtn').textContent='↻ 재스캔'}
  if($('#backtestBtn')){$('#backtestBtn').disabled=true;$('#backtestBtn').textContent='실데이터 차트'}
  if($('#autoToggle')){$('#autoToggle').disabled=true;$('#autoToggle').checked=true}
  if($('#autoStateText'))$('#autoStateText').textContent='SERVER PAPER';
  if($('#killBtn')){$('#killBtn').disabled=true;$('#killBtn').textContent='REAL ORDER OFF'}
  if($('#resetBtn')){$('#resetBtn').disabled=true;$('#resetBtn').textContent='서버 데이터 보호'}
  if($('#maxOrderText'))$('#maxOrderText').textContent='비용 포함 Risk sizing';
  if($('#riskTradeText'))$('#riskTradeText').textContent='0.35%';
  if($('#maxPositionsText'))$('#maxPositionsText').textContent='2개';
  if($('#exitText'))$('#exitText').textContent='-1.0% · +0.8% 보호 · +1.5% Trail';
  if(matchMedia('(max-width:520px)').matches){const lows=document.querySelectorAll('.lower-grid');if(lows[1]){addCollapse(lows[1].children[0],'실패 원인 분석');addCollapse(lows[1].children[1],'시스템 로그')}const order=document.querySelector('#tradesBody')?.closest('.panel');addCollapse(order,'Paper 주문 내역')}
}

async function checkConnection(){
  try{
    const h=await api('/api/health'),safe=h.universe?.verified;
    $('#nhBadge').textContent=h.credentialsConfigured?'NH CONNECTED':'NH SERVER ONLY';$('#nhBadge').className='badge '+(h.credentialsConfigured?'ok':'');
    $('#brokerBadge').textContent=safe?'SAFE MASTER ON':'MASTER CHECK';$('#brokerBadge').className='badge '+(safe?'ok':'');
    $('#nhStatusText').innerHTML=`<b>Lenovo 정상</b> · Engine v${h.version} · ${h.mode} · 안전마스터 ${safe?'검증완료':'확인중'} · REAL ORDER OFF`;
    log(`연결 정상 · Engine ${h.version} · 안전필터 ${safe?'ON':'대기'}`);
  }catch(e){$('#nhBadge').textContent='BACKEND OFF';$('#nhStatusText').textContent='연결 실패: '+e.message;log('연결 실패 · '+e.message)}
}

function render(d){
  lastData=d;
  const safe=!!d.collector?.safetyVerified,u=d.universe||d.collector?.universe||{};
  $('#systemBadge').textContent=d.collector.running?'SYSTEM RUNNING':'SYSTEM OFF';$('#systemBadge').className='badge '+(d.collector.running?'ok':'badbadge');
  $('#brokerBadge').textContent=safe?'SAFE FILTER ON':'SAFE CHECK';$('#brokerBadge').className='badge '+(safe?'ok':'');
  $('#nhBadge').textContent='NH LIVE DATA';$('#nhBadge').className='badge ok';
  $('#lockBadge').textContent=d.daily.locked?'DAILY LOCK':'TRADE READY';$('#lockBadge').className='badge '+(d.daily.locked?'badbadge':'ok');
  const op=d.positions.reduce((s,x)=>s+Number(x.unrealized_pnl||0),0),capital=10000000+Number(d.daily.pnl||0)+op;
  $('#equity').textContent=won(capital);$('#equityPnl').textContent=`실현 ${won(d.daily.pnl)} · 평가 ${won(op)}`;
  $('#cash').textContent='서버 관리';$('#vault').textContent='통합 예정';$('#reserve').textContent='통합 예정';
  $('#lossStreak').textContent=`${d.daily.consecutiveLosses} / 2`;$('#shadowCount').textContent=d.paperLoop.shadowSignals||0;$('#shadowWin').textContent='Server Shadow';

  const all=[...(d.scanner||[])],ss=all.slice(0,10);
  const desc=document.querySelector('.main-grid article .panel-head p');
  if(desc)desc.textContent=`공식 마스터 안전후보 ${u.selectedRows||d.collector.universeSize||0} · 활성 ${all.length} · Top10 · ORB30/VWAP/EMA/거래량`;
  $('#scannerBody').innerHTML=ss.map((s,i)=>{
    const x=s.indicators||{},m=s.market||{},p=s.code==='068270';
    const activity=m.activityScore!=null?`활성 ${Number(m.activityScore).toFixed(0)} · ${Number(m.turnoverEok||0).toFixed(0)}억`:'';
    return `<tr class="pick-row" data-code="${s.code}"><td>${i+1}</td><td><b>${s.name||nameOf(s.code)}</b><br><small>${s.code}</small>${p?' <span class="protected">PROTECTED</span>':''}</td><td>${x.price?won(x.price):'-'}</td><td>${m.changeRate!=null?pct(m.changeRate):'-'}</td><td><span class="score">${Number(s.score||0).toFixed(1)}</span><br><small>${decisionText(s)}</small><br><small class="neutral">${activity}</small></td><td>${x.vwap?won(x.vwap):'-'}</td><td>${x.volumeRatio?Number(x.volumeRatio).toFixed(2)+'x':'-'}</td><td class="${actionClass(s.action)}">${s.action}</td><td>서버 자동</td></tr>`;
  }).join('')||'<tr><td colspan="9" class="neutral">활성 후보 준비 중</td></tr>';
  document.querySelectorAll('.pick-row').forEach(r=>r.onclick=()=>{selectedCode=r.dataset.code;renderSelected();if(innerWidth<=520)$('#priceChart')?.scrollIntoView({behavior:'smooth',block:'center'})});

  $('#positionsBody').innerHTML=d.positions.length?d.positions.map(p=>`<tr><td><b>${nameOf(p.code)}</b><br><span class="neutral">${p.code} · SERVER PAPER</span></td><td>${p.qty}</td><td>${won(p.entry_price)}</td><td>${won(p.current_price)}</td><td class="${p.unrealized_pnl>=0?'up':'down'}">${won(p.unrealized_pnl)}</td><td>${pct(p.unrealized_pct)}</td><td>자동관리</td></tr>`).join(''):'<tr><td colspan="7" class="neutral">포지션 없음</td></tr>';
  $('#tradesBody').innerHTML=d.recentTrades?.length?d.recentTrades.slice(0,30).map(t=>`<tr><td>${t.exit_at?new Date(t.exit_at).toLocaleTimeString('ko-KR',{hour12:false}):'-'}</td><td>${labelOf(t.code)}</td><td>CLOSE</td><td>${t.qty}</td><td>${t.exit_price?won(t.exit_price):'-'}</td><td class="${Number(t.pnl||0)>=0?'up':'down'}">${won(t.pnl)}</td><td>${t.exit_reason||t.status}</td></tr>`).join(''):'<tr><td colspan="7" class="neutral">거래 없음</td></tr>';

  $('#riskState').textContent=d.daily.locked?'DAILY LOCK · 신규진입 중지 · Shadow 계속':safe?'NORMAL · 안전필터 ON · 비용반영 Risk':'SAFETY WAIT · 신규진입 차단';
  $('#riskState').className='risk-state '+(d.daily.locked||!safe?'badbox':'okbox');
  $('#strategyStats').innerHTML=`<div><small>Safe Universe</small><strong>${u.selectedRows||0}</strong></div><div><small>Active Focus</small><strong>${all.length}/30</strong></div><div><small>Entry Window</small><strong>${d.entryStart||'09:30'}~${d.entryCutoff}</strong></div><div><small>EOD Exit</small><strong>${d.eodExit}</strong></div><div><small>Cost Est.</small><strong>${Number(d.risk?.roundTripCostEstimatePct||0).toFixed(2)}%</strong></div><div><small>Max Positions</small><strong>${d.risk?.maxOpenPositions||2}</strong></div>`;
  $('#learningBox').innerHTML=`<div class="learning-stats"><b>Shadow ${d.paperLoop.shadowSignals||0}</b><b>종료 ${d.daily.closedTrades||0}</b><b>연속손실 ${d.daily.consecutiveLosses||0}/2</b></div><p>관리·거래정지·정리매매·투자경고 등 위험종목은 공식 NH 종목마스터 단계에서 제외합니다. 개장 30분 이후 ORB30 + VWAP/EMA + 유동성 + RSI/ADX를 통과한 종목만 Paper 진입 후보가 됩니다.</p>`;
  if((!selectedCode||!all.some(x=>x.code===selectedCode))&&ss.length)selectedCode=ss[0].code;
  renderSelected();
}

async function renderSelected(){
  if(!lastData)return;const s=(lastData.scanner||[]).find(x=>x.code===selectedCode)||(lastData.scanner||[])[0];if(!s)return;
  selectedCode=s.code;const i=s.indicators||{},m=s.market||{};
  $('#chartTitle').textContent=`${s.name||labelOf(s.code)} ${s.code} · NH 5분봉`;
  $('#indicatorCards').innerHTML=`<div><small>판정</small><strong>${s.action}</strong></div><div><small>Score</small><strong>${Number(s.score||0).toFixed(1)}</strong></div><div><small>VWAP</small><strong>${i.vwap?won(i.vwap):'-'}</strong></div><div><small>EMA9/20</small><strong>${i.ema9&&i.ema20?Math.round(i.ema9).toLocaleString()+' / '+Math.round(i.ema20).toLocaleString():'-'}</strong></div><div><small>RSI</small><strong>${i.rsi!=null?Number(i.rsi).toFixed(1):'-'}</strong></div><div><small>ADX</small><strong>${i.adx!=null?Number(i.adx).toFixed(1):'-'}</strong></div>`;
  const orb=i.openingRangeHigh?won(i.openingRangeHigh):'-',spread=m.spreadPct?Number(m.spreadPct).toFixed(3)+'%':'-',breadth=Number(s.marketBreadth||0)*100;
  $('#backtestResult').textContent=`${decisionText(s)} · ORB30 고점 ${orb} · 거래대금 ${Number(m.turnoverEok||0).toFixed(1)}억 · 스프레드 ${spread} · 시장 breadth ${breadth.toFixed(0)}% · 완료봉 기준`;
  try{const j=await api(`/api/market/bars/${s.code}?limit=80&u=${Date.now()}`);drawChart(j.rows||[],i.vwap)}catch(e){log('차트 오류 · '+e.message)}
}

function ema(values,period){if(!values.length)return[];const a=2/(period+1),out=[];let v=Number(values[0]);out.push(v);for(let i=1;i<values.length;i++){v=a*Number(values[i])+(1-a)*v;out.push(v)}return out}
function drawChart(rows,vwap){
  const c=$('#priceChart');if(!c)return;const ctx=c.getContext('2d'),r=devicePixelRatio||1,w=c.clientWidth||600,h=c.clientHeight||300;c.width=w*r;c.height=h*r;ctx.setTransform(r,0,0,r,0,0);ctx.clearRect(0,0,w,h);
  rows=(rows||[]).filter(x=>Number(x.open)>0&&Number(x.high)>0&&Number(x.low)>0&&Number(x.close)>0).sort((a,b)=>String(a.bucket).localeCompare(String(b.bucket)));
  if(rows.length<2){ctx.fillStyle='#94a3b8';ctx.fillText('5분봉 데이터 준비 중',12,22);return}
  const highs=rows.map(x=>Number(x.high)),lows=rows.map(x=>Number(x.low)),closes=rows.map(x=>Number(x.close)),a=Math.min(...lows),b=Math.max(...highs),pad=(b-a||1)*.08,lo=a-pad,hi=b+pad,plotH=h-28,xStep=(w-16)/rows.length,bodyW=Math.max(2,Math.min(7,xStep*.58)),y=p=>plotH-(p-lo)/(hi-lo)*(plotH-8)+4;
  ctx.strokeStyle='#334155';ctx.lineWidth=1;for(let k=1;k<4;k++){const gy=plotH*k/4;ctx.beginPath();ctx.moveTo(0,gy);ctx.lineTo(w,gy);ctx.stroke()}
  rows.forEach((bar,i)=>{const x=8+(i+.5)*xStep,o=Number(bar.open),cl=Number(bar.close),hh=Number(bar.high),ll=Number(bar.low),up=cl>=o;ctx.strokeStyle=up?'#4ade80':'#fb7185';ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(x,y(hh));ctx.lineTo(x,y(ll));ctx.stroke();const top=Math.min(y(o),y(cl)),bh=Math.max(1,Math.abs(y(o)-y(cl)));ctx.fillRect(x-bodyW/2,top,bodyW,bh)});
  const e9=ema(closes,9),e20=ema(closes,20);[[e9,'#60a5fa'],[e20,'#f59e0b']].forEach(([arr,color])=>{ctx.strokeStyle=color;ctx.lineWidth=1.6;ctx.beginPath();arr.forEach((p,i)=>{const x=8+(i+.5)*xStep,yy=y(p);i?ctx.lineTo(x,yy):ctx.moveTo(x,yy)});ctx.stroke()});
  if(vwap&&vwap>=lo&&vwap<=hi){ctx.save();ctx.setLineDash([4,3]);ctx.strokeStyle='#5ee0a3';ctx.beginPath();ctx.moveTo(6,y(vwap));ctx.lineTo(w-6,y(vwap));ctx.stroke();ctx.restore()}
  ctx.fillStyle='#94a3b8';ctx.font='11px system-ui';ctx.fillText('5분봉 · EMA9/20 · VWAP',8,h-8);
}

async function load(){try{render(await api('/api/mobile/status?u='+Date.now()))}catch(e){$('#systemBadge').textContent='SERVER OFF';$('#systemBadge').className='badge badbadge';log('갱신 실패 · '+e.message)}}
async function runScan(){
  if(scanning)return;scanning=true;const b=$('#scanBtn');if(b){b.disabled=true;b.textContent='⏳ 스캔'}
  try{const j=await api('/api/paper/scan?u='+Date.now()),rows=j.rows||[];await load();const buys=rows.filter(x=>x.action==='BUY_CANDIDATE').length,setups=rows.filter(x=>x.action==='SETUP').length;log(`광역 재스캔 · 활성 ${rows.length} · BUY ${buys} · SETUP ${setups}`);if(b)b.textContent=`✓ ${rows.length}`}
  catch(e){log('스캔 실패 · '+e.message);alert('스캔 실패: '+e.message)}
  finally{setTimeout(()=>{scanning=false;if(b){b.disabled=false;b.textContent='↻ 재스캔'}},900)}
}
async function runUpdate(){
  if(updating)return;if(lastData?.positions?.length){alert('열린 Paper 포지션이 있어 업데이트할 수 없습니다.');return}
  if(!confirm('최신 버전을 확인하고 필요한 경우 업데이트할까요?'))return;updating=true;const b=$('#remoteUpdateBtn');if(b){b.disabled=true;b.textContent='확인 중...'}
  try{const j=await api('/api/system/update/run',{method:'POST'});if(j.uiVersion){uiVersion=j.uiVersion;setBrand()}}
  catch(e){alert('업데이트 차단: '+e.message);updating=false;if(b){b.disabled=false;b.textContent='↻ 업데이트'}return}
  let seenDown=false,tries=0;if(b)b.textContent='재시작 대기';const t=setInterval(async()=>{tries++;try{const r=await fetch('/api/health?u='+Date.now(),{cache:'no-store'});if(!r.ok)throw new Error('offline');if(seenDown){clearInterval(t);if(b)b.textContent='✓ 완료';location.replace('/classic?u='+Date.now());return}}catch{seenDown=true;if(b)b.textContent='재시작 중'}if(tries>90){clearInterval(t);updating=false;if(b){b.disabled=false;b.textContent='↻ 업데이트'}alert('서버 재연결 시간이 초과되었습니다.')}} ,1000);
}

bootstrapUi().then(()=>{checkConnection();load();setInterval(()=>{if(!updating&&!scanning)load()},5000)});addEventListener('resize',()=>renderSelected());log('Day Trader v0.11.0 LIVE 시작 · SAFE MASTER · REAL ORDER OFF');
