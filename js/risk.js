export class RiskEngine {
  constructor(cfg){this.cfg=cfg;this.halted=false;this.haltReason='';}
  halt(reason){this.halted=true;this.haltReason=reason;}
  resume(){this.halted=false;this.haltReason='';}
  validateBuy({price,qty,equity,positions,dailyPnlPct,lossStreak}){
    if(this.halted) return {ok:false,reason:this.haltReason||'Risk halt'};
    if(dailyPnlPct<=-this.cfg.dailyLossPct) return {ok:false,reason:'일일 손실 한도 도달'};
    if(lossStreak>=this.cfg.maxLossStreak) return {ok:false,reason:'연속 손실 한도 도달'};
    if(price*qty>this.cfg.maxOrderWon) return {ok:false,reason:'1회 최대 주문 초과'};
    if(positions.length>=this.cfg.maxPositions) return {ok:false,reason:'최대 보유 종목 초과'};
    if((price*qty)/equity>this.cfg.maxPositionPct) return {ok:false,reason:'종목 비중 제한 초과'};
    return {ok:true};
  }
}
