#!/usr/bin/env node
// Exercise the self-contained bear-market annotator in a real local Chrome instance.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2] || 'research/market_views/spy_qqq_bear_market_annotator.html');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-bear-annotator-chrome-`);
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--allow-file-access-from-files',
  '--window-size=1800,1200', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'
], {stdio: 'ignore'});

const delay = (ms) => new Promise((resolveDelay) => setTimeout(resolveDelay, ms));

async function waitForPort() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
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
    await new Promise((resolveSocket, rejectSocket) => {
      this.socket.onopen = resolveSocket;
      this.socket.onerror = rejectSocket;
    });
  }
  command(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolveCommand, rejectCommand) => {
      this.pending.set(id, {resolve: resolveCommand, reject: rejectCommand});
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
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const ready = await evaluate(client, `Boolean(window.__bearAnnotator && document.getElementById('bear-market-chart')?._fullLayout?.xaxis2)`);
    if (ready) break;
    if (attempt === 159) throw new Error('Bear-market annotator did not become ready');
    await delay(100);
  }
  const drag = await evaluate(client, `(function () {
    const graph = document.getElementById('bear-market-chart');
    const rect = graph.getBoundingClientRect();
    const axis = graph._fullLayout.xaxis;
    const yaxis = graph._fullLayout.yaxis;
    return {
      startX: rect.left + axis._offset + axis._length * 0.70,
      endX: rect.left + axis._offset + axis._length * 0.73,
      y: rect.top + yaxis._offset + yaxis._length / 2
    };
  }())`);
  await client.command('Input.dispatchMouseEvent', {type: 'mouseMoved', x: drag.startX, y: drag.y});
  await client.command('Input.dispatchMouseEvent', {type: 'mousePressed', x: drag.startX, y: drag.y, button: 'left', buttons: 1, clickCount: 1});
  for (let step = 1; step <= 8; step += 1) {
    const x = drag.startX + (drag.endX - drag.startX) * step / 8;
    await client.command('Input.dispatchMouseEvent', {type: 'mouseMoved', x, y: drag.y + Math.sin(step / 8 * Math.PI) * 3, button: 'left', buttons: 1});
  }
  await client.command('Input.dispatchMouseEvent', {type: 'mouseReleased', x: drag.endX, y: drag.y, button: 'left', clickCount: 1});
  await delay(500);
  const result = await evaluate(client, `(async function () {
    const graph = document.getElementById('bear-market-chart');
    if (window.__bearAnnotator.getIntervals().length !== 1) throw new Error('Real horizontal drag did not create an interval');
    await new Promise((resolveWait) => setTimeout(resolveWait, 350));
    const row = document.querySelector('tbody[data-role="intervals"] tr');
    const label = row.querySelector('input[data-field="label"]');
    label.value = '疫情熊市'; label.dispatchEvent(new Event('change', {bubbles: true}));
    await new Promise((resolveWait) => setTimeout(resolveWait, 350));
    document.querySelector('[data-mode="zoom"]').click();
    await new Promise((resolveWait) => setTimeout(resolveWait, 150));
    const payload = window.__bearAnnotator.payload();
    return {
      traces: graph.data.length,
      shapeCount: graph.layout.shapes?.length || 0,
      annotationCount: graph.layout.annotations?.filter((item) => item.text === '疫情熊市').length || 0,
      rows: document.querySelectorAll('tbody[data-role="intervals"] tr').length,
      dragmode: graph.layout.dragmode,
      stored: JSON.parse(localStorage.getItem('quant:spy_qqq_bear_market_annotator_v1:intervals') || '[]'),
      interval: payload.intervals[0],
      xRange: graph._fullLayout.xaxis.range,
      x2Range: graph._fullLayout.xaxis2.range,
      firstX: graph.data[0].x[0],
      lastX: graph.data[0].x[graph.data[0].x.length - 1],
      firstY: graph.data[0].y[0],
      lastY: graph.data[0].y[graph.data[0].y.length - 1],
      template: document.querySelector('meta[name="quant-view"]')?.content
    };
  }())`);
  client.close();
  const interval = result.interval;
  if (result.traces !== 2 || result.shapeCount !== 1 || result.annotationCount !== 1 || result.rows !== 1 || result.dragmode !== 'zoom' || result.stored.length !== 1 || interval.label !== '疫情熊市' || interval.start >= interval.end || interval.trading_sessions < 2 || !Number.isFinite(interval.QQQ.max_drawdown) || !Number.isFinite(interval.SPY.max_drawdown) || !Number.isFinite(result.firstY) || !Number.isFinite(result.lastY)) {
    throw new Error(`Bear-market annotator smoke test failed: ${JSON.stringify(result)}`);
  }
  process.stdout.write(`Bear-market annotator UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
}

try {
  await main();
} finally {
  chrome.kill('SIGTERM');
  await delay(500);
  rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
