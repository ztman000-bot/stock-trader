const ASSET_VERSION='1788796000';
const CACHE=`stock-day-trader-live-v${ASSET_VERSION}-version-display`;
const CORE_ASSETS=[
  '/classic',
  `/styles.css?v=${ASSET_VERSION}`,
  `/js/app-safe.js?v=${ASSET_VERSION}`,
  '/js/config.js',
  `/js/classic-fast-start.js?v=${ASSET_VERSION}`,
  `/js/ui-polish.js?v=${ASSET_VERSION}`,
  `/js/live-app.js?v=${ASSET_VERSION}`,
  `/js/version-display.js?v=${ASSET_VERSION}`,
  `/js/trade-name-fix.js?v=${ASSET_VERSION}`,
  `/js/scanner-resilience.js?v=${ASSET_VERSION}`,
  `/js/history-ui.js?v=${ASSET_VERSION}`,
  `/js/strategy-lab-ui.js?v=${ASSET_VERSION}`,
  `/js/market-lab-ui.js?v=${ASSET_VERSION}`,
  `/js/final-results-ui.js?v=${ASSET_VERSION}`,
  `/manifest.webmanifest?v=${ASSET_VERSION}`,
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

  // Navigation must be network-first. A cache-first /classic shell can keep the
  // previous app-safe bootstrap alive after a successful Android update.
  if(event.request.mode==='navigate'){
    event.respondWith(
      networkRefresh(event.request).catch(async()=>{
        const exact=await caches.match(event.request);
        if(exact)return exact;
        const shell=await caches.match('/classic');
        if(shell)return shell;
        return new Response('OFFLINE',{status:503,statusText:'Offline'});
      })
    );
    return;
  }

  const refresh=networkRefresh(event.request);
  event.waitUntil(refresh.then(()=>{}).catch(()=>{}));
  event.respondWith(
    caches.match(event.request).then(cached=>cached||refresh.catch(()=>new Response('OFFLINE',{status:503,statusText:'Offline'})))
  );
});
