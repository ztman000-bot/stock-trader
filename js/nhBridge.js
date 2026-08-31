export class NHBridge {
  constructor(baseUrl=''){
    this.baseUrl=(baseUrl||'').replace(/\/$/,'');
    this.connected=false;
    this.lastHealth=null;
  }
  setBaseUrl(url){this.baseUrl=(url||'').trim().replace(/\/$/,'');localStorage.setItem('stockTraderNhBackend',this.baseUrl);}
  loadSavedUrl(){const v=localStorage.getItem('stockTraderNhBackend');if(v)this.baseUrl=v;return this.baseUrl;}
  async health(){
    if(!this.baseUrl)throw new Error('NH 백엔드 주소가 설정되지 않았습니다.');
    const r=await fetch(`${this.baseUrl}/api/health`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Backend HTTP ${r.status}`);
    const data=await r.json();this.connected=!!data.ok;this.lastHealth=data;return data;
  }
  async quote(code){
    if(!this.baseUrl)throw new Error('NH 백엔드 주소가 설정되지 않았습니다.');
    const r=await fetch(`${this.baseUrl}/api/nh/quote/${encodeURIComponent(code)}`,{cache:'no-store'});
    if(!r.ok){let msg=`HTTP ${r.status}`;try{const d=await r.json();msg=d.detail||msg}catch{}throw new Error(msg)}
    return r.json();
  }
}
