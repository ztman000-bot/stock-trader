export const sma=(values,period)=>{
  if(values.length<period) return null;
  return values.slice(-period).reduce((a,b)=>a+b,0)/period;
};
export const ema=(values,period)=>{
  if(!values.length) return null;
  const k=2/(period+1); let v=values[0];
  for(let i=1;i<values.length;i++) v=values[i]*k+v*(1-k);
  return v;
};
export function rsi(values,period=14){
  if(values.length<=period) return 50;
  let gains=0,losses=0;
  for(let i=values.length-period;i<values.length;i++){
    const d=values[i]-values[i-1]; if(d>=0) gains+=d; else losses-=d;
  }
  if(losses===0) return 100;
  const rs=(gains/period)/(losses/period); return 100-(100/(1+rs));
}
export function adxDmi(candles,period=14){
  if(candles.length<period+2) return {adx:0,plusDI:0,minusDI:0};
  const rows=[];
  for(let i=1;i<candles.length;i++){
    const c=candles[i],p=candles[i-1];
    const up=c.high-p.high,down=p.low-c.low;
    const plusDM=up>down&&up>0?up:0,minusDM=down>up&&down>0?down:0;
    const tr=Math.max(c.high-c.low,Math.abs(c.high-p.close),Math.abs(c.low-p.close));
    rows.push({tr,plusDM,minusDM});
  }
  const recent=rows.slice(-period);
  const tr=recent.reduce((a,b)=>a+b.tr,0);
  if(!tr) return {adx:0,plusDI:0,minusDI:0};
  const plusDI=100*recent.reduce((a,b)=>a+b.plusDM,0)/tr;
  const minusDI=100*recent.reduce((a,b)=>a+b.minusDM,0)/tr;
  const dx=100*Math.abs(plusDI-minusDI)/Math.max(plusDI+minusDI,1e-9);
  return {adx:dx,plusDI,minusDI};
}
export function calcIndicators(history){
  const closes=history.map(x=>x.close);
  const e5=ema(closes.slice(-40),5),e20=ema(closes.slice(-60),20),e60=ema(closes.slice(-120),60);
  const r=rsi(closes,14),d=adxDmi(history,14);
  const latest=history.at(-1),prev=history.at(-2)||latest;
  const volAvg=sma(history.slice(-20).map(x=>x.volume),Math.min(20,history.length))||latest.volume;
  return {ema5:e5,ema20:e20,ema60:e60,rsi:r,adx:d.adx,plusDI:d.plusDI,minusDI:d.minusDI,volumeRatio:latest.volume/Math.max(volAvg,1),momentum:(latest.close-prev.close)/Math.max(prev.close,1)};
}
