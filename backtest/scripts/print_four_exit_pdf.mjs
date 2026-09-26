#!/usr/bin/env node
// Print the four-exit rules-first report to one PDF.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const inputPath = resolve(process.argv[2]);
const outputPath = resolve(process.argv[3]);
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-four-exit-pdf-`);
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
      const pending = this.pending.get(message.id); this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message)); else pending.resolve(message.result);
    };
  }
  async ready() { if (this.socket.readyState === WebSocket.OPEN) return; await new Promise((resolveSocket, rejectSocket) => { this.socket.onopen = resolveSocket; this.socket.onerror = rejectSocket; }); }
  command(method, params = {}) { const id = this.id++; return new Promise((resolveCommand, rejectCommand) => { this.pending.set(id, {resolve: resolveCommand, reject: rejectCommand}); this.socket.send(JSON.stringify({id, method, params})); }); }
  close() { this.socket.close(); }
}

try {
  const port = await waitForPort();
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(pathToFileURL(inputPath).href)}`, {method: 'PUT'});
  const target = await response.json(); const client = new Client(target.webSocketDebuggerUrl); await client.ready();
  await client.command('Page.enable'); await client.command('Runtime.enable');
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const state = await client.command('Runtime.evaluate', {expression: 'document.readyState', returnByValue: true});
    if (state.result.value === 'complete') break; await delay(100);
  }
  const text = await client.command('Runtime.evaluate', {expression: 'document.querySelector(".page")?.innerText || ""', returnByValue: true});
  for (const required of ['我们测的是什么', '买入路径A', '买入路径B', '四个卖出开关', '2⁴=16']) {
    if (!text.result.value.includes(required)) throw new Error(`First page is missing: ${required}`);
  }
  const printed = await client.command('Page.printToPDF', {landscape: true, printBackground: true, preferCSSPageSize: true, displayHeaderFooter: false});
  const bytes = Buffer.from(printed.data, 'base64');
  if (bytes.subarray(0, 5).toString() !== '%PDF-') throw new Error('Chrome output is not a PDF');
  writeFileSync(outputPath, bytes); client.close();
  process.stdout.write(`PDF print: PASS\n${JSON.stringify({outputPath, bytes: bytes.length, firstPageRulesVerified: true}, null, 2)}\n`);
} finally {
  chrome.kill('SIGTERM'); await delay(100); rmSync(profile, {recursive: true, force: true});
}
