import {failureTags} from './strategy.js';

export class LearningEngine{
  constructor(cfg){this.cfg=cfg;this.key='stockTraderLearningV03';this.load();}
  load(){const raw=localStorage.getItem(this.key);const d=raw?JSON.parse(raw):{};this.lossCases=d.lossCases||[];this.shadowOpen=d.shadowOpen||{};this.shadowClosed=d.shadowClosed||[];}
  save(){localStorage.setItem(this.key,JSON.stringify({lossCases:this.lossCases.slice(-300),shadowOpen:this.shadowOpen,shadowClosed:this.shadowClosed.slice(-500)}));}
  reset(){localStorage.removeItem(this.key);this.load();}
  recordLoss(trade,score){if(!trade||trade.pnl>=0)return;this.lossCases.push({ts:trade.ts,code:trade.code,name:trade.name,pnlPct:trade.pnlPct,reason:trade.reason,tags:failureTags(score,trade.reason),snapshot:{rsi:score.ind.rsi,adx:score.ind.adx,volumeRatio:score.ind.volumeRatio,vwapGap:(score.quote.price-score.ind.vwap)/score.ind.vwap}});this.save();}
  observeShadow(scores,tick){
    for(const s of scores.slice(0,10))if(s.entryReady&&!this.shadowOpen[s.code])this.shadowOpen[s.code]={code:s.code,name:s.name,entry:s.quote.price,peak:s.quote.price,startTick:tick,entryScore:s.total};
    for(const [code,p] of Object.entries({...this.shadowOpen})){
      const s=scores.find(x=>x.code===code);if(!s)continue;const price=s.quote.price;p.peak=Math.max(p.peak,price);const ret=(price-p.entry)/p.entry;
      let outcome='';if(ret<=-this.cfg.stopLossPct)outcome='LOSS';else if(ret>=this.cfg.takeProfitPct)outcome='WIN';else if(tick-p.startTick>=this.cfg.shadowBars)outcome=ret>=0?'WIN':'LOSS';
      if(outcome){this.shadowClosed.push({...p,exit:price,ret,outcome,endTick:tick});delete this.shadowOpen[code];}
    }
    this.save();
  }
  stats(){const n=this.shadowClosed.length,w=this.shadowClosed.filter(x=>x.outcome==='WIN').length;const tagCounts={};for(const c of this.lossCases)for(const t of c.tags)tagCounts[t]=(tagCounts[t]||0)+1;const topTags=Object.entries(tagCounts).sort((a,b)=>b[1]-a[1]).slice(0,3);const avgLoss=this.lossCases.length?this.lossCases.reduce((a,b)=>a+b.pnlPct,0)/this.lossCases.length:0;return{lossCases:this.lossCases.length,shadowTrades:n,shadowWinRate:n?w/n:0,avgLoss,topTags};}
  recommendation(){const s=this.stats();if(s.lossCases<5)return'학습 데이터가 아직 적습니다. 실전 규칙은 자동 변경하지 않고 실패 사례와 Shadow 결과만 축적합니다.';const top=s.topTags[0];if(!top)return'추가 데이터 축적 중';return`가장 빈번한 실패 원인: ${top[0]} ${top[1]}회. 이 조건을 강화한 대안 전략을 Shadow Trading에서 우선 검증하세요.`;}
}
