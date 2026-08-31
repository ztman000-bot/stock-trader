import {CONFIG} from './config.js';
import {MockBroker} from './mockBroker.js';
import {scoreSymbol,evaluateExit} from './strategy.js';
import {RiskEngine} from './risk.js';
import {PaperAccount} from './paperBroker.js';
import {LearningEngine} from './learning.js';
import {renderChart} from './chart.js';
import {backtestSymbol} from './backtest.js';
import {NHBridge} from './nhBridge.js';

const broker=new MockBroker();
const paper=new PaperAccount(CONFIG.initialCash,CONFIG.profitSplit);
const risk=new RiskEngine(CONFIG.risk,CONFIG.protectedSymbols);
const learning=new LearningEngine(CONFIG.dayTrading);
const nh=new NHBridge(CONFIG.nh.backendBaseUrl);nh.loadSavedUrl();
let auto=false,lastScores=[],selectedCode='005930',tick=0;
const $=s=>document.querySelector(s),won=n=>'₩'+Math.round(n||0).toLocaleString('ko-KR'),pct=n=>(n*100).toFixed(2)+'%';
function log(msg){const d=document.createElement('div');d.className='logline';d.innerHTML=`<span>${new Date().toLocaleTimeString('ko-KR',{hour12:false})}</span> ${msg}`;$('#logBox').prepend(d);while($('#logBox').children.length>80)$('#logBox').lastChild.remove();}
function totalDayEquity(){return paper.totalProtectedEquity(broker.getQuotes())}
function dailyPnlPct(){return (totalDayEquity()-paper.dayStartEquity)/paper.dayStartEquity}
function orderQty(price){const eq=paper.equity(broker.getQuotes()),riskWon=eq*CONFIG.risk.riskPerTradePct,stopWon=price*CONFIG.dayTrading.stopLossPct,byRisk=Math.floor(riskWon/Math.max(stopWon,1)),byOrder=Math.floor(CONFIG.risk.maxOrderWon/price),byPosition=Math.floor(eq*CONFIG.risk.maxPositionPct/price),byCash=Math.floor(paper.cash/price);return Math.max(0,Math.min(byRisk,byOrder,byPosition,byCash))}

async function checkNhConnection(){
 const box=$('#nhStatusText'),badge=$('#nhBadge');box.textContent='NH 백엔드 연결 확인 중...';
 try{const h=await nh.health();badge.textContent=h.credentialsConfigured?'NH CONNECTED':'NH SERVER ONLY';badge.className='badge '+(h.credentialsConfigured?'ok':'');box.innerHTML=`<b>백엔드 정상</b> · mode ${h.mode} · trading ${h.tradingEnabled?'ENABLED':'LOCKED'} · credentials ${h.credentialsConfigured?'READY':'미설정'} · ${h.baseUrl}`;log(`NH Bridge 연결 · ${h.mode} · 주문 ${h.tradingEnabled?'허용':'잠금'}`)}
 catch(e){badge.textContent='NH BACKEND OFF';badge.className='badge';box.textContent=`연결 실패: ${e.message}`;log(`NH Bridge 연결 실패 · ${e.message}`)}
}

function scan(){
 tick++;
 const qs=broker.getQuotes();
 lastScores=qs.map(q=>({...scoreSymbol(q,broker.getHistory(q.code),CONFIG.dayTrading),quote:q})).sort((a,b)=>b.total-a.total);
 renderScanner();renderSelected();
 manageAutoPositions();
 if(auto&&!risk.halted)autoEntries();
 if(risk.haltType==='daily')learning.observeShadow(lastScores.filter(s=>!CONFIG.protectedSymbols.includes(s.code)),tick);
 renderAll();
 log(`Scanner · TOP ${lastScores[0].name} ${lastScores[0].total}점 · ${lastScores.filter(s=>s.entryReady).length}개 진입조건 충족`);
}

