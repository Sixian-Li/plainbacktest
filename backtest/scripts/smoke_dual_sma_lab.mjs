#!/usr/bin/env node
// Launch the Streamlit dual-SMA lab and generate its default 11,134-cell heatmap.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';


const backtestRoot = resolve(import.meta.dirname, '..');
const app = resolve(backtestRoot, 'dual_sma_lab/app.py');
const python = resolve(backtestRoot, '.venv/bin/python');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = Number(process.env.QUANT_DUAL_SMA_LAB_SMOKE_PORT || 18766);
const url = `http://127.0.0.1:${port}`;
const profile = mkdtempSync(`${tmpdir()}/quant-dual-sma-lab-chrome-`);
let serverOutput = '';

const server = spawn(python, [
  '-m', 'streamlit', 'run', app,
  '--server.headless', 'true',
  '--server.address', '127.0.0.1',
  '--server.port', String(port),
  '--browser.gatherUsageStats', 'false'
], {cwd: backtestRoot, stdio: ['ignore', 'pipe', 'pipe']});
server.stdout.on('data', (chunk) => { serverOutput += chunk.toString(); });
server.stderr.on('data', (chunk) => { serverOutput += chunk.toString(); });

const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--window-size=1800,1400',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'
], {stdio: 'ignore'});

const delay = (milliseconds) => new Promise((done) => setTimeout(done, milliseconds));

async function waitForServer() {
  for (let attempt = 0; attempt < 240; attempt += 1) {
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
  for (let attempt = 0; attempt < 240; attempt += 1) {
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

async function waitFor(client, expression, label, attempts = 400) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (await evaluate(client, expression)) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}\n${serverOutput}`);
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
    `Boolean(document.body?.innerText?.includes('双均线策略实验台') && document.querySelector('.js-plotly-plot'))`,
    'single-parameter result'
  );
  const initial = await evaluate(client, `(() => ({
    title:document.querySelector('h1')?.innerText,
    text:document.body.innerText,
    traceNames:Array.from(document.querySelectorAll('.js-plotly-plot'))[0]?.data?.map((trace)=>trace.name),
    errors:Array.from(document.querySelectorAll('[data-testid="stException"]')).map((item)=>item.innerText)
  }))()`);
  await evaluate(client, `(() => {
    const tab=Array.from(document.querySelectorAll('[role="tab"]')).find((item)=>item.innerText.includes('参数热力图'));
    if(!tab) throw new Error('grid tab not found');
    tab.click();
    return true;
  })()`);
  await waitFor(
    client,
    `Array.from(document.querySelectorAll('button')).some((item)=>item.innerText.includes('生成 / 刷新热力图'))`,
    'grid submit button'
  );
  await evaluate(client, `(() => {
    const button=Array.from(document.querySelectorAll('button')).find((item)=>item.innerText.includes('生成 / 刷新热力图'));
    if(!button) throw new Error('grid button not found');
    button.click();
    return true;
  })()`);
  await waitFor(
    client,
    `Array.from(document.querySelectorAll('.js-plotly-plot')).some((graph)=>graph.data?.some((trace)=>trace.type==='heatmap')) && document.body?.innerText?.includes('有效参数 11,134 组') && document.body?.innerText?.includes('下载完整网格 CSV') && document.body?.innerText?.includes('仅样本内描述')`,
    'default grid heatmap',
    600
  );
  const grid = await evaluate(client, `(() => {
    const graph=Array.from(document.querySelectorAll('.js-plotly-plot')).find((item)=>item.data?.some((trace)=>trace.type==='heatmap'));
    const heatmap=graph.data.find((trace)=>trace.type==='heatmap');
    return {
      title:graph._fullLayout.title.text,
      fastRows:heatmap.y.length,
      slowColumns:heatmap.x.length,
      hover:heatmap.hovertemplate,
      hasExport:document.body.innerText.includes('下载完整网格 CSV'),
      hasWarning:document.body.innerText.includes('仅样本内描述'),
      errors:Array.from(document.querySelectorAll('[data-testid="stException"]')).map((item)=>item.innerText)
    };
  })()`);
  client.close();
  if (
    initial.title !== '双均线策略实验台' ||
    !initial.text.includes('日历 CAGR') ||
    !initial.text.includes('持仓 Sharpe') ||
    !initial.traceNames.includes('双均线策略') ||
    !initial.traceNames.includes('AAPL Buy & Hold') ||
    initial.errors.length ||
    grid.fastRows !== 50 ||
    grid.slowColumns !== 236 ||
    !grid.hover.includes('持仓 Sharpe') ||
    !grid.hasExport ||
    !grid.hasWarning ||
    grid.errors.length
  ) {
    throw new Error(`Dual-SMA lab browser smoke failed: ${JSON.stringify({initial, grid})}`);
  }
  process.stdout.write(`Dual-SMA lab browser smoke: PASS\n${JSON.stringify({
    initial:{title:initial.title, traceNames:initial.traceNames}, grid
  }, null, 2)}\n`);
}

try { await main(); }
finally {
  server.kill('SIGTERM');
  chrome.kill('SIGTERM');
  await delay(700);
  rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
