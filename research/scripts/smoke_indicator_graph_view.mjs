#!/usr/bin/env node
// Exercise the self-contained indicator graph in real local Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const viewPath = resolve(process.argv[2] || 'research/market_views/aapl_1990-01-02_2026-08-04_sma10-350_stochrsi14-210_graph.html');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-indicator-graph-chrome-`);
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--allow-file-access-from-files',
  '--window-size=1800,1200', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'
], {stdio: 'ignore'});

const delay = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitForPort() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try { return Number(readFileSync(`${profile}/DevToolsActivePort`, 'utf8').trim().split('\n')[0]); }
    catch { await delay(100); }
  }
  throw new Error('Chrome DevTools port did not become ready');
}

class CdpClient {
  constructor(url) {
    this.nextId = 1; this.pending = new Map(); this.socket = new WebSocket(url);
    this.socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const pending = this.pending.get(message.id); this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message)); else pending.resolve(message.result);
    };
  }
  async ready() { if (this.socket.readyState === WebSocket.OPEN) return; await new Promise((done, fail) => { this.socket.onopen = done; this.socket.onerror = fail; }); }
  command(method, params = {}) { const id = this.nextId++; return new Promise((done, fail) => { this.pending.set(id, {resolve: done, reject: fail}); this.socket.send(JSON.stringify({id, method, params})); }); }
  close() { this.socket.close(); }
}

async function evaluate(client, expression) {
  const result = await client.command('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}

async function main() {
  const port = await waitForPort();
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(pathToFileURL(viewPath).href)}`, {method: 'PUT'});
  if (!response.ok) throw new Error(`Unable to create Chrome target: ${response.status}`);
  const target = await response.json(), client = new CdpClient(target.webSocketDebuggerUrl);
  await client.ready(); await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (await evaluate(client, 'Boolean(window.__indicatorGraph?.ready)')) break;
    if (attempt === 299) throw new Error('Indicator graph did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(async function(){
    const api=window.__indicatorGraph,graph=api.graph,allBoxes=Array.from(api.controls.querySelectorAll('input[data-series]'));
    const initialChecked=allBoxes.filter((box)=>box.checked).map((box)=>box.dataset.series);
    const sma20=allBoxes.find((box)=>box.dataset.series==='sma20'),before20=graph.data[api.indices('sma20')[0]].visible;
    sma20.checked=true;sma20.dispatchEvent(new Event('change',{bubbles:true}));await new Promise((done)=>setTimeout(done,250));const after20=graph.data[api.indices('sma20')[0]].visible;
    await api.setGroup('sma','all');const smaAll=api.boxes('sma').filter((box)=>box.checked).length;
    await api.setGroup('sma','none');const smaNone=api.boxes('sma').filter((box)=>box.checked).length;
    await api.setGroup('sma','default');const smaDefault=api.boxes('sma').filter((box)=>box.checked).map((box)=>box.dataset.series);
    await api.setGroup('stochrsi','all');const stochAll=api.boxes('stochrsi').filter((box)=>box.checked).length;
    await api.setGroup('stochrsi','default');
    const log=document.querySelector('[data-action="log"]');log.checked=false;log.dispatchEvent(new Event('change',{bubbles:true}));await new Promise((done)=>setTimeout(done,250));const linear=graph.layout.yaxis.type;
    await Plotly.relayout(graph,{'xaxis2.range':['1990-01-02','1991-01-31']});await api.rescalePrice();
    return {template:document.querySelector('meta[name="quant-view"]')?.content,traces:graph.data.length,sessions:graph.data[0].x.length,boxes:allBoxes.length,initialChecked,before20,after20,smaAll,smaNone,smaDefault,stochAll,linear,priceRange:graph.layout.yaxis.range,stochRange:graph.layout.yaxis2.range,visibleSvgTraces:graph.querySelectorAll('.scatterlayer .trace').length};
  }())`);
  client.close();
  if (result.template !== 'full_history_sma_stochrsi_graph_v1' || result.traces !== 51 || result.sessions !== 9214 || result.boxes !== 51 || result.initialChecked.length !== 9 || result.before20 !== 'legendonly' || result.after20 !== true || result.smaAll !== 35 || result.smaNone !== 0 || JSON.stringify(result.smaDefault) !== JSON.stringify(['sma50','sma100','sma200','sma350']) || result.stochAll !== 15 || result.linear !== 'linear' || !Array.isArray(result.priceRange) || result.priceRange.length !== 2 || result.stochRange[0] !== -0.03 || result.stochRange[1] !== 1.03 || result.visibleSvgTraces < 5) throw new Error(`Indicator graph UI smoke test failed: ${JSON.stringify(result)}`);
  process.stdout.write(`Indicator graph UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
}

try { await main(); }
finally { chrome.kill('SIGTERM'); await delay(500); rmSync(profile, {recursive:true, force:true, maxRetries:5, retryDelay:100}); }
