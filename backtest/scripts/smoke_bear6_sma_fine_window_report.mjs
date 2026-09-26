#!/usr/bin/env node
// Exercise one target-specific SMA response dropdown in a real local Chrome instance.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2]);
const graphId = process.argv[3] || 'sma-window-azo';
const windowStart = Number(process.argv[4] || 190);
const windowEnd = Number(process.argv[5] || 270);
const windowStep = Number(process.argv[6] || 1);
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-bear6-sma-fine-chrome-`);
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
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const ready = await evaluate(client, `Boolean(
      document.readyState === 'complete' &&
      document.querySelectorAll('[id^="sma-window-"]').length === 6 &&
      document.getElementById(${JSON.stringify(graphId)})?._fullLayout?.updatemenus?.length
    )`);
    if (ready) break;
    if (attempt === 299) throw new Error(`SMA graph ${graphId} did not become ready`);
    await delay(100);
  }
  const result = await evaluate(client, `(async () => {
    const graph = document.getElementById(${JSON.stringify(graphId)});
    const responseGraphs = Array.from(document.querySelectorAll('[id^="sma-window-"]'));
    const visibleIndices = () => graph.data
      .map((trace, index) => ({trace, index}))
      .filter(({trace}) => trace.visible === true || trace.visible === undefined)
      .map(({index}) => index);
    const initialVisible = visibleIndices();
    const initialTitle = graph.layout.title?.text || '';
    const sma = graph.data[initialVisible[0]];
    const hold = graph.data[initialVisible[1]];
    const button = graph.layout.updatemenus[0].buttons[0];
    await Plotly.update(graph, button.args[0], button.args[1]);
    await new Promise((done) => setTimeout(done, 300));
    return {
      responseGraphCount: responseGraphs.length,
      traceCount: graph.data.length,
      initialVisible,
      updatedVisible: visibleIndices(),
      initialTitle,
      updatedTitle: graph.layout.title?.text || '',
      smaPoints: Array.from(sma.x),
      holdDash: hold.line?.dash,
      holdUniqueY: new Set(Array.from(hold.y).map(Number)).size,
      template: document.querySelector('meta[name="quant-report-template"]')?.content || ''
    };
  })()`);
  const expectedWindows = [];
  for (let value = windowStart; value <= windowEnd; value += windowStep) expectedWindows.push(value);
  const checks = [
    result.responseGraphCount === 6,
    result.traceCount === 32,
    JSON.stringify(result.initialVisible) === JSON.stringify([30, 31]),
    JSON.stringify(result.updatedVisible) === JSON.stringify([0, 1]),
    result.initialTitle.includes('不包含2000'),
    result.updatedTitle !== result.initialTitle,
    JSON.stringify(result.smaPoints) === JSON.stringify(expectedWindows),
    result.holdDash === 'dash',
    result.holdUniqueY === 1,
    result.template === 'interactive_research_v5'
  ];
  if (!checks.every(Boolean)) throw new Error(`SMA fine-window report contract failed: ${JSON.stringify(result)}`);
  process.stdout.write(`Bear6 SMA fine-window UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
  client.close();
} finally {
  chrome.kill('SIGTERM'); await delay(100); rmSync(profile, {recursive: true, force: true});
}
