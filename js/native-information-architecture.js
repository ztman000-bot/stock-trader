// Native Android information architecture v2.
// Display-only layout organizer: separates operations, trading, and research views.
// No broker/order API calls and no Control/Paper mutation.
(()=>{
  if(new URLSearchParams(location.search).get('native')!=='1')return;

  const root=document.documentElement;
  const style=document.createElement('style');
  style.id='nativeInformationArchitectureV2';
  style.textContent=`
    /* HOME = 10-second operating summary. */
    body[data-mobile-tab="home"] #nativeShadowPanel,
    body[data-mobile-tab="home"] #dataHealthPanel{display:block!important}
    body[data-mobile-tab="home"] #nativeShadowGrid,
    body[data-mobile-tab="home"] #nativeShadowList,
    body[data-mobile-tab="home"] #nativeShadowDeep,
    body[data-mobile-tab="home"] #nativeShadowPanel .native-detail-note,
    body[data-mobile-tab="home"] #dataHealthGrid,
    body[data-mobile-tab="home"] #dataHealthNote{display:none!important}
    body[data-mobile-tab="home"] #nativeResearchTabBtn{display:inline-flex!important}

    /* TOP10 = scanner only. */
    body[data-mobile-tab="top"] #nativeShadowPanel,
    body[data-mobile-tab="top"] #dataHealthPanel{display:none!important}

    /* CHART/TRADING = chart + positions + Paper history. */
    body[data-mobile-tab="chart"] #nativeShadowPanel,
    body[data-mobile-tab="chart"] #dataHealthPanel{display:none!important}
    body[data-mobile-tab="chart"] .native-trade-grid{display:grid!important}
    body[data-mobile-tab="chart"] .native-trade-grid>[data-native-role="research"]{display:none!important}
    body[data-mobile-tab="chart"] .native-paper-history{display:block!important}

    /* RESEARCH/LEARNING = detailed research, no duplicated trading controls/history. */
    body[data-mobile-tab="learn"] #nativeShadowPanel,
    body[data-mobile-tab="learn"] #dataHealthPanel{display:block!important}
    html.native-compact body[data-mobile-tab="learn"] #dataHealthGrid{display:grid!important}
    html.native-compact body[data-mobile-tab="learn"] #dataHealthNote{display:block!important}
    body[data-mobile-tab="learn"] #nativeShadowGrid{display:grid!important}
    body[data-mobile-tab="learn"] #nativeShadowList{display:grid!important}
    body[data-mobile-tab="learn"] #nativeShadowDeep{display:block!important}
    body[data-mobile-tab="learn"] #nativeShadowPanel .native-detail-note{display:block!important}
    body[data-mobile-tab="learn"] .native-trade-grid>[data-native-role="trade"]{display:none!important}
    body[data-mobile-tab="learn"] .native-paper-history{display:none!important}
    body[data-mobile-tab="learn"] #nativeResearchTabBtn{display:none!important}

    /* The old global detail toggle is replaced by tab roles. */
    #nativeUiModeBtn{display:none!important}

    /* Keep home summaries compact and readable. */
    body[data-mobile-tab="home"] #nativeShadowPanel .panel-head,
    body[data-mobile-tab="home"] #dataHealthPanel .panel-head{margin-bottom:7px}
    body[data-mobile-tab="home"] #nativeShadowSummary,
    body[data-mobile-tab="home"] #dataHealthSummary{font-size:11px;line-height:1.45}
    #nativeResearchTabBtn{white-space:nowrap}
  `;
  document.head.appendChild(style);

  function switchTab(tab){
    document.body.dataset.mobileTab=tab;
    try{localStorage.setItem('daytrader-mobile-tab',tab)}catch{}
    document.querySelectorAll('.mobile-nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
    try{scrollTo({top:0,behavior:'instant'})}catch{scrollTo(0,0)}
  }

  function classifyTradingSections(){
    const positions=document.querySelector('#positionsBody')?.closest('article');
    const risk=document.querySelector('#riskState')?.closest('article');
    const grid=positions?.closest('.lower-grid');
    if(grid)grid.classList.add('native-trade-grid');
    if(positions)positions.dataset.nativeRole='trade';
    if(risk)risk.dataset.nativeRole='research';

    const trades=document.querySelector('#tradesBody')?.closest('section');
    if(trades)trades.classList.add('native-paper-history');
  }

  function tuneNavigation(){
    const learn=document.querySelector('.mobile-nav button[data-tab="learn"]');
    if(learn&&learn.dataset.iaV2!=='1'){
      learn.dataset.iaV2='1';
      const icon=learn.querySelector('b')?.outerHTML||'<b>▣</b>';
      learn.innerHTML=`${icon}연구·학습`;
    }
  }

  function ensureResearchShortcut(){
    const panel=document.querySelector('#nativeShadowPanel');
    if(!panel)return false;
    let actions=panel.querySelector('.native-shadow-actions');
    if(!actions){
      const head=panel.querySelector('.panel-head');
      if(!head)return false;
      actions=document.createElement('div');
      actions.className='native-shadow-actions';
      head.appendChild(actions);
    }
    if(!document.querySelector('#nativeResearchTabBtn')){
      const btn=document.createElement('button');
      btn.id='nativeResearchTabBtn';
      btn.type='button';
      btn.className='btn ghost compact';
      btn.textContent='연구 보기';
      btn.addEventListener('click',()=>switchTab('learn'));
      actions.appendChild(btn);
    }
    return true;
  }

  function enforceCompactHome(){
    root.classList.add('native-compact');
    try{localStorage.setItem('stock-trader-native-detail','0')}catch{}
  }

  function apply(){
    enforceCompactHome();
    classifyTradingSections();
    tuneNavigation();
    ensureResearchShortcut();
  }

  function start(){
    apply();
    const obs=new MutationObserver(apply);
    obs.observe(document.documentElement,{childList:true,subtree:true});
    setTimeout(()=>obs.disconnect(),25000);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
