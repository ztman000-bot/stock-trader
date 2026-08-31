export class NHBridge {
  constructor(baseUrl=''){this.baseUrl=(baseUrl||'').replace(/\/$/,'');this.connected=false;this.lastHealth=null;}
  setBaseUrl(url){this.baseUrl=(url||'').trim().replace(/\/$/,'');localStorage.setItem('stockTraderNhBackend',this.baseUrl);}
  loadSavedUrl(){const v=localStorage.getItem('stockTraderNhBackend');if(v)this.baseUrl=v;return this.baseUrl;}
  async _get(path){
    if(!this.baseUrl)throw new Error('NH 백엔드 주소가 설정되지 않았습니다.');
    const r=await fetch(`${this.baseUrl}${path}`,{cache:'no-store'});
    if(!r.ok){let msg=`HTTP ${r.status}`;try{const d=await r.json();msg=d.detail||msg}catch{}throw new Error(msg)}
    return r.json();
  }
  async health(){const data=await this._get('/api/health');this.connected=!!data.ok;this.lastHealth=data;return data;}
  async testLiveData(){return this._get('/api/nh/test');}
  async quote(code){return this._get(`/api/nh/quote/${encodeURIComponent(code)}`);}
}
