import {calcIndicators} from './indicators.js';

export function scoreSymbol(q,history,cfg){
  const i=calcIndicators(history), recent=history.slice(-(cfg.breakoutLookback+1),-1), priorHigh=Math.max(...recent.map(x=>x.high)), breakout=q.price>priorHigh;
  const checks={
    aboveVwap:q.price>i.vwap,
    emaTrend:i.ema9>i.ema20,
    volume:i.volumeRatio>=cfg.entryVolumeRatio,
    breakout,
    rsi:i.rsi>=cfg.rsiMin&&i.rsi<=cfg.rsiMax,
    trendStrength:i.adx>=cfg.adxMin&&i.plusDI>i.minusDI,
  };
  const core=[checks.aboveVwap,checks.emaTrend,checks.volume,checks.breakout], corePassed=core.filter(Boolean).length;
  const confirm=[checks.rsi,checks.trendStrength].filter(Boolean).length;
  const total=corePassed*20+confirm*10;
  const entryReady=corePassed===4&&confirm>=1;
  const verdict=entryReady?'진입 가능':corePassed>=3?'대기·확인':corePassed>=2?'관찰':'제외';
  return {code:q.code,name:q.name,total,verdict,entryReady,checks,ind:i,priorHigh};
}

export function evaluateExit(position,quote,score,cfg){
  const pnlPct=(quote.price-position.avg)/position.avg;
  position.peak=Math.max(position.peak||position.avg,quote.price);
  const peakPct=(position.peak-position.avg)/position.avg;
  const trailPct=(quote.price-position.peak)/position.peak;
  if(pnlPct<=-cfg.stopLossPct)return{sell:true,reason:`손절 ${fmtPct(pnlPct)}`};
  if(pnlPct>=cfg.takeProfitPct)return{sell:true,reason:`목표익절 ${fmtPct(pnlPct)}`};
  if(peakPct>=cfg.trailingTriggerPct&&trailPct<=-cfg.trailingGapPct)return{sell:true,reason:`트레일링 ${fmtPct(trailPct)}`};
  if(!score.checks.aboveVwap&&!score.checks.emaTrend)return{sell:true,reason:'VWAP+EMA 추세 이탈'};
  return{sell:false};
}

export function failureTags(score,reason=''){
  const tags=[];
  if(score.ind.volumeRatio<1.5)tags.push('거래량 부족');
  if(score.ind.rsi>75)tags.push('과매수 추격');
  if(score.ind.rsi<50)tags.push('모멘텀 부족');
  if(score.ind.adx<18)tags.push('추세강도 부족');
  if(!score.checks.aboveVwap)tags.push('VWAP 이탈');
  if(!score.checks.breakout)tags.push('돌파 실패');
  if(reason.includes('손절'))tags.push('손절 도달');
  return tags.length?tags:['기타'];
}
const fmtPct=v=>(v*100).toFixed(2)+'%';
