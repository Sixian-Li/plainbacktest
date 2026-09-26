#!/usr/bin/env node
// Exercise the P24 performance controls and v5 strategy flow in real Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2]);
const graphId = 'performance';
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-p24-report-chrome-`);
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--allow-file-access-from-files',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'
], {stdio: 'ignore'});
const delay = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitForPort() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try { return Number(readFileSync(`${profile}/DevToolsActivePort`, 'utf8').trim().split('\n')[0]); }
    catch { await delay(100); }
  }
  throw new Error('Chrome DevTools port did not become ready');
}

class Client {
  constructor(url) {
    this.id = 1; this.pending = new Map(); this.socket = new WebSocket(url);
    this.socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const {resolveCommand, rejectCommand} = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) rejectCommand(new Error(message.error.message));
      else resolveCommand(message.result);
    };
  }
  async ready() {
    if (this.socket.readyState === WebSocket.OPEN) return;
    await new Promise((ok, bad) => { this.socket.onopen = ok; this.socket.onerror = bad; });
  }
  command(method, params = {}) {
    const id = this.id++;
    return new Promise((resolveCommand, rejectCommand) => {
      this.pending.set(id, {resolveCommand, rejectCommand});
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

try {
  const port = await waitForPort();
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(pathToFileURL(reportPath).href)}`, {method: 'PUT'});
  const target = await response.json();
  const client = new Client(target.webSocketDebuggerUrl);
  await client.ready();
  await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const ready = await evaluate(client, `Boolean(
      document.getElementById('${graphId}')?.data?.length &&
      document.querySelector('.performance-controls[data-target="${graphId}"]')?.dataset?.initialized === 'true'
    )`);
    if (ready) break;
    if (attempt === 159) throw new Error('P24 performance graph did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(async function () {
    const graph = document.getElementById('${graphId}');
    const controls = document.querySelector('.performance-controls[data-target="${graphId}"]');
    const equity = graph.data.filter((trace) => trace.meta?.panel === 'equity');
    const benchmark = equity.find((trace) => trace.meta?.is_benchmark === true);
    if (!benchmark) throw new Error('Missing benchmark equity trace');
    const dates = Array.from(benchmark.x);
    const start = dates[Math.floor(dates.length * 0.2)];
    const end = dates[Math.ceil(dates.length * 0.8)];
    const update = {'xaxis.range': [start, end]};
    if (graph._fullLayout.xaxis2) update['xaxis2.range'] = [start, end];
    await Plotly.relayout(graph, update);
    controls.querySelector('[data-action="rebase-visible"]').click();
    await new Promise((done) => setTimeout(done, 500));
    const firstValue = (trace) => {
      const index = Array.from(trace.x).findIndex((date) => new Date(date) >= new Date(start));
      return Number(trace.y[index]);
    };
    const rebased = graph.data.filter((trace) => trace.meta?.panel === 'equity');
    const rebasedBenchmark = rebased.find((trace) => trace.meta?.is_benchmark === true);
    const aligned = rebased
      .filter((trace) => trace.meta?.series_key !== 'dca_visible_range')
      .every((trace) => Math.abs(firstValue(trace) - firstValue(rebasedBenchmark)) < 1e-6);
    const nonBenchmark = rebased.find((trace) => !trace.meta?.is_benchmark && trace.meta?.series_key !== 'dca_visible_range');
    const checkbox = controls.querySelector('input[data-series="' + nonBenchmark.meta.series_key + '"]');
    checkbox.checked = false;
    checkbox.dispatchEvent(new Event('change'));
    await new Promise((done) => setTimeout(done, 250));
    const hidden = graph.data
      .filter((trace) => trace.meta?.series_key === nonBenchmark.meta.series_key)
      .every((trace) => trace.visible === 'legendonly');
    return {
      aligned, hidden,
      template: document.querySelector('meta[name="quant-report-template"]')?.content,
      hasFlow: Boolean(document.querySelector('#strategy-definition .strategy-flow')),
      hasRawCode: Boolean(document.querySelector('#strategy-definition pre')),
      hasBuy: document.body.innerText.includes('什么时候买'),
      hasSell: document.body.innerText.includes('什么时候卖'),
      hasExecution: document.body.innerText.includes('信号如何变成成交')
    };
  }())`);
  client.close();
  if (!result.aligned || !result.hidden || result.template !== 'interactive_research_v5' || !result.hasFlow || result.hasRawCode || !result.hasBuy || !result.hasSell || !result.hasExecution) {
    throw new Error(`P24 report smoke test failed: ${JSON.stringify(result)}`);
  }
  process.stdout.write(`P24 report UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
} finally {
  chrome.kill('SIGTERM');
  await delay(100);
  rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
