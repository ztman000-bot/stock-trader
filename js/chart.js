export function renderChart(canvas,history,indicators){
 const ctx=canvas.getContext('2d'),dpr=window.devicePixelRatio||1,w=canvas.clientWidth||700,h=canvas.clientHeight||300;
 canvas.width=w*dpr;canvas.height=h*dpr;ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);
 if(history.length<2)return;
 const data=history.slice(-70),prices=data.flatMap(x=>[x.high,x.low]);const min=Math.min(...prices),max=Math.max(...prices),pad=(max-min)*.08||1,lo=min-pad,hi=max+pad;
 const x=i=>34+i*(w-50)/(data.length-1),y=p=>12+(hi-p)*(h-42)/(hi-lo);
 ctx.strokeStyle='#263248';ctx.lineWidth=1;for(let j=0;j<4;j++){const yy=15+j*(h-45)/3;ctx.beginPath();ctx.moveTo(30,yy);ctx.lineTo(w-10,yy);ctx.stroke();}
 const emaLine=(period,color)=>{const k=2/(period+1);let e=data[0].close;ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();data.forEach((c,i)=>{e=i?c.close*k+e*(1-k):c.close;const xx=x(i),yy=y(e);i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy);});ctx.stroke();};
 data.forEach((c,i)=>{const xx=x(i),yo=y(c.open),yc=y(c.close),yh=y(c.high),yl=y(c.low),up=c.close>=c.open;ctx.strokeStyle=up?'#5ee0a3':'#ff6b6b';ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(xx,yh);ctx.lineTo(xx,yl);ctx.stroke();ctx.fillRect(xx-2.3,Math.min(yo,yc),4.6,Math.max(1,Math.abs(yc-yo)));});
 emaLine(5,'#ffd166');emaLine(20,'#6ea8fe');
 ctx.fillStyle='#8ea0b9';ctx.font='10px system-ui';ctx.fillText(`EMA5 ${Math.round(indicators.ema5||0).toLocaleString()} · EMA20 ${Math.round(indicators.ema20||0).toLocaleString()} · RSI ${indicators.rsi.toFixed(1)} · ADX ${indicators.adx.toFixed(1)}`,34,h-8);
}
