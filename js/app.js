import {CONFIG} from './config.js';
import {MockBroker} from './mockBroker.js';
import {scoreSymbol,evaluateExit} from './strategy.js';
import {RiskEngine} from './risk.js';
import {PaperAccount} from './paperBroker.js';

const broker=new MockBroker();
const paper=new PaperAccount(CONFIG.initialCash);
const risk=new RiskEngine(CONFIG.risk);
let cycle=0, auto=false, lastScores=[];
const $=s=>document.querySelector(s);
const won=n=>'₩'+Math.round(n).toLocaleString('ko-KR');
const pct=n=>(n*100).toFixed(2)+'%';
function log(msg){const d=document.createElement('div');d.className='logline';d.innerHTML=`<span>${new Date().toLocaleTimeString('ko-KR',{hour12:false})}</span> ${msg}`;$('#logBox').prepend(d);while($('#logBox').children.length>60)$('#logBox').lastChild.remove();}

function scan(){
  cycle++;
  const qs=broker.getQuotes();
  lastScores=qs.map(q=>({...scoreSymbol(q,cycle),quote:q})).sort((a,b)=>b.total-a.total);
  renderScanner();
  if(auto) autoTrade();
  log(`Scanner 완료 · TOP ${lastScores[0].name} ${lastScores[0].total}점`);
}

function renderScanner(){
  $('#scannerBody').innerHTML=lastScores.slice(0,10).map((s,i)=>`<tr><td>${i+1}</td><td><b>${s.name}</b><br><span class="neutral">${s.code}</span></td><td>${won(s.quote.price)}</td><td class="${s.quote.change>=0?'up':'down'}">${pct(s.quote.change)}</td><td><span class="score">${s.total}</span></td><td>${s.verdict}</td><td><button class="btn small buy-btn" data-code="${s.code}">Paper 매수</button></td></tr>`).join('');
  document.querySelectorAll('.buy-btn').forEach(b=>b.onclick=()=>manualBuy(b.dataset.code));
}

function getEquity(){return paper.equity(broker.getQuotes());}
function orderQty(price){return Math.max(1,Math.floor(CONFIG.risk.maxOrderWon/price));}
function manualBuy(code){
  const q=broker.getQuote(code),eq=getEquity(),qty=orderQty(q.price);
  const check=risk.validateBuy({price:q.price,qty,equity:eq,positions:Object.values(paper.positions),dailyPnlPct:(eq-paper.dayStartEquity)/paper.dayStartEquity,lossStreak:paper.lossStreak});
  if(!check.ok){log(`Risk 거절 · ${q.name}: ${check.reason}`);return;}
  if(paper.buy(q,qty,'수동 Paper 주문')) log(`BUY ${q.name} ${qty}주 @ ${won(q.price)}`); else log(`주문 실패 · 현금 부족`);
  renderAll();
}
function manualSell(code){const q=broker.getQuote(code),p=paper.positions[code];if(p&&paper.sell(q,p.qty,'수동 전량매도'))log(`SELL ${q.name} ${p.qty}주 @ ${won(q.price)}`);renderAll();}

function autoTrade(){
  const qs=broker.getQuotes(),eq=getEquity();
  for(const p of Object.values({...paper.positions})){
    const q=qs.find(x=>x.code===p.code);const s=lastScores.find(x=>x.code===p.code);if(!q||!s)continue;
    const ex=evaluateExit(p,q,s,CONFIG.strategy);if(ex.sell){paper.sell(q,p.qty,'AUTO '+ex.reason);log(`AUTO SELL ${q.name} · ${ex.reason}`);}
  }
  for(const s of lastScores){
    if(s.total<CONFIG.strategy.buyScore) break;
    if(paper.positions[s.code]) continue;
    const q=s.quote, qty=orderQty(q.price), equity=getEquity();
    const check=risk.validateBuy({price:q.price,qty,equity,positions:Object.values(paper.positions),dailyPnlPct:(equity-paper.dayStartEquity)/paper.dayStartEquity,lossStreak:paper.lossStreak});
    if(check.ok && paper.buy(q,qty,`AUTO Score ${s.total}`)){log(`AUTO BUY ${q.name} ${qty}주 · Score ${s.total}`);break;} else if(!check.ok){log(`AUTO Risk 차단 · ${q.name}: ${check.reason}`);}
  }
  renderAll();
}

