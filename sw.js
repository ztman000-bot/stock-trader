const CACHE='stock-day-trader-live-v1782-research-delta';
const CORE_ASSETS=[
  '/classic',
  '/styles.css?v=1782',
  '/js/app-safe.js?v=1782',
  '/js/config.js',
  '/js/ui-polish.js?v=1782',
  '/js/live-app.js?v=1782',
  '/js/trade-name-fix.js?v=1782',
  '/js/history-ui.js?v=1782',
  '/js/strategy-lab-ui.js?v=1782',
  '/js/market-lab-ui.js?v=1782',
  '/js/final-results-ui.js?v=1782',
  '/manifest.webmanifest?v=1782',
  '/icons/icon-192.svg',
  '/icons/icon-512.svg'
];

self.addEventListener('install',event=>{
  event.waitUntil(
    caches.open(CACHE)
      .then(cache=>cache.addAll(CORE_ASSETS))
      .then(()=>self.skipWaiting())
  );
});

self.addEventListener('activate',event=>{
  event.waitUntil(
    Promise.all([
      caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))),
      self.clients.claim()
    ])
  );
});

function networkRefresh(request){
  return fetch(request,{cache:'no-store'}).then(response=>{
    if(response && response.ok){
      const copy=response.clone();
      caches.open(CACHE).then(cache=>cache.put(request,copy)).catch(()=>{});
    }
    return response;
  });
}

self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET')return;
  const url=new URL(event.request.url);
  if(url.pathname.startsWith('/api/')){
    event.respondWith(fetch(event.request,{cache:'no-store'}));
    return;
  }
  if(url.origin!==self.location.origin)return;
  const refresh=networkRefresh(event.request);
  event.waitUntil(refresh.then(()=>{}).catch(()=>{}));
  event.respondWith(
    caches.match(event.request).then(cached=>{
      if(cached)return cached;
      return refresh.catch(async()=>{
        if(event.request.mode==='navigate'){
          const shell=await caches.match('/classic');
          if(shell)return shell;
        }
        return new Response('OFFLINE',{status:503,statusText:'Offline'});
      });
    })
  );
});
