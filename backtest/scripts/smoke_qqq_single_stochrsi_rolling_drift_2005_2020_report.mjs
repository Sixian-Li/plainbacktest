#!/usr/bin/env node
// Verify the eleven rolling-window selectors and frozen v5 report in real Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2]);
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-stochrsi-drift-`);
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--allow-file-access-from-files', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'
], {stdio: 'ignore'});

const delay = (ms) => new Promise((done) => setTimeout(done, ms));

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

class Client {
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
  const target = await response.json();
  const client = new Client(target.webSocketDebuggerUrl);
  await client.ready();
  await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const ready = await evaluate(client, `['market-qqq','cagr-response','sharpe-response','performance-qqq'].every((id) => document.getElementById(id)?.data?.length)`);
    if (ready) break;
    if (attempt === 179) throw new Error('Report charts did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(async function () {
    const responses = [];
    for (const id of ['market-qqq','cagr-response','sharpe-response']) {
      const graph = document.getElementById(id);
      const controls = document.querySelector('.market-controls[data-target="' + id + '"]');
      const traces = graph.data.filter((trace) => trace.meta?.panel === 'market');
      const boxes = Array.from(controls.querySelectorAll('input[data-series]'));
      const first = boxes[0];
      first.checked = false;
      first.dispatchEvent(new Event('change'));
      await new Promise((done) => setTimeout(done, 250));
      const hidden = graph.data.filter((trace) => trace.meta?.series_key === first.dataset.series).every((trace) => trace.visible === 'legendonly');
      first.checked = true;
      first.dispatchEvent(new Event('change'));
      await new Promise((done) => setTimeout(done, 250));
      const restored = graph.data.filter((trace) => trace.meta?.series_key === first.dataset.series).every((trace) => trace.visible === true);
      responses.push({id, traceCount: traces.length, checkboxCount: boxes.length, hidden, restored, firstX: traces[0].x[0], lastX: traces[0].x.at(-1)});
    }
    const performance = document.getElementById('performance-qqq');
    const benchmark = performance.data.find((trace) => trace.meta?.panel === 'equity' && trace.meta?.is_benchmark === true);
    return {
      responses,
      hasBenchmark: Boolean(benchmark),
      template: document.querySelector('meta[name="quant-report-template"]')?.content,
      hasFlow: Boolean(document.querySelector('.strategy-flow')),
      hasRawCode: Boolean(document.querySelector('#strategy-definition pre')),
      parameterDisplay: getComputedStyle(document.querySelector('.strategy-parameters')).display
    };
  }())`);
  client.close();
  const valid = result.responses.every((item) => item.traceCount === 11 && item.checkboxCount === 11 && item.hidden && item.restored && Number(item.firstX) === 14 && Number(item.lastX) === 210);
  if (!valid || !result.hasBenchmark || result.template !== 'interactive_research_v5' || !result.hasFlow || result.hasRawCode) {
    throw new Error(`Rolling drift report smoke failed: ${JSON.stringify(result)}`);
  }
  process.stdout.write(`Rolling drift report smoke: PASS\n${JSON.stringify(result, null, 2)}\n`);
}

try {
  await main();
} finally {
  chrome.kill('SIGTERM');
  await delay(100);
  rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
