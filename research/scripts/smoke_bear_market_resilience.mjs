#!/usr/bin/env node
// Exercise the self-contained bear-market resilience report in real local Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2] || 'research/market_views/bear_market_resilience_current_constituents.html');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-bear-resilience-chrome-`);
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--allow-file-access-from-files',
  '--window-size=1800,1200', '--remote-debugging-port=0',
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
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const ready = await evaluate(client, `Boolean(window.__bearResilience && document.querySelectorAll('tbody[data-role="summary-body"] tr').length)`);
    if (ready) break;
    if (attempt === 159) throw new Error('Bear-market resilience report did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(function () {
    const initialRows = document.querySelectorAll('tbody[data-role="summary-body"] tr').length;
    const first = document.querySelector('tbody[data-role="summary-body"] tr');
    first.click();
    const selected = window.__bearResilience.selected;
    const bars = document.querySelectorAll('#detail-bars .bar-row').length;
    document.querySelector('[data-control="screened-only"]').checked = false;
    document.querySelector('[data-control="screened-only"]').dispatchEvent(new Event('input', {bubbles:true}));
    const allRows = document.querySelectorAll('tbody[data-role="summary-body"] tr').length;
    document.querySelector('[data-control="search"]').value = 'QQQ';
    document.querySelector('[data-control="search"]').dispatchEvent(new Event('input', {bubbles:true}));
    const qqqRows = document.querySelectorAll('tbody[data-role="summary-body"] tr').length;
    const qqqText = document.querySelector('tbody[data-role="summary-body"] tr')?.innerText || '';
    document.querySelector('th[data-sort="symbol"]').click();
    return {
      template: document.querySelector('meta[name="quant-view"]')?.content,
      summaries: window.__bearResilience.data.summaries.length,
      expectedSummaries: window.__bearResilience.data.metadata.counts.analyzed_assets,
      expectedScreened: window.__bearResilience.data.metadata.counts.screened_assets,
      details: window.__bearResilience.data.details.length,
      intervals: window.__bearResilience.data.intervals.length,
      initialRows, allRows, qqqRows, qqqText, selected, bars,
      sortKey: window.__bearResilience.sortKey,
      title: document.querySelector('[data-role="detail-title"]').textContent
    };
  }())`);
  client.close();
  if (result.template !== 'bear_market_resilience_current_constituents_v1' || result.summaries !== result.expectedSummaries || result.initialRows !== result.expectedScreened || result.details !== result.summaries * result.intervals || result.initialRows < 1 || result.allRows !== result.summaries || result.qqqRows !== 1 || !result.qqqText.includes('QQQ') || !result.selected || result.bars !== result.intervals || result.sortKey !== 'symbol') {
    throw new Error(`Bear-market resilience UI smoke test failed: ${JSON.stringify(result)}`);
  }
  process.stdout.write(`Bear-market resilience UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
}

try {
  await main();
} finally {
  chrome.kill('SIGTERM');
  await delay(500);
  rmSync(profile, {recursive:true, force:true, maxRetries:5, retryDelay:100});
}
