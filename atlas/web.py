"""Local web app for ATLAS (stdlib only, zero dependencies).

Serves a single-page app with an **interactive candlestick chart** (Chart tab)
and the analysis cards (Analysis tab), backed by JSON APIs that run the real
engine — the same output as the CLI, in a browser. The chart is drawn from
scratch on a canvas (no external libraries) with the computed overlays —
indicators, S/R levels, trendlines, Fibonacci, pattern markers — drawn on it,
plus zoom / pan / crosshair and timeframe + data-source controls.

Endpoints: ``/`` (app), ``/api/analyze`` (analysis envelope), ``/api/chart``
(bars + overlays for the chart).

Run it:

    python -m atlas.web                 # http://127.0.0.1:8787
    python -m atlas.web --port 9000

This is a LOCAL development server bound to localhost. It is not hardened for
public exposure — do not put it on a public interface.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .analysis import analyze
from .data import AlphaVantageProvider, CSVProvider, SyntheticProvider
from .tools import ToolRegistry


def build_registry(source: str, api_key: str = "", csv_dir: str = "./data", seed: int = 42) -> ToolRegistry:
    if source == "alphavantage":
        return ToolRegistry(AlphaVantageProvider(api_key=api_key or None))
    if source == "csv":
        return ToolRegistry(CSVProvider(csv_dir or "./data"))
    return ToolRegistry(SyntheticProvider(seed=seed))


def _truthy(v: str) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


def run_analysis(params: dict):
    """Pure request handler: params dict -> (status_code, response_dict)."""
    symbol = (params.get("symbol") or "").strip().upper()
    if not symbol:
        return 400, {"error": "symbol is required"}
    source = (params.get("source") or "synthetic").strip()
    benchmark = (params.get("benchmark") or "").strip() or None
    try:
        registry = build_registry(
            source,
            api_key=params.get("apikey", ""),
            csv_dir=params.get("csvdir", "./data"),
            seed=int(params.get("seed", 42) or 42),
        )
        out = analyze(
            symbol,
            registry=registry,
            timeframe=params.get("timeframe", "1d") or "1d",
            lookback=int(params.get("lookback", 300) or 300),
            benchmark=benchmark,
            with_fundamentals=_truthy(params.get("fundamentals", "")),
            with_sentiment=_truthy(params.get("sentiment", "")),
            with_events=_truthy(params.get("events", "")),
        )
        return 200, out
    except Exception as e:  # noqa: BLE001 - report failures as JSON, never crash the server
        return 500, {"error": f"{type(e).__name__}: {e}"}


def build_chart_data(params: dict):
    """Return bars + computed overlays for the interactive chart."""
    from . import indicators as ind
    from .chart_patterns import detect_classical
    from .fibonacci import auto_fibonacci
    from .levels import classify_by_price, detect_trendlines
    from .patterns import detect_patterns

    symbol = (params.get("symbol") or "").strip().upper()
    if not symbol:
        return 400, {"error": "symbol is required"}
    source = (params.get("source") or "synthetic").strip()
    timeframe = params.get("timeframe", "1d") or "1d"
    try:
        registry = build_registry(source, api_key=params.get("apikey", ""),
                                  csv_dir=params.get("csvdir", "./data"),
                                  seed=int(params.get("seed", 42) or 42))
        fetched = registry.get_ohlcv(symbol, timeframe, int(params.get("lookback", 300) or 300))
        if "error" in fetched:
            return 200, {"error": fetched["error"]}
        s = fetched["_series"]
        close = list(s.close)
        bb = ind.bollinger_bands(close)
        bars = [{"t": s.ts[i].isoformat(), "o": s.open[i], "h": s.high[i],
                 "l": s.low[i], "c": s.close[i], "v": s.volume[i]} for i in range(len(s))]
        levels = classify_by_price(s)
        tl = detect_trendlines(s)
        fib = auto_fibonacci(s)
        pats = [p for p in detect_patterns(s) if p["direction"] != "neutral"]
        classical = detect_classical(s)
        return 200, {
            "symbol": symbol, "timeframe": timeframe,
            "simulated": fetched["provenance"].get("simulated", False),
            "bars": bars,
            "overlays": {
                "ema20": ind.ema(close, 20), "ema50": ind.ema(close, 50),
                "vwap": ind.vwap(s),
                "bb_upper": bb["upper"], "bb_mid": bb["middle"], "bb_lower": bb["lower"],
            },
            "levels": {"support": levels["support"][:6], "resistance": levels["resistance"][:6]},
            "trendlines": tl,
            "fibonacci": fib,
            "patterns": pats[-40:],
            "classical": classical,
        }
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"{type(e).__name__}: {e}"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter console
        pass

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/analyze":
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            status, payload = run_analysis(params)
            self._send(status, json.dumps(payload, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        if parsed.path == "/api/chart":
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            status, payload = build_chart_data(params)
            self._send(status, json.dumps(payload, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._send(404, b'{"error":"not found"}', "application/json")


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"ATLAS dashboard running at {url}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ATLAS dashboard.")
    finally:
        server.server_close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="atlas.web", description="ATLAS web dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    args = p.parse_args(argv)
    serve(args.host, args.port)
    return 0


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATLAS Charts</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2330;--line:#30363d;--text:#e6edf3;
        --muted:#8b949e;--accent:#4ea1ff;--green:#3fb950;--red:#f85149;--amber:#d29922;--teal:#2dd4bf;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
  header{padding:12px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  header h1{font-size:17px;margin:0;letter-spacing:.5px}
  header .tag{color:var(--muted);font-size:12px}
  .ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:auto}
  input,select{background:var(--bg);border:1px solid var(--line);color:var(--text);border-radius:7px;padding:7px 9px;font-size:13px}
  input#symbol{width:120px;font-weight:600;text-transform:uppercase}
  button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:7px;padding:7px 12px;font-size:13px;cursor:pointer}
  button.primary{background:var(--accent);color:#04101f;border:0;font-weight:700}
  button.active{background:var(--accent);color:#04101f;border-color:var(--accent)}
  .tabs{display:flex;gap:4px;padding:8px 18px 0}
  .tabs button{border-radius:8px 8px 0 0}
  .wrap{padding:12px 18px}
  #status{color:var(--muted);font-size:13px;margin:6px 0}
  #status.err{color:var(--red)}
  .tf button{padding:6px 9px}
  .toggles{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0;font-size:13px;color:var(--muted)}
  .toggles label{display:flex;gap:5px;align-items:center;cursor:pointer}
  .sw{width:22px;height:3px;border-radius:2px;display:inline-block}
  .chartbox{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  canvas{display:block;width:100%;height:560px;cursor:crosshair}
  #readout{position:absolute;top:8px;left:10px;background:rgba(13,17,23,.85);border:1px solid var(--line);
           border-radius:8px;padding:6px 10px;font-size:12px;font-variant-numeric:tabular-nums;pointer-events:none;line-height:1.5}
  .sim{color:var(--amber);font-weight:700}
  /* analysis view reuse */
  .hero{display:flex;gap:16px;flex-wrap:wrap;margin-top:6px}
  .scorecard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 22px;min-width:200px}
  .scorecard .big{font-size:46px;font-weight:800;line-height:1}
  .chip{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:700;font-size:13px;margin-top:8px}
  .grow{flex:1;min-width:280px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
  h3{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
  .barrow{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px}
  .barrow .name{width:120px;color:var(--muted)}
  .track{flex:1;height:9px;background:var(--bg);border-radius:6px;overflow:hidden;border:1px solid var(--line)}
  .fill{height:100%}
  .kv{color:var(--muted);font-size:13px;line-height:1.7}
  .hint{color:var(--muted);font-size:12px}
  .pill{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:3px 9px;font-size:12px;margin:2px}
</style>
</head>
<body>
<header>
  <h1>ATLAS</h1><span class="tag">charts &amp; analysis</span>
  <div class="ctl">
    <input id="symbol" placeholder="MSFT" value="MSFT">
    <select id="source">
      <option value="synthetic">Synthetic</option>
      <option value="alphavantage">Alpha Vantage</option>
      <option value="csv">CSV</option>
    </select>
    <input id="apikey" placeholder="API key" style="display:none">
    <input id="csvdir" value="./data" style="display:none">
    <span class="tf" id="tfbar">
      <button data-tf="1d" class="active">1D</button>
      <button data-tf="1w">1W</button>
      <button data-tf="1mo">1M</button>
    </span>
    <button class="primary" id="go">Load</button>
  </div>
</header>
<div class="tabs">
  <button id="tabChart" class="active">Chart</button>
  <button id="tabAnalysis">Analysis</button>
</div>
<div class="wrap">
  <div id="status"></div>

  <div id="chartView">
    <div class="toggles" id="toggles"></div>
    <div class="chartbox">
      <canvas id="cv"></canvas>
      <div id="readout"></div>
    </div>
    <div class="hint" id="chartmeta" style="margin-top:8px"></div>
  </div>

  <div id="analysisView" style="display:none"></div>
</div>
<script>
const $=id=>document.getElementById(id);
const C={green:'#3fb950',red:'#f85149',amber:'#d29922',accent:'#4ea1ff',teal:'#2dd4bf',
         muted:'#8b949e',line:'#30363d',text:'#e6edf3',ema20:'#4ea1ff',ema50:'#d29922',
         vwap:'#2dd4bf',bb:'#8b949e',sup:'#3fb950',res:'#f0883e',fib:'#a371f7'};
const OV=[['candles','Candles','#c9d1d9'],['ema20','EMA20',C.ema20],['ema50','EMA50',C.ema50],
         ['bb','Bollinger',C.bb],['vwap','VWAP',C.vwap],['levels','S/R',C.sup],
         ['trend','Trendlines',C.res],['fib','Fibonacci',C.fib],['patterns','Patterns',C.teal],
         ['volume','Volume',C.muted]];
let data=null, view={s:0,e:0}, hover=-1, on={}, timeframe='1d';
OV.forEach(o=>on[o[0]]=true);

function controls(){
  return {symbol:$('symbol').value.trim(), source:$('source').value,
          apikey:$('apikey').value, csvdir:$('csvdir').value, timeframe};
}
function toggleSourceFields(){const s=$('source').value;
  $('apikey').style.display=s==='alphavantage'?'':'none';
  $('csvdir').style.display=s==='csv'?'':'none';}
$('source').addEventListener('change',toggleSourceFields);
$('symbol').addEventListener('keydown',e=>{if(e.key==='Enter')load();});
$('go').addEventListener('click',load);
$('tabChart').addEventListener('click',()=>showTab('chart'));
$('tabAnalysis').addEventListener('click',()=>showTab('analysis'));
$('tfbar').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{
  timeframe=b.dataset.tf; $('tfbar').querySelectorAll('button').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); if(tab==='chart') loadChart(); }));

let tab='chart';
function showTab(t){tab=t;
  $('tabChart').classList.toggle('active',t==='chart');
  $('tabAnalysis').classList.toggle('active',t==='analysis');
  $('chartView').style.display=t==='chart'?'':'none';
  $('analysisView').style.display=t==='analysis'?'':'none';
  if(t==='chart' && !data) loadChart();
  if(t==='analysis') loadAnalysis();
}
function setStatus(t,err){const s=$('status');s.textContent=t||'';s.className=err?'err':'';}

// ---- toggles UI ----
function buildToggles(){
  $('toggles').innerHTML=OV.map(o=>
    `<label><input type="checkbox" data-k="${o[0]}" ${on[o[0]]?'checked':''}>
      <span class="sw" style="background:${o[2]}"></span>${o[1]}</label>`).join('');
  $('toggles').querySelectorAll('input').forEach(i=>i.addEventListener('change',()=>{
    on[i.dataset.k]=i.checked; draw();}));
}

function load(){ if(tab==='chart') loadChart(); else loadAnalysis(); }

async function loadChart(){
  setStatus('Loading '+$('symbol').value+' …');
  const p=new URLSearchParams(controls());
  try{
    const r=await fetch('/api/chart?'+p); const d=await r.json();
    if(d.error){setStatus('Error: '+d.error,true);return;}
    data=d; const n=d.bars.length; view={s:Math.max(0,n-140),e:n}; hover=-1;
    buildToggles(); setStatus('');
    $('chartmeta').innerHTML=`${d.symbol} · ${d.timeframe} · ${n} bars `+
      (d.simulated?'<span class="sim">SIMULATED</span>':'');
    resize();
  }catch(e){setStatus('Request failed: '+e,true);}
}

// ---- canvas chart ----
const cv=$('cv'); const ctx=cv.getContext('2d');
let W=0,H=0,DPR=1;
function resize(){
  DPR=window.devicePixelRatio||1;
  const r=cv.getBoundingClientRect(); W=r.width; H=r.height;
  cv.width=W*DPR; cv.height=H*DPR; ctx.setTransform(DPR,0,0,DPR,0,0); draw();
}
window.addEventListener('resize',()=>{if(data)resize();});

const PADL=8,PADR=62,PADT=10;
function layout(){
  const priceH=Math.round((H-PADT)*0.72), volTop=PADT+priceH+14, volH=(H-volTop)-6;
  return {priceH,volTop,volH};
}
function vis(){return data.bars.slice(view.s,view.e);}
function priceRange(bars){
  let lo=1e18,hi=-1e18;
  bars.forEach(b=>{lo=Math.min(lo,b.l);hi=Math.max(hi,b.h);});
  const ov=data.overlays;
  const addArr=a=>{for(let i=view.s;i<view.e;i++){const v=a[i];if(v!=null){lo=Math.min(lo,v);hi=Math.max(hi,v);}}};
  if(on.ema20)addArr(ov.ema20); if(on.ema50)addArr(ov.ema50);
  if(on.bb){addArr(ov.bb_upper);addArr(ov.bb_lower);} if(on.vwap)addArr(ov.vwap);
  if(on.levels){[...data.levels.support,...data.levels.resistance].forEach(l=>{
     if(l.price>=lo*0.9&&l.price<=hi*1.1){lo=Math.min(lo,l.price);hi=Math.max(hi,l.price);}});}
  const pad=(hi-lo)*0.06||1; return {lo:lo-pad,hi:hi+pad};
}
function xOf(i){const bw=(W-PADL-PADR)/(view.e-view.s); return PADL+(i-view.s)*bw+bw/2;}
function bw(){return (W-PADL-PADR)/(view.e-view.s);}
let PR;
function yOf(p){return PADT+(PR.hi-p)/(PR.hi-PR.lo)*layout().priceH;}

function line(arr,color,w){
  ctx.strokeStyle=color;ctx.lineWidth=w||1.4;ctx.beginPath();let started=false;
  for(let i=view.s;i<view.e;i++){const v=arr[i];if(v==null){started=false;continue;}
    const x=xOf(i),y=yOf(v); if(!started){ctx.moveTo(x,y);started=true;}else ctx.lineTo(x,y);}
  ctx.stroke();
}
function hline(price,color,label,dash){
  const y=yOf(price); if(y<PADT||y>PADT+layout().priceH)return;
  ctx.strokeStyle=color;ctx.lineWidth=1;ctx.setLineDash(dash||[]);
  ctx.beginPath();ctx.moveTo(PADL,y);ctx.lineTo(W-PADR,y);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle=color;ctx.font='10px sans-serif';ctx.textAlign='left';
  ctx.fillText(label,W-PADR+3,y+3);
}
function draw(){
  ctx.clearRect(0,0,W,H); if(!data){return;}
  const bars=vis(); if(!bars.length)return; PR=priceRange(bars);
  const L=layout(), o=data.overlays, b=bw();
  // grid + price axis
  ctx.strokeStyle=C.line;ctx.fillStyle=C.muted;ctx.font='10px sans-serif';ctx.textAlign='left';
  for(let k=0;k<=4;k++){const p=PR.hi-(PR.hi-PR.lo)*k/4,y=yOf(p);
    ctx.globalAlpha=.4;ctx.beginPath();ctx.moveTo(PADL,y);ctx.lineTo(W-PADR,y);ctx.stroke();ctx.globalAlpha=1;
    ctx.fillText(p.toFixed(2),W-PADR+3,y+3);}
  // volume
  if(on.volume){let mv=0;for(let i=view.s;i<view.e;i++)mv=Math.max(mv,data.bars[i].v);
    for(let i=view.s;i<view.e;i++){const bar=data.bars[i];const x=xOf(i);
      const vh=mv?bar.v/mv*L.volH:0; ctx.fillStyle=(bar.c>=bar.o?'rgba(63,185,80,.35)':'rgba(248,81,73,.35)');
      ctx.fillRect(x-b*0.4,L.volTop+L.volH-vh,b*0.8,vh);}}
  // candles
  if(on.candles){for(let i=view.s;i<view.e;i++){const bar=data.bars[i];const x=xOf(i);
    const up=bar.c>=bar.o; ctx.strokeStyle=up?C.green:C.red;ctx.fillStyle=up?C.green:C.red;
    ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,yOf(bar.h));ctx.lineTo(x,yOf(bar.l));ctx.stroke();
    const y1=yOf(bar.o),y2=yOf(bar.c);const top=Math.min(y1,y2);let hgt=Math.abs(y2-y1);if(hgt<1)hgt=1;
    ctx.fillRect(x-b*0.35,top,b*0.7,hgt);}}
  // overlays
  if(on.ema20)line(o.ema20,C.ema20); if(on.ema50)line(o.ema50,C.ema50);
  if(on.vwap)line(o.vwap,C.vwap,1.2);
  if(on.bb){line(o.bb_upper,C.bb,1);line(o.bb_mid,C.bb,0.8);line(o.bb_lower,C.bb,1);}
  // S/R
  if(on.levels){data.levels.support.forEach(l=>hline(l.price,C.sup,'S '+l.price,[4,3]));
    data.levels.resistance.forEach(l=>hline(l.price,C.res,'R '+l.price,[4,3]));}
  // trendlines
  if(on.trend&&data.trendlines){['support','resistance'].forEach(k=>{const t=data.trendlines[k];if(!t)return;
    const col=k==='support'?C.sup:C.res;
    const i0=view.s,i1=view.e-1;const p0=t.slope*i0+t.intercept,p1=t.slope*i1+t.intercept;
    ctx.strokeStyle=col;ctx.lineWidth=1.4;ctx.globalAlpha=.85;ctx.beginPath();
    ctx.moveTo(xOf(i0),yOf(p0));ctx.lineTo(xOf(i1),yOf(p1));ctx.stroke();ctx.globalAlpha=1;});}
  // fibonacci
  if(on.fib&&data.fibonacci&&data.fibonacci.retracements){
    Object.entries(data.fibonacci.retracements).forEach(([k,p])=>hline(p,C.fib,'fib '+k,[2,3]));}
  // pattern markers
  if(on.patterns&&data.patterns){data.patterns.forEach(p=>{
    if(p.index<view.s||p.index>=view.e)return;const bar=data.bars[p.index];const x=xOf(p.index);
    const bull=p.direction==='bullish';const y=bull?yOf(bar.l)+8:yOf(bar.h)-8;
    ctx.fillStyle=bull?C.green:C.red;ctx.beginPath();
    if(bull){ctx.moveTo(x,y-6);ctx.lineTo(x-4,y);ctx.lineTo(x+4,y);}else{ctx.moveTo(x,y+6);ctx.lineTo(x-4,y);ctx.lineTo(x+4,y);}
    ctx.closePath();ctx.fill();});}
  // crosshair + readout
  if(hover>=view.s&&hover<view.e){const x=xOf(hover);
    ctx.strokeStyle=C.muted;ctx.setLineDash([3,3]);ctx.globalAlpha=.6;
    ctx.beginPath();ctx.moveTo(x,PADT);ctx.lineTo(x,L.volTop+L.volH);ctx.stroke();
    ctx.setLineDash([]);ctx.globalAlpha=1;
    const bar=data.bars[hover];
    $('readout').innerHTML=`<b>${bar.t.slice(0,10)}</b><br>O ${bar.o.toFixed(2)} H ${bar.h.toFixed(2)}<br>`+
      `L ${bar.l.toFixed(2)} C ${bar.c.toFixed(2)}<br>Vol ${Math.round(bar.v).toLocaleString()}`;
  } else { $('readout').innerHTML=''; }
}

// interactions
cv.addEventListener('mousemove',e=>{if(!data)return;const r=cv.getBoundingClientRect();
  const x=e.clientX-r.left; const b=bw(); hover=Math.round(view.s+(x-PADL)/b-0.5); draw();
  if(drag){const dx=Math.round((dragX-x)/b); if(dx!==0){pan(dx);dragX=x;}}});
cv.addEventListener('mouseleave',()=>{hover=-1;draw();});
let drag=false,dragX=0;
cv.addEventListener('mousedown',e=>{drag=true;dragX=e.clientX-cv.getBoundingClientRect().left;});
window.addEventListener('mouseup',()=>{drag=false;});
function pan(dx){const n=data.bars.length;let s=view.s+dx,e=view.e+dx;
  if(s<0){e-=s;s=0;} if(e>n){s-=(e-n);e=n;} s=Math.max(0,s); view={s,e}; draw();}
cv.addEventListener('wheel',e=>{if(!data)return;e.preventDefault();const r=cv.getBoundingClientRect();
  const x=e.clientX-r.left;const frac=(x-PADL)/(W-PADL-PADR);
  const span=view.e-view.s;const f=e.deltaY>0?1.15:0.87;let ns=Math.max(20,Math.min(data.bars.length,Math.round(span*f)));
  const anchor=view.s+frac*span;let s=Math.round(anchor-frac*ns),en=s+ns;
  if(s<0){s=0;en=ns;} if(en>data.bars.length){en=data.bars.length;s=Math.max(0,en-ns);}
  view={s,e:en};draw();},{passive:false});

// ---- analysis view (cards) ----
async function loadAnalysis(){
  setStatus('Analyzing '+$('symbol').value+' …');
  const p=new URLSearchParams(controls());
  try{const r=await fetch('/api/analyze?'+p);const d=await r.json();
    if(d.error){setStatus('Error: '+d.error,true);return;} setStatus(''); renderAnalysis(d);
  }catch(e){setStatus('Request failed: '+e,true);}
}
function scoreColor(l){return {buy:C.green,accumulate:C.teal,hold:C.amber,reduce:'#f0883e',avoid:C.red}[l]||C.muted;}
function fillColor(v){if(v==null)return C.muted;return v>=66?C.green:v>=45?C.amber:C.red;}
function barRow(name,v){const w=v==null?0:Math.max(0,Math.min(100,v));const val=v==null?'n/a':v.toFixed(0);
  return `<div class="barrow"><div class="name">${name}</div><div class="track"><div class="fill" style="width:${w}%;background:${fillColor(v)}"></div></div><div style="width:34px;text-align:right">${val}</div></div>`;}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function renderAnalysis(d){
  const subs=d.subscores||{},col=scoreColor(d.score_label),lv=d.levels||{};
  let h=`<div class="hero"><div class="scorecard">
    <div style="color:${C.muted};font-size:12px;text-transform:uppercase">ATLAS Score</div>
    <div class="big" style="color:${col}">${d.atlas_score==null?'–':d.atlas_score}</div>
    <span class="chip" style="background:${col};color:#04101f">${(d.score_label||'').toUpperCase()}</span>
    <div class="kv" style="margin-top:8px"><b>${esc(d.symbol)}</b> · ${esc(d.regime||'')}<br>${esc(d.asof||'')}
    ${d.data_is_simulated?'<br><span class="sim">SIMULATED</span>':''}</div></div>
    <div class="grow"><h3>Sub-scores</h3>
    ${barRow('technical',subs.technical)}${barRow('fundamental',subs.fundamental)}${barRow('sentiment',subs.sentiment)}
    ${barRow('rel. strength',subs.relative_strength)}${barRow('risk',subs.risk)}
    ${d.top_contributors?`<div class="kv" style="margin-top:8px">${d.top_contributors.map(esc).join(' · ')}</div>`:''}</div></div>`;
  const pats=d.patterns||{};let chips='';['candlestick','classical','harmonic'].forEach(f=>(pats[f]||[]).forEach(p=>{
    chips+=`<span class="pill">${esc(p.name)} <span style="color:${p.direction==='bullish'?C.green:p.direction==='bearish'?C.red:C.muted}">${esc(p.direction||'')}</span></span>`;}));
  h+=`<div class="grow" style="margin-top:14px"><h3>Patterns</h3>${chips||'<span class="kv">none</span>'}</div>`;
  if((d.notes||[]).length)h+=`<div class="grow" style="margin-top:14px"><h3>Notes</h3><div class="kv">${d.notes.map(esc).join('<br>')}</div></div>`;
  h+=`<div class="hint" style="margin-top:12px">${esc(d.disclaimer||'')}</div>`;
  $('analysisView').innerHTML=h;
}
toggleSourceFields(); loadChart();
</script>
</body>
</html>"""


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
