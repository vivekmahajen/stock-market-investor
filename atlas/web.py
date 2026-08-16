"""Local web dashboard for ATLAS (stdlib only, zero dependencies).

Serves a single-page dashboard and a JSON API that runs the real ``analyze``
engine — the same output as the CLI, in a browser. Enter a symbol, pick a data
source, and get the full ATLAS envelope rendered as cards.

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
<title>ATLAS Dashboard</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2330; --line:#30363d;
    --text:#e6edf3; --muted:#8b949e; --accent:#4ea1ff;
    --green:#3fb950; --teal:#2dd4bf; --amber:#d29922; --orange:#f0883e; --red:#f85149;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
  header{padding:18px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px}
  header h1{font-size:18px;margin:0;letter-spacing:.5px}
  header .tag{color:var(--muted);font-size:12px}
  .wrap{max-width:1080px;margin:0 auto;padding:22px}
  .controls{display:flex;flex-wrap:wrap;gap:10px;align-items:end;background:var(--panel);
            border:1px solid var(--line);border-radius:12px;padding:16px}
  .field{display:flex;flex-direction:column;gap:4px}
  .field label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
  input,select{background:var(--bg);border:1px solid var(--line);color:var(--text);
               border-radius:8px;padding:9px 10px;font-size:14px;min-width:120px}
  input#symbol{min-width:140px;font-weight:600;text-transform:uppercase}
  .checks{display:flex;gap:14px;align-items:center}
  .checks label{display:flex;gap:6px;align-items:center;font-size:13px;color:var(--text);text-transform:none;letter-spacing:0}
  button{background:var(--accent);color:#04101f;border:0;border-radius:8px;padding:10px 18px;
         font-weight:700;font-size:14px;cursor:pointer}
  button:disabled{opacity:.6;cursor:progress}
  .hint{color:var(--muted);font-size:12px;margin-top:8px}
  #status{margin:16px 0;color:var(--muted)}
  #status.err{color:var(--red)}
  .hero{display:flex;gap:18px;flex-wrap:wrap;align-items:stretch;margin-top:18px}
  .scorecard{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 24px;min-width:230px}
  .scorecard .big{font-size:52px;font-weight:800;line-height:1}
  .chip{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:700;font-size:13px;margin-top:8px}
  .meta{color:var(--muted);font-size:13px;margin-top:10px;line-height:1.6}
  .grow{flex:1;min-width:280px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
  h3{margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
  .barrow{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
  .barrow .name{width:120px;color:var(--muted)}
  .track{flex:1;height:10px;background:var(--bg);border-radius:6px;overflow:hidden;border:1px solid var(--line)}
  .fill{height:100%;border-radius:6px}
  .barrow .val{width:38px;text-align:right;font-variant-numeric:tabular-nums}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
  @media(max-width:720px){.grid{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
  .lvl{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;font-variant-numeric:tabular-nums}
  .lvl.res{color:var(--orange)} .lvl.sup{color:var(--green)}
  .pill{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:999px;
        padding:4px 10px;font-size:12px;margin:3px 4px 3px 0}
  .pill.bull{border-color:var(--green);color:var(--green)}
  .pill.bear{border-color:var(--red);color:var(--red)}
  .ev{display:flex;justify-content:space-between;font-size:13px;padding:5px 0;border-top:1px solid var(--line)}
  .ev .r-high{color:var(--red);font-weight:700}.ev .r-medium{color:var(--amber)}.ev .r-low{color:var(--muted)}
  ul.notes{margin:6px 0 0;padding-left:18px;color:var(--muted);font-size:13px;line-height:1.6}
  details{margin-top:18px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
  summary{cursor:pointer;color:var(--muted);font-size:13px}
  pre{overflow:auto;font-size:12px;color:#c9d1d9;background:var(--bg);padding:14px;border-radius:8px;border:1px solid var(--line)}
  .sim{color:var(--amber);font-size:12px;font-weight:700}
  .kv{color:var(--muted);font-size:13px;line-height:1.7}
  .kv b{color:var(--text)}
</style>
</head>
<body>
<header>
  <h1>ATLAS</h1><span class="tag">market-intelligence dashboard</span>
</header>
<div class="wrap">
  <div class="controls">
    <div class="field"><label>Symbol</label><input id="symbol" placeholder="MSFT" value="MSFT"></div>
    <div class="field"><label>Data source</label>
      <select id="source">
        <option value="synthetic">Synthetic (demo)</option>
        <option value="alphavantage">Alpha Vantage</option>
        <option value="csv">CSV folder</option>
      </select>
    </div>
    <div class="field" id="keyField" style="display:none"><label>API key</label><input id="apikey" placeholder="ALPHAVANTAGE key"></div>
    <div class="field" id="csvField" style="display:none"><label>CSV dir</label><input id="csvdir" value="./data"></div>
    <div class="field"><label>Benchmark</label><input id="benchmark" placeholder="SPY"></div>
    <div class="field"><label>Extras</label>
      <div class="checks">
        <label><input type="checkbox" id="fundamentals">Fund.</label>
        <label><input type="checkbox" id="sentiment">News</label>
        <label><input type="checkbox" id="events">Events</label>
      </div>
    </div>
    <button id="go">Analyze</button>
  </div>
  <div class="hint">Synthetic data is deterministic demo data (flagged SIMULATED). Alpha Vantage needs a free key; each extra (fundamentals/news/events) is a separate API call. CSV reads files named <code>SYMBOL_1d.csv</code> from the folder.</div>
  <div id="status"></div>
  <div id="result"></div>
</div>
<script>
const $ = id => document.getElementById(id);
function toggleSource(){
  const s=$('source').value;
  $('keyField').style.display = s==='alphavantage'?'flex':'none';
  $('csvField').style.display = s==='csv'?'flex':'none';
}
$('source').addEventListener('change',toggleSource);
$('symbol').addEventListener('keydown',e=>{if(e.key==='Enter')run();});
$('go').addEventListener('click',run);

function scoreColor(label){
  return {buy:'var(--green)',accumulate:'var(--teal)',hold:'var(--amber)',
          reduce:'var(--orange)',avoid:'var(--red)'}[label]||'var(--muted)';
}
function fillColor(v){
  if(v==null) return 'var(--muted)';
  if(v>=66) return 'var(--green)'; if(v>=45) return 'var(--amber)'; return 'var(--red)';
}
function bar(name,v){
  const w = v==null?0:Math.max(0,Math.min(100,v));
  const val = v==null?'n/a':v.toFixed(0);
  return `<div class="barrow"><div class="name">${name}</div>
    <div class="track"><div class="fill" style="width:${w}%;background:${fillColor(v)}"></div></div>
    <div class="val">${val}</div></div>`;
}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

async function run(){
  const sym=$('symbol').value.trim();
  if(!sym){setStatus('Enter a symbol.',true);return;}
  const p=new URLSearchParams({
    symbol:sym, source:$('source').value, apikey:$('apikey').value,
    csvdir:$('csvdir').value, benchmark:$('benchmark').value,
    fundamentals:$('fundamentals').checked?1:0,
    sentiment:$('sentiment').checked?1:0, events:$('events').checked?1:0
  });
  $('go').disabled=true; setStatus('Analyzing '+sym+'…');
  $('result').innerHTML='';
  try{
    const r=await fetch('/api/analyze?'+p.toString());
    const d=await r.json();
    if(d.error){setStatus('Error: '+d.error,true);}
    else{setStatus(''); render(d);}
  }catch(e){setStatus('Request failed: '+e,true);}
  finally{$('go').disabled=false;}
}
function setStatus(t,err){const s=$('status');s.textContent=t;s.className=err?'err':'';}

function render(d){
  const subs=d.subscores||{};
  const col=scoreColor(d.score_label);
  const sim=d.data_is_simulated?'<span class="sim">SIMULATED DATA</span>':'';
  const lv=d.levels||{};
  let html='';
  html+=`<div class="hero">
    <div class="scorecard">
      <div style="color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.6px">ATLAS Score</div>
      <div class="big" style="color:${col}">${d.atlas_score==null?'–':d.atlas_score}</div>
      <span class="chip" style="background:${col};color:#04101f">${(d.score_label||'').toUpperCase()}</span>
      <div class="meta"><b>${esc(d.symbol)}</b> · regime: ${esc(d.regime||'?')}<br>
        as of ${esc(d.asof||'?')}<br>${esc(d.score_horizon||'')} ${sim}</div>
    </div>
    <div class="grow">
      <h3>Sub-scores</h3>
      ${bar('technical',subs.technical)}
      ${bar('fundamental',subs.fundamental)}
      ${bar('sentiment',subs.sentiment)}
      ${bar('rel. strength',subs.relative_strength)}
      ${bar('risk',subs.risk)}
      ${d.confluence&&d.confluence.score!=null?bar('confluence (TA)',d.confluence.score):''}
      ${d.top_contributors?`<div class="kv" style="margin-top:10px">${d.top_contributors.map(esc).join(' · ')}</div>`:''}
    </div>
  </div>`;

  html+='<div class="grid">';
  // Levels
  html+='<div class="card"><h3>Levels · last '+esc(lv.last_close)+'</h3>';
  (lv.resistance||[]).slice(0,5).forEach(x=>{html+=`<div class="lvl res"><span>R ${x}</span></div>`;});
  html+=`<div style="height:6px"></div>`;
  (lv.support||[]).slice(0,5).forEach(x=>{html+=`<div class="lvl sup"><span>S ${x}</span></div>`;});
  html+='</div>';
  // Patterns
  html+='<div class="card"><h3>Patterns</h3>';
  const pats=d.patterns||{}; let any=false;
  ['candlestick','classical','harmonic'].forEach(f=>(pats[f]||[]).forEach(pp=>{
    any=true;const c=pp.direction==='bullish'?'bull':pp.direction==='bearish'?'bear':'';
    html+=`<span class="pill ${c}">${esc(pp.name)}</span>`;}));
  if(!any) html+='<div class="kv">none detected</div>';
  html+='</div>';
  html+='</div>';

  // Fundamentals / sentiment / events row
  const cards=[];
  if(d.fundamentals_detail){const f=d.fundamentals_detail;
    cards.push(`<div class="card"><h3>Fundamentals</h3>
      <div class="kv"><b>${esc(f.name||d.symbol)}</b> · ${esc(f.sector||'')} · score <b>${f.score}</b></div>
      <div class="kv">${(f.contributors||[]).map(esc).join('<br>')}</div></div>`);}
  if(d.sentiment_detail){const s=d.sentiment_detail;
    cards.push(`<div class="card"><h3>Sentiment</h3>
      <div class="kv">score <b>${s.score}</b> · ${s.articles} articles · avg ${s.avg_sentiment}</div>
      <div class="kv">${Object.entries(s.label_mix||{}).map(([k,v])=>esc(k)+': '+v).join(' · ')}</div></div>`);}
  if((d.events||[]).length){let e='<div class="card"><h3>Event risk</h3>';
    d.events.forEach(ev=>{e+=`<div class="ev"><span>${esc(ev.type)} · ${esc(ev.date)}</span>
      <span class="r-${ev.risk}">${ev.days_away}d · ${String(ev.risk||'').toUpperCase()}</span></div>`;});
    cards.push(e+'</div>');}
  if(cards.length) html+='<div class="grid">'+cards.join('')+'</div>';

  // Notes
  if((d.notes||[]).length){
    html+='<div class="card" style="margin-top:16px"><h3>Notes</h3><ul class="notes">'+
      d.notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul></div>';
  }
  html+='<details><summary>Raw JSON</summary><pre>'+esc(JSON.stringify(d,null,2))+'</pre></details>';
  html+='<div class="hint" style="margin-top:14px">'+esc(d.disclaimer||'')+'</div>';
  $('result').innerHTML=html;
}
toggleSource();
</script>
</body>
</html>"""


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
