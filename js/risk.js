export class RiskEngine {
  constructor(cfg,protectedSymbols=[]){this.cfg=cfg;this.protectedSymbols=new Set(protectedSymbols);this.halted=false;this.haltReason='';}
  halt(reason){this.halted=true;this.haltReason=reason;}
  resume(){this.halted=false;this.haltReason='';}
  validateBuy({code,price,qty,equity,positions,dailyPnlPct,lossStreak}){
    if(this.protectedSymbols.has(code))return{ok:false,reason:'PROTECTED HOLDING 종목'};
    if(this.halted)return{ok:false,reason:this.haltReason||'Risk halt'};
    if(lossStreak>=this.cfg.maxLossStreak)return{ok:false,reason:`${this.cfg.maxLossStreak}연패 당일 거래 중지`};
    if(dailyPnlPct<=-this.cfg.dailyLossPct)return{ok:false,reason:'일일 손실 한도 도달'};
    if(price*qty>this.cfg.maxOrderWon)return{ok:false,reason:'1회 최대 주문 초과'};
    if(positions.length>=this.cfg.maxPositions)return{ok:false,reason:'최대 보유 종목 초과'};
    if((price*qty)/equity>this.cfg.maxPositionPct)return{ok:false,reason:'종목 비중 제한 초과'};
    return{ok:true};
  }
  shouldDailyLock(lossStreak){return lossStreak>=this.cfg.maxLossStreak;}
}
