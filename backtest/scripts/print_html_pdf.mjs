#!/usr/bin/env node
// Print one local HTML report to a single PDF and verify its first-page wording.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const inputPath = resolve(process.argv[2]);
const outputPath = resolve(process.argv[3]);
const requestedWording = process.argv.slice(4);
const requiredWording = requestedWording.length ? requestedWording : ['我们测的是什么', '旧 8% 止损已完全删除', '措施 1', '措施 2', '四个版本与两个时期'];
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-pdf-chrome-`);
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

try {
  const port = await waitForPort();
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(pathToFileURL(inputPath).href)}`, {method: 'PUT'});
  const target = await response.json(); const client = new Client(target.webSocketDebuggerUrl); await client.ready();
  await client.command('Page.enable'); await client.command('Runtime.enable');
  for (let i = 0; i < 100; i += 1) { const state = await client.command('Runtime.evaluate', {expression: 'document.readyState', returnByValue: true}); if (state.result.value === 'complete') break; await delay(100); }
  for (let i = 0; i < 200; i += 1) {
    const rendered = await client.command('Runtime.evaluate', {expression: 'document.querySelectorAll(".plotly-graph-div").length === 0 || document.querySelectorAll(".plotly-graph-div .main-svg").length >= document.querySelectorAll(".plotly-graph-div").length', returnByValue: true});
    if (rendered.result.value) break;
    await delay(100);
  }
  const text = await client.command('Runtime.evaluate', {expression: '(document.querySelector(".page") || document.body)?.innerText || ""', returnByValue: true});
  for (const required of requiredWording) {
    if (!text.result.value.includes(required)) throw new Error(`First page is missing: ${required}`);
  }
  await client.command('Emulation.setEmulatedMedia', {media: 'print'});
  const strategyContract = await client.command('Runtime.evaluate', {
    expression: `(() => {
      const template = document.querySelector('meta[name="quant-report-template"]')?.content || '';
      const strategy = document.getElementById('strategy-definition');
      const parameters = strategy?.querySelector('.strategy-parameters');
      return {
        template,
        hasFlow: Boolean(strategy?.querySelector('.strategy-flow')),
        hasRawCode: Boolean(strategy?.querySelector('pre')),
        parameterDisplay: parameters ? getComputedStyle(parameters).display : null
      };
    })()`,
    returnByValue: true
  });
  const contract = strategyContract.result.value;
  if (contract.template === 'interactive_research_v5' && (
    !contract.hasFlow || contract.hasRawCode || contract.parameterDisplay !== 'none'
  )) {
    throw new Error(`V5 printable strategy contract failed: ${JSON.stringify(contract)}`);
  }
  const printed = await client.command('Page.printToPDF', {landscape: true, printBackground: true, preferCSSPageSize: true, displayHeaderFooter: false});
  const bytes = Buffer.from(printed.data, 'base64'); if (bytes.subarray(0, 5).toString() !== '%PDF-') throw new Error('Chrome output is not a PDF');
  writeFileSync(outputPath, bytes); client.close();
  process.stdout.write(`PDF print: PASS\n${JSON.stringify({outputPath, bytes: bytes.length, firstPageRulesVerified: true, strategyContract: contract}, null, 2)}\n`);
} finally {
  chrome.kill('SIGTERM'); await delay(100); rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
