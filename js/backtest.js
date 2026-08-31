import {scoreSymbol} from './strategy.js';
export function backtestSymbol(q,history,cfg){
 if(history.length<70)return {trades:0,wins:0,winRate:0,returnPct:0,maxDrawdown:0};
 let cash=1_000_000,qty=0,entry=0,trades=0,wins=0,peak=cash,maxDD=0;
 for(let n=60;n<history.length;n++){
  const h=history.slice(0,n+1),c=h.at(-1),qq={...q,price:c.close,change:(c.close-h[0].open)/h[0].open};const s=scoreSymbol(qq,h);
  if(!qty&&s.total>=cfg.buyScore){qty=Math.floor(cash/c.close);if(qty){cash-=qty*c.close;entry=c.close;}}
  else if(qty){const pnl=(c.close-entry)/entry;if(pnl>=cfg.takeProfitPct||pnl<=-cfg.stopLossPct||s.total<=cfg.sellScore){cash+=qty*c.close;trades++;if(c.close>entry)wins++;qty=0;entry=0;}}
  const equity=cash+qty*c.close;peak=Math.max(peak,equity);maxDD=Math.min(maxDD,(equity-peak)/peak);
 }
 if(qty)cash+=qty*history.at(-1).close;
 return {trades,wins,winRate:trades?wins/trades:0,returnPct:(cash/1_000_000)-1,maxDrawdown:maxDD};
}
