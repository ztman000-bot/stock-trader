const CACHE='stock-trader-v02';
const ASSETS=['./','./index.html','./styles.css','./js/app.js','./js/config.js','./js/mockBroker.js','./js/strategy.js','./js/indicators.js','./js/chart.js','./js/backtest.js','./js/risk.js','./js/paperBroker.js','./js/nhAdapter.js','./manifest.webmanifest'];
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)))});
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',e=>e.respondWith(fetch(e.request).then(r=>{const copy=r.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));return r}).catch(()=>caches.match(e.request))));
