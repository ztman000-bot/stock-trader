export class PaperAccount {
  constructor(initialCash){this.initialCash=initialCash;this.load();}
  load(){
    const raw=localStorage.getItem('stockTraderV01');
    if(raw){Object.assign(this,JSON.parse(raw));}
    else{this.cash=this.initialCash;this.positions={};this.trades=[];this.realized=0;this.lossStreak=0;this.dayStartEquity=this.initialCash;}
  }
  save(){localStorage.setItem('stockTraderV01',JSON.stringify({cash:this.cash,positions:this.positions,trades:this.trades,realized:this.realized,lossStreak:this.lossStreak,dayStartEquity:this.dayStartEquity}));}
  reset(){localStorage.removeItem('stockTraderV01');this.load();}
  equity(quotes){return this.cash+Object.values(this.positions).reduce((sum,p)=>{const q=quotes.find(x=>x.code===p.code);return sum+(q?q.price:0)*p.qty},0)}
  buy(q,qty,reason){const cost=q.price*qty;if(cost>this.cash)return false;const p=this.positions[q.code];if(p){const total=p.avg*p.qty+cost;p.qty+=qty;p.avg=total/p.qty;}else this.positions[q.code]={code:q.code,name:q.name,qty,avg:q.price};this.cash-=cost;this.trades.unshift({ts:Date.now(),code:q.code,name:q.name,side:'BUY',qty,price:q.price,reason});this.save();return true;}
  sell(q,qty,reason){const p=this.positions[q.code];if(!p||qty<=0)return false;qty=Math.min(qty,p.qty);const pnl=(q.price-p.avg)*qty;this.cash+=q.price*qty;p.qty-=qty;if(p.qty===0)delete this.positions[q.code];this.realized+=pnl;this.lossStreak=pnl<0?this.lossStreak+1:0;this.trades.unshift({ts:Date.now(),code:q.code,name:q.name,side:'SELL',qty,price:q.price,reason,pnl});this.save();return true;}
}