function renderScanner(){
 $('#scannerBody').innerHTML=lastScores.slice(0,10).map((s,i)=>{const prot=CONFIG.protectedSymbols.includes(s.code);const core=[s.checks.aboveVwap,s.checks.emaTrend,s.checks.volume,s.checks.breakout].filter(Boolean).length;return `<tr class="pick-row" data-code="${s.code}"><td>${i+1}</td><td><b>${s.name}</b>${prot?' <span class="protected">PROTECTED</span>':''}<br><span class="neutral">${s.code}</span></td><td>${won(s.quote.price)}</td><td class="${s.quote.change>=0?'up':'down'}">${pct(s.quote.change)}</td><td><span class="score">${core}/4</span></td><td class="${s.checks.aboveVwap?'up':'down'}">${s.checks.aboveVwap?'상단':'하단'}</td><td>${s.ind.volumeRatio.toFixed(2)}x</td><td>${prot?'장기보유 보호':s.verdict}</td><td><button class="btn small buy-btn" ${prot?'disabled':''} data-code="${s.code}">Paper 매수</button></td></tr>`}).join('');
 document.querySelectorAll('.pick-row').forEach(r=>r.onclick=e=>{if(e.target.closest('button'))return;selectedCode=r.dataset.code;renderSelected()});
 document.querySelectorAll('.buy-btn:not([disabled])').forEach(b=>b.onclick=()=>manualBuy(b.dataset.code));
}
function renderSelected(){const s=lastScores.find(x=>x.code===selectedCode)||lastScores[0];if(!s)return;selectedCode=s.code;$('#chartTitle').textContent=`${s.name} (${s.code})`;$('#indicatorCards').innerHTML=`<div><small>핵심조건</small><strong>${[s.checks.aboveVwap,s.checks.emaTrend,s.checks.volume,s.checks.breakout].filter(Boolean).length}/4</strong></div><div><small>VWAP</small><strong>${won(s.ind.vwap)}</strong></div><div><small>EMA9/20</small><strong>${s.ind.ema9>s.ind.ema20?'상승':'약세'}</strong></div><div><small>RSI(14)</small><strong>${s.ind.rsi.toFixed(1)}</strong></div><div><small>ADX / DMI</small><strong>${s.ind.adx.toFixed(0)} · ${s.ind.plusDI>s.ind.minusDI?'+DI':'-DI'}</strong></div><div><small>거래량비</small><strong>${s.ind.volumeRatio.toFixed(2)}x</strong></div>`;renderChart($('#priceChart'),broker.getHistory(s.code),s.ind)}

function validate(code,q,qty){return risk.validateBuy({code,price:q.price,qty,equity:paper.equity(broker.getQuotes()),positions:Object.values(paper.positions),dailyPnlPct:dailyPnlPct(),lossStreak:paper.lossStreak})}
function manualBuy(code){const q=broker.getQuote(code),s=lastScores.find(x=>x.code===code),qty=orderQty(q.price),check=validate(code,q,qty);if(!check.ok){log(`Risk 거절 · ${q.name}: ${check.reason}`);return}if(paper.buy(q,qty,'수동 Paper 주문',{mode:'MANUAL',signal:s?.checks||{}}))log(`BUY ${q.name} ${qty}주 @ ${won(q.price)}`);renderAll()}
function manualSell(code){const q=broker.getQuote(code),p=paper.positions[code];if(!p)return;const qty=p.qty,trade=paper.sell(q,qty,'수동 전량매도');if(trade){handleClosedTrade(trade,lastScores.find(x=>x.code===code));log(`SELL ${q.name} ${qty}주 · ${won(trade.pnl)}`)}renderAll()}
function handleClosedTrade(trade,score){if(trade.pnl<0&&score)learning.recordLoss(trade,score);if(trade.split)log(`수익분리 · 재투자 ${won(trade.split.reinvested)} / Vault ${won(trade.split.vault)} / Reserve ${won(trade.split.reserve)}`);if(risk.shouldDailyLock(paper.lossStreak)){risk.halt(`${CONFIG.risk.maxLossStreak}연패 · 당일 신규매매 중지`,'daily');auto=false;$('#autoToggle').checked=false;$('#autoStateText').textContent='DAILY LOCK';log('DAILY LOCK · 주문 중지, Scanner/Shadow Learning 계속 실행')}}
function manageAutoPositions(){const qs=broker.getQuotes();for(const p of Object.values({...paper.positions})){if(p.entryMeta?.mode!=='AUTO')continue;const q=qs.find(x=>x.code===p.code),s=lastScores.find(x=>x.code===p.code);if(!q||!s)continue;const ex=evaluateExit(p,q,s,CONFIG.dayTrading);if(ex.sell){const trade=paper.sell(q,p.qty,'AUTO '+ex.reason);if(trade){handleClosedTrade(trade,s);log(`AUTO SELL ${q.name} · ${ex.reason} · ${won(trade.pnl)}`)}}}}
function autoEntries(){for(const s of lastScores){if(!s.entryReady||CONFIG.protectedSymbols.includes(s.code)||paper.positions[s.code])continue;const q=s.quote,qty=orderQty(q.price),check=validate(s.code,q,qty);if(check.ok&&paper.buy(q,qty,'AUTO 핵심조건 충족',{mode:'AUTO',signal:s.checks,score:s.total})){log(`AUTO BUY ${q.name} ${qty}주 · VWAP/EMA/Volume/Breakout 충족`);break}if(!check.ok)log(`AUTO Risk 차단 · ${q.name}: ${check.reason}`)}}

