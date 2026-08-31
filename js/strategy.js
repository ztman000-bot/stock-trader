import {calcIndicators} from './indicators.js';
const clamp=(v,min=0,max=100)=>Math.max(min,Math.min(max,v));
export function scoreSymbol(q,history){
  const i=calcIndicators(history);
  const trend=clamp(50+(i.ema5-i.ema20)/i.ema20*900+(i.ema20-i.ema60)/i.ema60*550);
  const momentum=clamp(50+(i.rsi-50)*1.4+i.momentum*700);
  const volume=clamp(45+(i.volumeRatio-1)*42);
  const dmi=clamp(50+(i.plusDI-i.minusDI)*1.3+(i.adx-20)*.8);
  const risk=clamp(88-Math.max(0,i.rsi-72)*2.2-Math.abs(q.change)*500);
  const total=Math.round(trend*.28+momentum*.24+volume*.16+dmi*.20+risk*.12);
  const verdict=total>=86?'강한 매수 후보':total>=80?'매수 관심':total>=70?'관찰':'대기';
  return {code:q.code,name:q.name,total,verdict,ind:i,parts:{trend,momentum,volume,dmi,risk}};
}
export function evaluateExit(position,quote,score,cfg){
  const pnlPct=(quote.price-position.avg)/position.avg;
  if(pnlPct<=-cfg.stopLossPct) return {sell:true,reason:`손절 ${fmtPct(pnlPct)}`};
  if(pnlPct>=cfg.takeProfitPct) return {sell:true,reason:`익절 ${fmtPct(pnlPct)}`};
  if(score.total<=cfg.sellScore) return {sell:true,reason:`점수 하락 ${score.total}`};
  if(score.ind.rsi>78&&score.ind.plusDI<score.ind.minusDI) return {sell:true,reason:'과열+DMI 약화'};
  return {sell:false};
}
const fmtPct=v=>(v*100).toFixed(2)+'%';
