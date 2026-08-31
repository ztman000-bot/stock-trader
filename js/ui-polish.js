// v0.10 mobile control polish. Pure presentation layer; no trading logic.
const style=document.createElement('style');
style.textContent=`
#scanBtn.scan-action,#remoteUpdateBtn.secondary-action,#nhCheckBtn.utility-action{white-space:nowrap;letter-spacing:-.2px;box-shadow:none;transition:transform .12s ease,opacity .12s ease,background .12s ease}
#scanBtn.scan-action:active,#remoteUpdateBtn.secondary-action:active,#nhCheckBtn.utility-action:active{transform:scale(.97)}
#scanBtn.scan-action{min-width:104px;border-radius:12px;padding:10px 14px;background:#5ee0a3;color:#052117;font-size:13px}
#remoteUpdateBtn.secondary-action{border-radius:12px;padding:9px 13px;background:#16243a;color:#dbe8f8;border:1px solid #33445f;font-size:12px}
#nhCheckBtn.utility-action{border-radius:12px;background:#163a30;color:#8cf0c0;border:1px solid #245e4b}
@media(max-width:520px){
  .main-grid article .panel-head{align-items:center;gap:8px}
  .main-grid article .panel-head>div{min-width:0;flex:1}
  #scanBtn.scan-action{min-width:92px;min-height:40px;padding:8px 11px;font-size:12px;flex:0 0 auto}
  #remoteUpdateBtn.secondary-action{min-height:40px;padding:8px 10px;font-size:11px;flex:0 0 auto!important}
  #nhCheckBtn.utility-action{min-height:40px;font-size:12px}
  .connection-panel .panel-head{gap:8px}
}
`;
document.head.appendChild(style);
