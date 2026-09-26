#!/usr/bin/env node
// Exercise the self-contained 24-asset history view in real local Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2] || 'research/market_views/bear_resilience_24_full_history_sma_bear_intervals.html');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-bear-history-chrome-`);
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--allow-file-access-from-files',
  '--window-size=1800,1200', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'
], {stdio: 'ignore'});

const delay = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitForPort() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      return Number(readFileSync(`${profile}/DevToolsActivePort`, 'utf8').trim().split('\n')[0]);
    } catch {
      await delay(100);
    }
  }
  throw new Error('Chrome DevTools port did not become ready');
}

class CdpClient {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(url);
    this.socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const pending = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    };
  }
  async ready() {
    if (this.socket.readyState === WebSocket.OPEN) return;
    await new Promise((done, fail) => { this.socket.onopen = done; this.socket.onerror = fail; });
  }
  command(method, params = {}) {
    const id = this.nextId++;
    return new Promise((done, fail) => {
      this.pending.set(id, {resolve: done, reject: fail});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }
  close() { this.socket.close(); }
}

async function evaluate(client, expression) {
  const result = await client.command('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}

async function main() {
  const port = await waitForPort();
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(pathToFileURL(reportPath).href)}`, {method: 'PUT'});
  if (!response.ok) throw new Error(`Unable to create Chrome target: ${response.status}`);
  const target = await response.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.ready();
  await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const ready = await evaluate(client, `Boolean(window.__bearHistory?.ready && window.__bearHistory.rendered.has('AZO'))`);
    if (ready) break;
    if (attempt === 239) throw new Error('Bear history view did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(async function () {
    const state=window.__bearHistory;
    const first=document.querySelector('[data-symbol="AZO"]');
    const firstPlot=first.querySelector('.plot');
    const initialShapes=firstPlot.layout.shapes.length;
    const sma30=first.querySelector('[data-control="sma"][data-window="30"]');
    sma30.checked=true; sma30.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise((done)=>setTimeout(done,250));
    const sma30Visible=firstPlot.data[1].visible;
    const bear=first.querySelector('[data-control="bear-mode"]');
    bear.value='major'; bear.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise((done)=>setTimeout(done,250));
    const majorShapes=firstPlot.layout.shapes.length;
    bear.value='minor'; bear.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise((done)=>setTimeout(done,250));
    const minorShapes=firstPlot.layout.shapes.length;
    bear.value='off'; bear.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise((done)=>setTimeout(done,250));
    const offShapes=firstPlot.layout.shapes.length;
    const log=first.querySelector('[data-control="log"]');
    log.checked=false; log.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise((done)=>setTimeout(done,250));
    const linear=firstPlot.layout.yaxis.type;
    const last=document.querySelector('[data-symbol="TSCO"]');
    await state.renderAsset(last);
    return {
      template:document.querySelector('meta[name="quant-view"]')?.content,
      assets:state.data.assets.length,
      cards:state.cards.length,
      intervals:state.data.intervals.length,
      majorCount:state.data.intervals.filter((item)=>item.severity==='major').length,
      minorCount:state.data.intervals.filter((item)=>item.severity==='minor').length,
      initialShapes,majorShapes,minorShapes,offShapes,sma30Visible,linear,
      lastRendered:state.rendered.has('TSCO'),
      firstTraces:firstPlot.data.length,
      visibleSvgTraces:first.querySelectorAll('.scatterlayer .trace').length,
      firstXRange:firstPlot.layout.xaxis.range
    };
  }())`);
  client.close();
  if (
    result.template !== 'bear_resilience_24_full_history_sma_bear_intervals_v1'
    || result.assets !== 24 || result.cards !== 24 || result.intervals !== 12
    || result.majorCount !== 6 || result.minorCount !== 6
    || result.initialShapes !== 12 || result.majorShapes !== 6 || result.minorShapes !== 6 || result.offShapes !== 0
    || result.sma30Visible !== true || result.linear !== 'linear' || !result.lastRendered
    || result.firstTraces !== 6 || result.visibleSvgTraces < 2
    || result.firstXRange[0] !== '1999-01-01' || result.firstXRange[1] !== '2026-08-04'
  ) throw new Error(`Bear history UI smoke test failed: ${JSON.stringify(result)}`);
  process.stdout.write(`Bear history UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
}

try {
  await main();
} finally {
  chrome.kill('SIGTERM');
  await delay(500);
  rmSync(profile, {recursive:true, force:true, maxRetries:5, retryDelay:100});
}
