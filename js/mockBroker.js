const base = [
  ['005930','삼성전자',72400],['000660','SK하이닉스',198500],['068270','셀트리온',186500],['035420','NAVER',234000],
  ['035720','카카오',58500],['005380','현대차',276000],['000270','기아',126000],['051910','LG화학',346000],
  ['006400','삼성SDI',372000],['105560','KB금융',89500],['055550','신한지주',54800],['207940','삼성바이오로직스',988000],
  ['012450','한화에어로스페이스',356000],['042700','한미반도체',142000],['086520','에코프로',87200]
];

export class MockBroker {
  constructor(){
    this.symbols = base.map(([code,name,price],i)=>({code,name,price,prev:price,volume:1000000+i*45000,seed:i+1,open:price}));
    this.connected = true;
  }
  tick(){
    for(const s of this.symbols){
      const drift=(Math.random()-.49)*0.012;
      s.prev=s.price;
      s.price=Math.max(100, Math.round(s.price*(1+drift)/10)*10);
      s.volume=Math.round(s.volume*(1+(Math.random()-.45)*.03));
    }
  }
  getQuotes(){ return this.symbols.map(s=>({...s, change:(s.price-s.open)/s.open})); }
  getQuote(code){ return this.getQuotes().find(x=>x.code===code); }
}
