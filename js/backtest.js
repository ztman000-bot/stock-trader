import {scoreSymbol,evaluateExit} from './strategy.js';
export function backtestSymbol(q,history,cfg){
 if(history.length<70)return{trades:0,wins:0,winRate:0,returnPct:0,maxDrawdown:0};
 let cash=1_000_000,pos=null,trades=0,wins=0,peak=cash,maxDD=0;
 for(let n=60;n<history.length;n++){
  const h=history.slice(0,n+1),c=h.at(-1),qq={...q,price:c.close,change:(c.close-h[0].open)/h[0].open},s=scoreSymbol(qq,h,cfg);
  if(!pos&&s.entryReady){const qty=Math.floor(cash/c.close);if(qty){cash-=qty*c.close;pos={qty,avg:c.close,peak:c.close};}}
  else if(pos){const ex=evaluateExit(pos,qq,s,cfg);if(ex.sell){cash+=pos.qty*c.close;trades++;if(c.close>pos.avg)wins++;pos=null;}}
  const equity=cash+(pos?pos.qty*c.close:0);peak=Math.max(peak,equity);maxDD=Math.min(maxDD,(equity-peak)/peak);
 }
 if(pos)cash+=pos.qty*history.at(-1).close;
 return{trades,wins,winRate:trades?wins/trades:0,returnPct:(cash/1_000_000)-1,maxDrawdown:maxDD};
}
