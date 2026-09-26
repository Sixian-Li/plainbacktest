#!/usr/bin/env node
// Verify the QQQ/Bear9 robustness report and interactive_research_v5 contract in Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2]);
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-qqq-bear9-chrome-`);
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
      return document.readyState === 'complete' && graphs.length === 9 && graphs.every((graph) => graph.querySelector('.main-svg'));
    })()`);
    if (ready) break;
    if (attempt === 499) throw new Error('Nine QQQ/Bear9 figures did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(() => {
    const strategy = document.getElementById('strategy-definition');
    const ids = ['performance-qqq_bear9_rebalance','robustness-heatmap','layered-state','timing-sma160','timing-sma180','timing-sma200','timing-sma220','timing-sma240','timing-layered-190-310'];
    const graphs = ids.map((id) => document.getElementById(id));
    const body = document.body.innerText;
    return {
      graphCount: document.querySelectorAll('.plotly-graph-div').length,
      allGraphsPresent: graphs.every(Boolean),
      traceCounts: graphs.map((graph) => graph?.data?.length || 0),
      cardCount: document.querySelectorAll('.result-card').length,
      tableCount: document.querySelectorAll('.event-table').length,
      template: document.querySelector('meta[name="quant-report-template"]')?.content || '',
      hasFlow: Boolean(strategy?.querySelector('.strategy-flow')),
      hasRawCode: Boolean(strategy?.querySelector('pre')),
      rawCodeBlocks: document.querySelectorAll('pre, code').length,
      containsBear9: body.includes('Bear9'),
      containsLayered: body.includes('SMA190/310'),
      containsEx2000: body.includes('剔除2000'),
      containsRebalance: body.includes('不再平衡') && body.includes('30日'),
      containsWeights: ['AZO','SO','ED','ORLY','MO','WRB','DLTR','DG','WMT'].every((text) => body.includes(text))
    };
  })()`);
  const checks = [
    result.graphCount === 9,
    result.allGraphsPresent,
    result.traceCounts.every((value) => value >= 2),
    result.cardCount === 4,
    result.tableCount >= 2,
    result.template === 'interactive_research_v5',
    result.hasFlow,
    !result.hasRawCode,
    result.rawCodeBlocks === 0,
    result.containsBear9,
    result.containsLayered,
    result.containsEx2000,
    result.containsRebalance,
    result.containsWeights
  ];
  if (!checks.every(Boolean)) throw new Error(`QQQ/Bear9 report contract failed: ${JSON.stringify(result)}`);
  process.stdout.write(`QQQ/Bear9 UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
  client.close();
} finally {
  chrome.kill('SIGTERM'); await delay(100); rmSync(profile, {recursive: true, force: true});
}