function renderAll(){
 const qs=broker.getQuotes(),eq=paper.equity(qs),total=paper.totalProtectedEquity(qs),pnl=total-paper.dayStartEquity,positions=Object.values(paper.positions),ls=learning.stats();
 $('#equity').textContent=won(eq);$('#equityPnl').textContent=`오늘 경제손익 ${won(pnl)} (${pct(pnl/paper.dayStartEquity)})`;$('#cash').textContent=won(paper.cash);$('#vault').textContent=won(paper.vault);$('#reserve').textContent=won(paper.reserve);$('#lossStreak').textContent=`${paper.lossStreak} / ${CONFIG.risk.maxLossStreak}`;$('#shadowCount').textContent=ls.shadowTrades;$('#shadowWin').textContent=`승률 ${pct(ls.shadowWinRate)}`;
 $('#positionsBody').innerHTML=positions.length?positions.map(p=>{const q=qs.find(x=>x.code===p.code),val=q.price*p.qty,pn=(q.price-p.avg)*p.qty;return `<tr><td><b>${p.name}</b><br><span class="neutral">${p.code} · ${p.entryMeta?.mode||'PAPER'}</span></td><td>${p.qty}</td><td>${won(p.avg)}</td><td>${won(q.price)}</td><td class="${pn>=0?'up':'down'}">${won(pn)}</td><td>${pct(val/Math.max(eq,1))}</td><td><button class="btn small sell-btn" data-code="${p.code}">전량매도</button></td></tr>`}).join(''):'<tr><td colspan="7" class="neutral">Day Trading 포지션이 없습니다.</td></tr>';document.querySelectorAll('.sell-btn').forEach(b=>b.onclick=()=>manualSell(b.dataset.code));
 $('#tradesBody').innerHTML=paper.trades.slice(0,30).map(t=>`<tr><td>${new Date(t.ts).toLocaleTimeString('ko-KR',{hour12:false})}</td><td>${t.name}</td><td class="${t.side==='BUY'?'up':'down'}">${t.side}</td><td>${t.qty}</td><td>${won(t.price)}</td><td class="${(t.pnl||0)>=0?'up':'down'}">${t.pnl==null?'-':won(t.pnl)}</td><td>${t.reason}</td></tr>`).join('')||'<tr><td colspan="7" class="neutral">아직 주문이 없습니다.</td></tr>';
 const blocked=risk.halted||dailyPnlPct()<=-CONFIG.risk.dailyLossPct;$('#riskState').textContent=blocked?(risk.haltReason||'RISK BLOCKED'):'NORMAL · ENTRY GATE ACTIVE';$('#riskState').className='risk-state '+(blocked?'badbox':'okbox');$('#lockBadge').textContent=risk.haltType==='daily'?'DAILY LOCK':'TRADE READY';$('#lockBadge').className='badge '+(risk.haltType==='daily'?'badbadge':'ok');
 $('#strategyStats').innerHTML=`<div><small>진입 핵심</small><strong>4/4 + 확인 1</strong></div><div><small>손절</small><strong>-${pct(CONFIG.dayTrading.stopLossPct)}</strong></div><div><small>목표익절</small><strong>+${pct(CONFIG.dayTrading.takeProfitPct)}</strong></div><div><small>1회 위험</small><strong>${pct(CONFIG.risk.riskPerTradePct)}</strong></div><div><small>재투자 누계</small><strong>${won(paper.reinvested)}</strong></div><div><small>Protected</small><strong>068270</strong></div>`;
 const tags=ls.topTags.length?ls.topTags.map(([t,n])=>`<span class="tag">${t} ${n}</span>`).join(' '):'<span class="neutral">아직 손실 사례 없음</span>';$('#learningBox').innerHTML=`<div class="learning-stats"><b>손실 사례 ${ls.lossCases}건</b><b>Shadow ${ls.shadowTrades}건 / 승률 ${pct(ls.shadowWinRate)}</b><b>평균 손실 ${pct(ls.avgLoss)}</b></div><div class="tags">${tags}</div><p>${learning.recommendation()}</p><small>※ 학습 엔진은 실전 파라미터를 자동 변경하지 않습니다. 실패 원인과 대안 성과를 축적해 검증 후보만 제시합니다.</small>`;
}
function runBacktest(){const s=lastScores.find(x=>x.code===selectedCode)||lastScores[0],r=backtestSymbol(s.quote,broker.getHistory(s.code),CONFIG.dayTrading);$('#backtestResult').innerHTML=`<strong>${s.name}</strong> · 거래 ${r.trades}회 · 승률 ${pct(r.winRate)} · 누적수익 ${pct(r.returnPct)} · MDD ${pct(r.maxDrawdown)}`;log(`Backtest ${s.name} · 승률 ${pct(r.winRate)} · 수익 ${pct(r.returnPct)}`)}

