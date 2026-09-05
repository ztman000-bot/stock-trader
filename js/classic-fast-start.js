// Classic mobile fast-start layer.
// UI transport optimization only: server Paper strategy/order logic is untouched.
(()=>{
  const STATUS_KEY='stock-trader-classic-status-cache-v1';
  const STATUS_MEMORY_TTL=7000;
  const STATUS_DISK_MAX_AGE=10*60*1000;
  const BARS_MEMORY_TTL=30000;
  const nativeFetch=window.fetch.bind(window);
  let statusInFlight=null;
  let statusMemo=null;
  const barsMemo=new Map();

  const won=n=>'₩'+Math.round(Number(n||0)).toLocaleString('ko-KR');
  const pct=n=>(Number(n||0)>=0?'+':'')+Number(n||0).toFixed(2)+'%';
  const actionClass=a=>a==='BUY_CANDIDATE'?'up':a==='SETUP'||a==='SHADOW_ONLY'?'warn':a==='BLOCKED'||a==='SAFETY_WAIT'?'down':'neutral';

  function sameLocalDay(a,b){
    const x=new Date(a),y=new Date(b);
    return x.getFullYear()===y.getFullYear()&&x.getMonth()===y.getMonth()&&x.getDate()===y.getDate();
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
      desc.textContent=`최근 ${t} 화면 즉시 표시 · 서버 최신값 동기화 중`;
    }
    document.documentElement.dataset.fastStart='cached';
    return true;
  }

  function restoreCached(){
    try{
      const raw=localStorage.getItem(STATUS_KEY);
      if(!raw)return;
      const saved=JSON.parse(raw),age=Date.now()-Number(saved.at||0);
      if(age<0||age>STATUS_DISK_MAX_AGE||!sameLocalDay(saved.at,Date.now()))return;
      renderCached(saved.data,saved.at);
    }catch{}
  }

  function saveStatus(data){
    try{localStorage.setItem(STATUS_KEY,JSON.stringify({at:Date.now(),data}))}catch{}
  }

  function snapResponse(res,text){
    return {at:Date.now(),status:res.status,statusText:res.statusText,headers:[...res.headers.entries()],text};
  }
  function responseFrom(s){return new Response(s.text,{status:s.status,statusText:s.statusText,headers:s.headers})}
  async function fetchSnap(input,init){
    const res=await nativeFetch(input,init);
    const text=await res.clone().text();
    return snapResponse(res,text);
  }

  window.fetch=async function(input,init={}){
    let url;
    try{url=new URL(typeof input==='string'?input:input.url,location.href)}catch{return nativeFetch(input,init)}
    const method=String(init?.method||(typeof input!=='string'&&input.method)||'GET').toUpperCase();
    if(method!=='GET'||url.origin!==location.origin)return nativeFetch(input,init);

    if(url.pathname==='/api/mobile/status'){
      const now=Date.now();
      if(statusMemo&&now-statusMemo.at<STATUS_MEMORY_TTL)return responseFrom(statusMemo);
      if(!statusInFlight){
        statusInFlight=fetchSnap(input,init).then(s=>{
          if(s.status>=200&&s.status<300){
            statusMemo=s;
            try{saveStatus(JSON.parse(s.text))}catch{}
          }
          return s;
        }).finally(()=>{statusInFlight=null});
      }
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
    restore:restoreCached,
    clear(){statusMemo=null;barsMemo.clear();try{localStorage.removeItem(STATUS_KEY)}catch{}}
  };
  restoreCached();
})();