function renderAll(){
  const qs=broker.getQuotes(),eq=paper.equity(qs), pnl=eq-paper.dayStartEquity, positions=Object.values(paper.positions);
  $('#equity').textContent=won(eq);$('#equityPnl').textContent=`오늘 손익 ${won(pnl)} (${pct(pnl/paper.dayStartEquity)})`;
  $('#cash').textContent=won(paper.cash);$('#positionCount').textContent=positions.length;$('#exposure').textContent=`투자비중 ${pct((eq-paper.cash)/eq)}`;$('#todayOrders').textContent=paper.trades.length;$('#winLoss').textContent=`실현손익 ${won(paper.realized)}`;
  $('#positionsBody').innerHTML=positions.length?positions.map(p=>{const q=qs.find(x=>x.code===p.code);const val=q.price*p.qty,pnl=(q.price-p.avg)*p.qty;return `<tr><td><b>${p.name}</b><br><span class="neutral">${p.code}</span></td><td>${p.qty}</td><td>${won(p.avg)}</td><td>${won(q.price)}</td><td class="${pnl>=0?'up':'down'}">${won(pnl)}</td><td>${pct(val/eq)}</td><td><button class="btn small sell-btn" data-code="${p.code}">전량매도</button></td></tr>`}).join(''):'<tr><td colspan="7" class="neutral">보유 종목이 없습니다.</td></tr>';
  document.querySelectorAll('.sell-btn').forEach(b=>b.onclick=()=>manualSell(b.dataset.code));
  $('#tradesBody').innerHTML=paper.trades.slice(0,20).map(t=>`<tr><td>${new Date(t.ts).toLocaleTimeString('ko-KR',{hour12:false})}</td><td>${t.name}</td><td class="${t.side==='BUY'?'up':'down'}">${t.side}</td><td>${t.qty}</td><td>${won(t.price)}</td><td>${t.reason}</td></tr>`).join('')||'<tr><td colspan="6" class="neutral">아직 주문이 없습니다.</td></tr>';
  const daily=(eq-paper.dayStartEquity)/paper.dayStartEquity;
  const blocked=risk.halted||daily<=-CONFIG.risk.dailyLossPct||paper.lossStreak>=CONFIG.risk.maxLossStreak;
  $('#riskState').textContent=blocked?(risk.haltReason||'RISK BLOCKED'):'NORMAL';$('#riskState').className='risk-state '+(blocked?'badbox':'okbox');
  $('#strategyStats').innerHTML=`<div><small>매수 점수</small><strong>${CONFIG.strategy.buyScore}+</strong></div><div><small>매도 점수</small><strong>${CONFIG.strategy.sellScore}-</strong></div><div><small>익절 기준</small><strong>${pct(CONFIG.strategy.takeProfitPct)}</strong></div><div><small>손절 기준</small><strong>-${pct(CONFIG.strategy.stopLossPct)}</strong></div><div><small>연속 손실</small><strong>${paper.lossStreak}/${CONFIG.risk.maxLossStreak}</strong></div><div><small>일일 손익</small><strong>${pct(daily)}</strong></div>`;
}

$('#scanBtn').onclick=scan;
$('#autoToggle').onchange=e=>{auto=e.target.checked;if(auto&&risk.halted)risk.resume();$('#autoStateText').textContent=auto?'ON · PAPER':'OFF';log(`AUTO TRADE ${auto?'ON':'OFF'}`);};
$('#killBtn').onclick=()=>{auto=false;$('#autoToggle').checked=false;$('#autoStateText').textContent='EMERGENCY STOP';risk.halt('사용자 긴급 정지');renderAll();log('EMERGENCY STOP · 신규 자동주문 차단');};
$('#resetBtn').onclick=()=>{if(confirm('Paper 계좌와 주문 내역을 초기화할까요?')){paper.reset();risk.resume();auto=false;$('#autoToggle').checked=false;$('#autoStateText').textContent='OFF';renderAll();log('Paper 계좌 초기화');}};
$('#maxOrderText').textContent=won(CONFIG.risk.maxOrderWon);$('#maxPositionText').textContent=pct(CONFIG.risk.maxPositionPct);$('#maxPositionsText').textContent=CONFIG.risk.maxPositions+'종목';$('#dailyLossText').textContent='-'+pct(CONFIG.risk.dailyLossPct);$('#maxLossStreakText').textContent=CONFIG.risk.maxLossStreak+'회';

scan();renderAll();log('Stock Trader v0.1 시작 · MockBroker 연결');
setInterval(()=>{broker.tick();scan();renderAll();},CONFIG.scanIntervalMs);
if('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(()=>{});
