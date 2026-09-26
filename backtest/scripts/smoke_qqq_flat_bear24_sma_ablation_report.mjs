#!/usr/bin/env node
// Verify the Bear24 DIRECT/SMA report's specialty charts and v5 contract in Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2]);
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-qqq-flat-bear24-chrome-`);
const chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--allow-file-access-from-files', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], {stdio: 'ignore'});
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
      const msg = JSON.parse(event.data); if (!msg.id || !this.pending.has(msg.id)) return;
      const {resolveCommand, rejectCommand} = this.pending.get(msg.id); this.pending.delete(msg.id);
      if (msg.error) rejectCommand(new Error(msg.error.message)); else resolveCommand(msg.result);
    };
  }
  async ready() { if (this.socket.readyState === WebSocket.OPEN) return; await new Promise((ok, bad) => { this.socket.onopen = ok; this.socket.onerror = bad; }); }
  command(method, params = {}) { const id = this.id++; return new Promise((resolveCommand, rejectCommand) => { this.pending.set(id, {resolveCommand, rejectCommand}); this.socket.send(JSON.stringify({id, method, params})); }); }
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
  if (!response.ok) throw new Error(`Unable to create Chrome target: ${response.status}`);
  const target = await response.json(); const client = new Client(target.webSocketDebuggerUrl); await client.ready();
  await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 500; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const graphs = Array.from(document.querySelectorAll('.plotly-graph-div'));
      return document.readyState === 'complete' && graphs.length === 29 && graphs.every((graph) => graph.querySelector('.main-svg'));
    })()`);
    if (ready) break;
    if (attempt === 499) throw new Error('Twenty-nine Bear24 figures did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(() => {
    const strategy = document.getElementById('strategy-definition');
    const performance = document.getElementById('performance-qqq_flat_substitution');
    const market = document.getElementById('market-qqq');
    const assets = ['azo','tlt','cor','exe','dva','sjm','so','ed','gld','chd','hrl','gild','hsy','orly','mo','wrb','eqt','lmt','gis','wec','dltr','dg','wmt','tsco'];
    const assetGraphs = assets.map((id) => document.getElementById('asset-' + id));
    return {
      graphCount: document.querySelectorAll('.plotly-graph-div').length,
      performanceTraceCount: performance?.data?.length || 0,
      marketTraceCount: market?.data?.length || 0,
      marketShadeCount: market?.layout?.shapes?.length || 0,
      assetTraceCounts: assetGraphs.map((graph) => graph?.data?.length || 0),
      cardCount: document.querySelectorAll('.result-card').length,
      tableCount: document.querySelectorAll('.event-table').length,
      template: document.querySelector('meta[name="quant-report-template"]')?.content || '',
      hasFlow: Boolean(strategy?.querySelector('.strategy-flow')),
      hasRawCode: Boolean(strategy?.querySelector('pre')),
      rawCodeBlocks: document.querySelectorAll('pre, code').length,
      containsEx2000: document.body.innerText.includes('剔除2000'),
      containsDirect: document.body.innerText.includes('不筛选'),
      containsGroups: ['核心12', '近核心8', '零售4'].every((text) => document.body.innerText.includes(text)),
      containsMasterWording: document.body.innerText.includes('QQQ是唯一账户总开关')
    };
  })()`);
  const checks = [
    result.graphCount === 29,
    result.performanceTraceCount === 100,
    result.marketTraceCount === 6,
    result.marketShadeCount === 24,
    result.assetTraceCounts.every((value) => value === 4),
    result.cardCount === 4,
    result.tableCount === 3,
    result.template === 'interactive_research_v5',
    result.hasFlow,
    !result.hasRawCode,
    result.rawCodeBlocks === 0,
    result.containsEx2000,
    result.containsDirect,
    result.containsGroups,
    result.containsMasterWording
  ];
  if (!checks.every(Boolean)) throw new Error(`Bear24 ablation report contract failed: ${JSON.stringify(result)}`);
  process.stdout.write(`Bear24 ablation UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
  client.close();
} finally {
  chrome.kill('SIGTERM'); await delay(100); rmSync(profile, {recursive: true, force: true});
}
