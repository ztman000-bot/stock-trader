function clamp(v,min=0,max=100){ return Math.max(min,Math.min(max,v)); }
function pseudo(code,k){
  let n=0; for(const c of code) n=(n*31+c.charCodeAt(0))%9973;
  const x=Math.sin((n+k)*12.9898)*43758.5453; return x-Math.floor(x);
}
export function scoreSymbol(q, cycle=0){
  const change=q.change;
  const trend=clamp(50 + change*700 + (pseudo(q.code,cycle)-.5)*18);
  const momentum=clamp(52 + change*900 + (pseudo(q.code,cycle+2)-.5)*24);
  const volume=clamp(45 + (pseudo(q.code,cycle+4))*50);
  const pricePos=clamp(50 + change*500 + (pseudo(q.code,cycle+6)-.5)*35);
  const risk=clamp(85 - Math.abs(change)*900 - (pseudo(q.code,cycle+8))*15);
  const total=Math.round(trend*.25+momentum*.25+volume*.18+pricePos*.17+risk*.15);
  const verdict=total>=88?'강한 매수 후보':total>=82?'매수 관심':total>=72?'관찰':'대기';
  return {code:q.code,name:q.name,total,verdict,parts:{trend,momentum,volume,pricePos,risk}};
}

export function evaluateExit(position, quote, score, cfg){
  const pnlPct=(quote.price-position.avg)/position.avg;
  if(pnlPct<=-cfg.stopLossPct) return {sell:true,reason:`손절 ${fmtPct(pnlPct)}`};
  if(pnlPct>=cfg.takeProfitPct) return {sell:true,reason:`익절 ${fmtPct(pnlPct)}`};
  if(score.total<=cfg.sellScore) return {sell:true,reason:`점수 하락 ${score.total}`};
  return {sell:false};
}
const fmtPct=v=>(v*100).toFixed(2)+'%';
