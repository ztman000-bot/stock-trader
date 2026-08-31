export class PaperAccount {
  constructor(initialCash,profitSplit){this.initialCash=initialCash;this.profitSplit=profitSplit;this.load();}
  load(){
    const raw=localStorage.getItem('stockTraderV03');
    if(raw){Object.assign(this,JSON.parse(raw));return;}
    this.cash=this.initialCash;this.positions={};this.trades=[];this.realized=0;this.lossStreak=0;this.dayStartEquity=this.initialCash;this.vault=0;this.reserve=0;this.reinvested=0;this.allocatedProfit=0;
  }
  save(){localStorage.setItem('stockTraderV03',JSON.stringify({cash:this.cash,positions:this.positions,trades:this.trades,realized:this.realized,lossStreak:this.lossStreak,dayStartEquity:this.dayStartEquity,vault:this.vault,reserve:this.reserve,reinvested:this.reinvested,allocatedProfit:this.allocatedProfit}));}
  reset(){localStorage.removeItem('stockTraderV03');this.load();}
  equity(quotes){return this.cash+Object.values(this.positions).reduce((sum,p)=>{const q=quotes.find(x=>x.code===p.code);return sum+(q?q.price:0)*p.qty},0)}
  totalProtectedEquity(quotes){return this.equity(quotes)+this.vault+this.reserve}
  buy(q,qty,reason,meta={}){const cost=q.price*qty;if(cost>this.cash)return false;const p=this.positions[q.code];if(p){const total=p.avg*p.qty+cost;p.qty+=qty;p.avg=total/p.qty;p.peak=Math.max(p.peak||p.avg,q.price)}else this.positions[q.code]={code:q.code,name:q.name,qty,avg:q.price,peak:q.price,entryMeta:meta};this.cash-=cost;this.trades.unshift({ts:Date.now(),code:q.code,name:q.name,side:'BUY',qty,price:q.price,reason,meta});this.save();return true;}
  sell(q,qty,reason){const p=this.positions[q.code];if(!p||qty<=0)return null;qty=Math.min(qty,p.qty);const avg=p.avg,pnl=(q.price-avg)*qty,pnlPct=(q.price-avg)/avg,entryMeta=p.entryMeta||{};this.cash+=q.price*qty;p.qty-=qty;if(p.qty===0)delete this.positions[q.code];this.realized+=pnl;this.lossStreak=pnl<0?this.lossStreak+1:0;const split=this.allocateNewProfit();const trade={ts:Date.now(),code:q.code,name:q.name,side:'SELL',qty,price:q.price,reason,pnl,pnlPct,entryMeta,split};this.trades.unshift(trade);this.save();return trade;}
  allocateNewProfit(){if(this.realized<=this.allocatedProfit)return null;const delta=this.realized-this.allocatedProfit;this.allocatedProfit=this.realized;const vault=delta*this.profitSplit.vaultPct,reserve=delta*this.profitSplit.reservePct,reinvested=delta*this.profitSplit.reinvestPct;this.vault+=vault;this.reserve+=reserve;this.reinvested+=reinvested;this.cash-=vault+reserve;return{profit:delta,vault,reserve,reinvested};}
}
