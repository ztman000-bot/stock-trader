const CACHE='stock-day-trader-v041';
const ASSETS=['./','./index.html','./styles.css','./js/app-safe.js','./js/app.js','./js/config.js','./js/mockBroker.js','./js/strategy.js','./js/indicators.js','./js/chart.js','./js/backtest.js','./js/risk.js','./js/paperBroker.js','./js/learning.js','./js/nhAdapter.js','./js/nhBridge.js','./manifest.webmanifest','./icons/icon-192.svg','./icons/icon-512.svg'];
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)))});
self.addEventListener('activate',e=>e.waitUntil(Promise.all([caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))),self.clients.claim()])));
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;e.respondWith(fetch(e.request,{cache:'no-store'}).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));return r}).catch(()=>caches.match(e.request)))});
