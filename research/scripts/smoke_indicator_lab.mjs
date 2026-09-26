#!/usr/bin/env node
// Launch the Streamlit indicator lab and exercise it in real local Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';


const root = resolve(import.meta.dirname, '../..');
const app = resolve(root, 'research/indicator_lab/app.py');
const python = resolve(root, 'backtest/.venv/bin/python');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = Number(process.env.QUANT_INDICATOR_LAB_SMOKE_PORT || 18765);
const url = `http://127.0.0.1:${port}`;
const profile = mkdtempSync(`${tmpdir()}/quant-indicator-lab-chrome-`);
let serverOutput = '';

const server = spawn(python, [
  '-m', 'streamlit', 'run', app,
  '--server.headless', 'true',
  '--server.address', '127.0.0.1',
  '--server.port', String(port),
  '--browser.gatherUsageStats', 'false'
], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
server.stdout.on('data', (chunk) => { serverOutput += chunk.toString(); });
server.stderr.on('data', (chunk) => { serverOutput += chunk.toString(); });

const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--window-size=1800,1200',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'
], {stdio: 'ignore'});

const delay = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitForServer() {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    if (server.exitCode !== null) throw new Error(`Streamlit exited early:\n${serverOutput}`);
    try {
      const response = await fetch(`${url}/_stcore/health`);
      if (response.ok) return;
    } catch {}
    await delay(100);
  }
  throw new Error(`Streamlit health check timed out:\n${serverOutput}`);
}

async function waitForChromePort() {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    try {
      return Number(readFileSync(`${profile}/DevToolsActivePort`, 'utf8').trim().split('\n')[0]);
    } catch { await delay(100); }
  }
  throw new Error('Chrome DevTools port did not become ready');
}

class CdpClient {
  constructor(socketUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(socketUrl);
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
    await new Promise((done, fail) => {
      this.socket.onopen = done;
      this.socket.onerror = fail;
    });
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
  const response = await client.command('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
  }
  return response.result.value;
}

async function waitFor(client, expression, label) {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (await evaluate(client, expression)) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

async function main() {
  await Promise.all([waitForServer(), waitForChromePort()]);
  const debugPort = await waitForChromePort();
  const response = await fetch(
    `http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`,
    {method: 'PUT'}
  );
  if (!response.ok) throw new Error(`Unable to create Chrome target: ${response.status}`);
  const target = await response.json();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.ready();
  await client.command('Runtime.enable');
  await waitFor(
    client,
    `Boolean(document.body.innerText.includes('Quant 动态指标与策略实验台') && document.querySelector('.js-plotly-plot')?.data?.length >= 7)`,
    'rendered indicator chart'
  );
  const initial = await evaluate(client, `(() => {
    const graph=document.querySelector('.js-plotly-plot');
    return {
      title:document.querySelector('h1')?.innerText,
      traces:graph.data.map((trace)=>trace.name),
      paperBackground:graph._fullLayout.paper_bgcolor,
      plotBackground:graph._fullLayout.plot_bgcolor,
      fontColor:graph._fullLayout.font.color,
      sessions:document.body.innerText.includes('9,214'),
      inputs:Array.from(document.querySelectorAll('[data-testid="stNumberInput"]')).map((item)=>item.innerText),
      checkboxes:Array.from(document.querySelectorAll('[data-testid="stCheckbox"]')).map((item)=>item.innerText)
    };
  })()`);
  const toggled = await evaluate(client, `(async () => {
    const target=Array.from(document.querySelectorAll('[data-testid="stCheckbox"]')).find((item)=>item.innerText.includes('显示 K'));
    if(!target) throw new Error('K checkbox not found');
    target.querySelector('input').click();
    for(let attempt=0;attempt<120;attempt+=1){
      await new Promise((done)=>setTimeout(done,100));
      const graph=document.querySelector('.js-plotly-plot');
      if(graph&&graph.data&&!graph.data.some((trace)=>trace.name==='K')) return graph.data.map((trace)=>trace.name);
    }
    throw new Error('K trace did not disappear after checkbox click');
  })()`);
  client.close();
  if (
    initial.title !== 'Quant 动态指标与策略实验台' ||
    !initial.sessions ||
    !initial.traces.includes('Raw StochRSI') ||
    !initial.traces.includes('K') ||
    !initial.traces.includes('D') ||
    initial.paperBackground !== '#ffffff' ||
    initial.plotBackground !== '#f8fafc' ||
    initial.fontColor !== '#0f172a' ||
    !initial.inputs.some((label) => label.includes('RSI 周期')) ||
    toggled.includes('K')
  ) {
    throw new Error(`Indicator lab browser smoke failed: ${JSON.stringify({initial, toggled})}`);
  }
  process.stdout.write(`Indicator lab browser smoke: PASS\n${JSON.stringify({initial, toggled}, null, 2)}\n`);
}

try { await main(); }
finally {
  server.kill('SIGTERM');
  chrome.kill('SIGTERM');
  await delay(700);
  rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
