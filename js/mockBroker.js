const base=[
['005930','삼성전자',72400],['000660','SK하이닉스',198500],['068270','셀트리온',186500],['035420','NAVER',234000],['035720','카카오',58500],['005380','현대차',276000],['000270','기아',126000],['051910','LG화학',346000],['006400','삼성SDI',372000],['105560','KB금융',89500],['055550','신한지주',54800],['207940','삼성바이오로직스',988000],['012450','한화에어로스페이스',356000],['042700','한미반도체',142000],['086520','에코프로',87200]];
const roundPrice=p=>Math.max(100,Math.round(p/10)*10);
export class MockBroker{
 constructor(){
  this.symbols=base.map(([code,name,price],idx)=>{
    const history=[];let p=price*.92;
    for(let i=0;i<140;i++){const drift=(Math.sin((i+idx)*.31)*.003)+(Math.random()-.48)*.013;p=roundPrice(p*(1+drift));const spread=p*(.003+Math.random()*.009);history.push({ts:Date.now()-(140-i)*60000,open:roundPrice(p*(1+(Math.random()-.5)*.004)),high:roundPrice(p+spread),low:roundPrice(Math.max(100,p-spread)),close:p,volume:Math.round((850000+idx*35000)*(0.65+Math.random()*.9))});}
    const last=history.at(-1); return {code,name,price:last.close,prev:last.close,open:history[0].open,volume:last.volume,history};
  }); this.connected=true;
 }
 tick(){for(const s of this.symbols){const drift=(Math.random()-.485)*.010;s.prev=s.price;s.price=roundPrice(s.price*(1+drift));s.volume=Math.round(s.volume*(.94+Math.random()*.15));const o=s.prev,spread=s.price*(.002+Math.random()*.005);s.history.push({ts:Date.now(),open:o,high:roundPrice(Math.max(o,s.price)+spread),low:roundPrice(Math.max(100,Math.min(o,s.price)-spread)),close:s.price,volume:s.volume});if(s.history.length>220)s.history.shift();}}
 getQuotes(){return this.symbols.map(s=>({code:s.code,name:s.name,price:s.price,prev:s.prev,open:s.open,volume:s.volume,change:(s.price-s.open)/s.open}));}
 getQuote(code){return this.getQuotes().find(x=>x.code===code);}
 getHistory(code){return (this.symbols.find(x=>x.code===code)?.history||[]).map(x=>({...x}));}
}
