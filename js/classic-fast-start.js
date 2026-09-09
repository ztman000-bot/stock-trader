// Classic mobile fast-start layer v2.
// UI transport optimization only: server Paper strategy/order logic is untouched.
(()=>{
  const STATUS_KEY='stock-trader-classic-status-cache-v2';
  const LEGACY_STATUS_KEY='stock-trader-classic-status-cache-v1';
  const STATUS_MEMORY_TTL=7000;
  const STATUS_DISK_MAX_AGE=30*60*1000;
  const BARS_MEMORY_TTL=30000;
  const nativeFetch=window.fetch.bind(window);
  let statusInFlight=null;
  let statusMemo=null;
  let diskServed=false;
  let diskSnapshot=null;
  const barsMemo=new Map();

  const won=n=>'₩'+Math.round(Number(n||0)).toLocaleString('ko-KR');
  const pct=n=>(Number(n||0)>=0?'+':'')+Number(n||0).toFixed(2)+'%';
  const actionClass=a=>a==='BUY_CANDIDATE'?'up':a==='SETUP'||a==='SHADOW_ONLY'?'warn':a==='BLOCKED'||a==='SAFETY_WAIT'?'down':'neutral';

  function compactStatus(data){
    if(!data||typeof data!=='object')return null;
    return {
      ok:data.ok,
      version:data.version,
      serverTime:data.serverTime,
      tradingEnabled:false,
      collector:data.collector||{},
      usCollector:data.usCollector||{},
      usQuotes:(data.usQuotes||[]).slice(0,20),
      universe:data.universe||{},
      paperLoop:data.paperLoop||{},
      daily:data.daily||{},
      validation:data.validation||{},
      positions:data.positions||[],
      scanner:(data.scanner||[]).slice(0,10),
      quotes:(data.quotes||[]).slice(0,20),
      recentTrades:(data.recentTrades||[]).slice(0,30),
      entryStart:data.entryStart,
      entryCutoff:data.entryCutoff,
      eodExit:data.eodExit,
      risk:data.risk||{}
    };
  }

  function readDiskStatus(){
    if(diskSnapshot)return diskSnapshot;
    try{
      const raw=localStorage.getItem(STATUS_KEY);
      if(!raw)return null;
      const saved=JSON.parse(raw),age=Date.now()-Number(saved.at||0);
      if(age<0||age>STATUS_DISK_MAX_AGE||!saved.data)return null;
      diskSnapshot=saved;
      return saved;
    }catch{return null}
  }

  function saveStatus(data){
    try{
      const compact=compactStatus(data);
      if(!compact)return;
      const saved={at:Date.now(),data:compact};
      localStorage.setItem(STATUS_KEY,JSON.stringify(saved));
      localStorage.removeItem(LEGACY_STATUS_KEY);
      diskSnapshot=saved;
    }catch{}
  }

  function renderCached(data,savedAt){
    const body=document.querySelector('#scannerBody');
    if(!body||!data)return false;
    const rows=(data.scanner||[]).slice(0,10);
    if(!rows.length)return false;
    body.innerHTML=rows.map((s,i)=>{
      const x=s.indicators||{},m=s.market||{};
      const activity=m.activityScore!=null?`활성 ${Number(m.activityScore).toFixed(0)} · ${Number(m.turnoverEok||0).toFixed(0)}억`:'';
      const why=(s.blockedReasons||[]).slice(0,2).join(' · ')||'최근 정상 스캔';
      return `<tr class="pick-row cached-pick-row" data-code="${s.code}"><td>${i+1}</td><td><b>${s.name||s.code}</b><br><small>${s.code}</small></td><td>${x.price?won(x.price):'-'}</td><td>${m.changeRate!=null?pct(m.changeRate):'-'}</td><td><span class="score">${Number(s.score||0).toFixed(1)}</span><br><small>${why}</small><br><small class="neutral">${activity}</small></td><td>${x.vwap?won(x.vwap):'-'}</td><td>${x.volumeRatio?Number(x.volumeRatio).toFixed(2)+'x':'-'}</td><td class="${actionClass(s.action)}">${s.action||'WATCH'}</td><td>동기화중</td></tr>`;
    }).join('');
    const desc=document.querySelector('.main-grid article .panel-head p');
    if(desc){
      const t=new Date(savedAt).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',hour12:false});
      desc.textContent=`최근 ${t} 화면 즉시 표시 · 공기계 최신값 동기화 중`;
    }
    document.documentElement.dataset.fastStart='cached';
    return true;
  }

  function markCachedUi(savedAt){
    document.documentElement.dataset.fastStart='cached';
    setTimeout(()=>{
      const t=new Date(savedAt).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
      const badge=document.querySelector('#systemBadge');
      if(badge){badge.textContent='CACHED · SYNCING';badge.className='badge'}
      const box=document.querySelector('#nhStatusText');
      if(box)box.innerHTML=`<b>최근 화면 ${t}</b> · 공기계 최신 상태 동기화 중 · 매매 엔진은 공기계에서 계속 실행`;
    },80);
  }

  function markFreshUi(){
    document.documentElement.dataset.fastStart='fresh';
    window.dispatchEvent(new CustomEvent('stocktrader:status-fresh'));
  }

  function restoreCached(){
    const saved=readDiskStatus();
    if(saved)renderCached(saved.data,saved.at);
    return saved;
  }

  function snapResponse(res,text){
    return {at:Date.now(),status:res.status,statusText:res.statusText,headers:[...res.headers.entries()],text};
  }
  function jsonSnap(data,at=Date.now()){
    return {at,status:200,statusText:'OK',headers:[['content-type','application/json; charset=utf-8'],['x-stocktrader-cache','disk']],text:JSON.stringify(data)};
  }
  function responseFrom(s){return new Response(s.text,{status:s.status,statusText:s.statusText,headers:s.headers})}
  async function fetchSnap(input,init){
    const res=await nativeFetch(input,init);
    const text=await res.clone().text();
    return snapResponse(res,text);
  }

  function refreshStatusInBackground(input,init){
    if(statusInFlight)return statusInFlight;
    statusInFlight=fetchSnap(input,init).then(s=>{
      if(s.status>=200&&s.status<300){
        statusMemo=s;
        try{saveStatus(JSON.parse(s.text))}catch{}
        markFreshUi();
      }
      return s;
    }).finally(()=>{statusInFlight=null});
    return statusInFlight;
  }

  async function normalizeUpdateResponse(input,init){
    const res=await nativeFetch(input,init);
    let data=null;
    try{data=await res.clone().json()}catch{return res}
    if(!data||typeof data!=='object')return res;

    if(res.status===409&&data.error==='이미 업데이트 중입니다.'){
      const headers=new Headers(res.headers);
      headers.set('content-type','application/json; charset=utf-8');
      return new Response(JSON.stringify({ok:true,message:'이미 업데이트 중입니다. 완료될 때까지 기다립니다.',alreadyRunning:true}),{status:200,statusText:'OK',headers});
    }

    if(!res.ok&&data.error&&!data.detail){
      data.detail=data.error;
      const headers=new Headers(res.headers);
      headers.set('content-type','application/json; charset=utf-8');
      return new Response(JSON.stringify(data),{status:res.status,statusText:res.statusText,headers});
    }
    return res;
  }

  window.fetch=async function(input,init={}){
    let url;
    try{url=new URL(typeof input==='string'?input:input.url,location.href)}catch{return nativeFetch(input,init)}
    const method=String(init?.method||(typeof input!=='string'&&input.method)||'GET').toUpperCase();
    if(url.origin!==location.origin)return nativeFetch(input,init);

    if(method==='POST'&&(url.pathname==='/api/system/update'||url.pathname==='/api/system/update/run')){
      return normalizeUpdateResponse(input,init);
    }
    if(method!=='GET')return nativeFetch(input,init);

    if(url.pathname==='/api/mobile/status'){
      const now=Date.now();
      if(statusMemo&&now-statusMemo.at<STATUS_MEMORY_TTL)return responseFrom(statusMemo);

      // First paint: serve the last known full dashboard immediately, then refresh
      // from the dedicated phone in the background. Cached state is visibly marked
      // and is never consumed by the server Paper/Control engine.
      if(!diskServed){
        diskServed=true;
        const saved=readDiskStatus();
        if(saved){
          markCachedUi(saved.at);
          refreshStatusInBackground(input,init).catch(()=>{});
          return responseFrom(jsonSnap(saved.data,saved.at));
        }
      }

      if(!statusInFlight)refreshStatusInBackground(input,init);
      return responseFrom(await statusInFlight);
    }

    if(url.pathname.startsWith('/api/market/bars/')){
      url.searchParams.delete('u');
      const key=url.pathname+'?'+url.searchParams.toString(),cached=barsMemo.get(key),now=Date.now();
      if(cached&&now-cached.at<BARS_MEMORY_TTL)return responseFrom(cached);
      const s=await fetchSnap(input,init);
      if(s.status>=200&&s.status<300)barsMemo.set(key,s);
      return responseFrom(s);
    }

    return nativeFetch(input,init);
  };

  window.stockClassicFastStart={
    get statusPending(){return !!statusInFlight},
    get source(){return document.documentElement.dataset.fastStart||'network'},
    restore:restoreCached,
    clear(){
      statusMemo=null;diskSnapshot=null;diskServed=false;barsMemo.clear();
      try{localStorage.removeItem(STATUS_KEY);localStorage.removeItem(LEGACY_STATUS_KEY)}catch{}
    }
  };
  restoreCached();
})();
