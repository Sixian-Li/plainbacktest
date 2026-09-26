#!/usr/bin/env node
// Exercise the Strategy1 energy-factor report and v5 contract in real Chrome.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2]);
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-energy-factor-chrome-`);
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
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const {resolveCommand, rejectCommand} = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) rejectCommand(new Error(message.error.message));
      else resolveCommand(message.result);
    };
  }
  async ready() { if (this.socket.readyState !== WebSocket.OPEN) await new Promise((ok, bad) => { this.socket.onopen = ok; this.socket.onerror = bad; }); }
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
  if (!response.ok) throw new Error(`Unable to create Chrome target: ${response.status}`);
  const target = await response.json();
  const client = new Client(target.webSocketDebuggerUrl);
  await client.ready(); await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 600; attempt += 1) {
    const ready = await evaluate(client, `(() => {
      const graphs = Array.from(document.querySelectorAll('.plotly-graph-div'));
      return document.readyState === 'complete' && graphs.length === 5 && graphs.every((graph) => graph.querySelector('.main-svg'));
    })()`);
    if (ready) break;
    if (attempt === 599) throw new Error('Five energy-factor figures did not become ready');
    await delay(100);
  }
  const result = await evaluate(client, `(() => {
    const ids = ['bucket-energy-factor', 'rank-ic-energy-factor', 'spreads-energy-factor', 'era-energy-factor', 'path-energy-factor'];
    const strategy = document.getElementById('strategy-definition');
    const details = strategy.querySelector('.strategy-parameters');
    const wasOpen = details.open; details.querySelector('summary').click(); const opened = details.open; details.querySelector('summary').click();
    return {
      graphCount: document.querySelectorAll('.plotly-graph-div').length,
      traceCounts: ids.map((id) => document.getElementById(id)?.data?.length || 0),
      navCount: document.querySelectorAll('.nav a').length,
      detailsInteraction: !wasOpen && opened && !details.open,
      template: document.querySelector('meta[name="quant-report-template"]')?.content || '',
      hasFlow: Boolean(strategy?.querySelector('.strategy-flow')),
      hasRawCode: Boolean(strategy?.querySelector('pre')),
      rawCodeBlocks: document.querySelectorAll('pre, code').length
    };
  })()`);
  const checks = [result.graphCount === 5, result.traceCounts.every((value) => value > 0), result.navCount >= 6, result.detailsInteraction, result.template === 'interactive_research_v5', result.hasFlow, !result.hasRawCode, result.rawCodeBlocks === 0];
  if (!checks.every(Boolean)) throw new Error(`Energy-factor report contract failed: ${JSON.stringify(result)}`);
  process.stdout.write(`Strategy1 energy-factor UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
  client.close();
} finally {
  chrome.kill('SIGTERM'); await delay(100);
  rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