$('#scanBtn').onclick=scan;$('#backtestBtn').onclick=runBacktest;
$('#saveBackendBtn').onclick=()=>{nh.setBaseUrl($('#backendUrl').value);$('#nhStatusText').textContent=nh.baseUrl?'백엔드 주소 저장됨. 연결 확인을 눌러주세요.':'백엔드 주소가 비어 있습니다.';log(`NH 백엔드 주소 ${nh.baseUrl?'저장':'삭제'}`)};
$('#nhCheckBtn').onclick=checkNhConnection;
$('#autoToggle').onchange=e=>{if(e.target.checked&&risk.haltType==='daily'){e.target.checked=false;log('AUTO 시작 거절 · 2연패 Daily Lock은 당일 해제 불가');return}auto=e.target.checked;$('#autoStateText').textContent=auto?'ON · PAPER':'OFF';log(`AUTO TRADE ${auto?'ON':'OFF'}`)};
$('#killBtn').onclick=()=>{auto=false;$('#autoToggle').checked=false;$('#autoStateText').textContent='EMERGENCY STOP';risk.halt('사용자 긴급 정지','manual');renderAll();log('EMERGENCY STOP · 신규 자동주문 차단')};
$('#resetBtn').onclick=()=>{if(confirm('Paper 계좌와 학습 데이터를 모두 초기화할까요?')){paper.reset();learning.reset();risk.resume(true);auto=false;$('#autoToggle').checked=false;$('#autoStateText').textContent='OFF';renderAll();log('Paper/학습 데이터 초기화')}};
$('#maxOrderText').textContent=won(CONFIG.risk.maxOrderWon);$('#riskTradeText').textContent=pct(CONFIG.risk.riskPerTradePct);$('#maxPositionsText').textContent=CONFIG.risk.maxPositions+'종목';$('#exitText').textContent=`-${pct(CONFIG.dayTrading.stopLossPct)} / +${pct(CONFIG.dayTrading.takeProfitPct)}`;$('#backendUrl').value=nh.baseUrl;
scan();renderAll();log('Stock Day Trader v0.4 시작 · NH Live Data Ready · 실주문 잠금');if(nh.baseUrl)checkNhConnection();setInterval(()=>{broker.tick();scan()},CONFIG.scanIntervalMs);addEventListener('resize',()=>renderSelected());if('serviceWorker'in navigator)navigator.serviceWorker.register('./sw.js').catch(()=>{});
