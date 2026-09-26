#!/usr/bin/env node
// Exercise the self-contained RSI/StochRSI market view in real local Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const viewPath = resolve(process.argv[2] || 'research/market_views/qqq_2020-01-01_2026-08-04_rsi_stochrsi_periods.html');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-rsi-stoch-view-chrome-`);
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
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(pathToFileURL(viewPath).href)}`, {method: 'PUT'});
  if (!response.ok) throw new Error(`Unable to create Chrome target: ${response.status}`);
  const target = await response.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.ready();
  await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const ready = await evaluate(client, `Boolean(window.__rsiStochView?.graph?.data?.length)`);
    if (ready) break;
    if (attempt === 239) throw new Error('RSI/StochRSI view did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(async function () {
    const state=window.__rsiStochView,graph=state.graph;
    const boxes=Array.from(state.controls.querySelectorAll('input[data-period]'));
    const initialChecked=boxes.filter((box)=>box.checked).map((box)=>Number(box.dataset.period));
    const p28=boxes.find((box)=>box.dataset.period==='28');
    const before28=state.periodIndices(28).map((index)=>graph.data[index].visible);
    p28.checked=true;p28.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise((done)=>setTimeout(done,250));
    const after28=state.periodIndices(28).map((index)=>graph.data[index].visible);
    state.controls.querySelector('[data-action="all"]').click();
    await new Promise((done)=>setTimeout(done,300));
    const allChecked=boxes.filter((box)=>box.checked).length;
    state.controls.querySelector('[data-action="none"]').click();
    await new Promise((done)=>setTimeout(done,300));
    const noneChecked=boxes.filter((box)=>box.checked).length;
    const p14=boxes.find((box)=>box.dataset.period==='14');p14.checked=true;p14.dispatchEvent(new Event('change',{bubbles:true}));
    await new Promise((done)=>setTimeout(done,250));
    return {
      template:document.querySelector('meta[name="quant-view"]')?.content,
      traces:graph.data.length,periodBoxes:boxes.length,initialChecked,before28,after28,
      allChecked,noneChecked,p14Visible:state.periodIndices(14).map((index)=>graph.data[index].visible),
      indicatorRange:graph.layout.yaxis2.range,xRange:graph.layout.xaxis2.range,
      visibleSvgTraces:graph.querySelectorAll('.scatterlayer .trace').length
    };
  }())`);
  client.close();
  if (
    result.template !== 'close_rsi_stochrsi_periods_v1'
    || result.traces !== 15 || result.periodBoxes !== 7
    || JSON.stringify(result.initialChecked) !== JSON.stringify([14])
    || !result.before28.every((value) => value === 'legendonly')
    || !result.after28.every((value) => value === true)
    || result.allChecked !== 7 || result.noneChecked !== 0
    || !result.p14Visible.every((value) => value === true)
    || result.indicatorRange[0] !== -0.03 || result.indicatorRange[1] !== 1.03
    || result.xRange[0] !== '2020-01-02' || result.xRange[1] !== '2026-08-04'
    || result.visibleSvgTraces < 3
  ) throw new Error(`RSI/StochRSI UI smoke test failed: ${JSON.stringify(result)}`);
  process.stdout.write(`RSI/StochRSI UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
}

try {
  await main();
} finally {
  chrome.kill('SIGTERM');
  await delay(500);
  rmSync(profile, {recursive:true, force:true, maxRetries:5, retryDelay:100});
}
