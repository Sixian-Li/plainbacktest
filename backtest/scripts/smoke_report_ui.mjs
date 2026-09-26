#!/usr/bin/env node
// Exercise the reusable report controls in a real local Chrome instance.

import {spawn} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const reportPath = resolve(process.argv[2] || 'experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid/report.html');
const graphId = process.argv[3] || 'performance-qqq';
const marketGraphId = graphId.replace('performance-', 'market-');
const chromePath = process.env.QUANT_CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = mkdtempSync(`${tmpdir()}/quant-report-chrome-`);
const chrome = spawn(chromePath, [
  '--headless=new',
  '--disable-gpu',
  '--allow-file-access-from-files',
  '--remote-debugging-port=0',
  `--user-data-dir=${profile}`,
  'about:blank'
], {stdio: 'ignore'});

function delay(ms) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

async function waitForPort() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const [port] = readFileSync(`${profile}/DevToolsActivePort`, 'utf8').trim().split('\n');
      return Number(port);
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
      const {resolveCommand, rejectCommand} = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) rejectCommand(new Error(message.error.message));
      else resolveCommand(message.result);
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
    const id = this.nextId;
    this.nextId += 1;
    return new Promise((resolveCommand, rejectCommand) => {
      this.pending.set(id, {resolveCommand, rejectCommand});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  close() {
    this.socket.close();
  }
}

async function evaluate(client, expression) {
  const result = await client.command('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true
  });
  if (result.exceptionDetails) {
    const description = result.exceptionDetails.exception?.description || result.exceptionDetails.text;
    throw new Error(description);
  }
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
    const ready = await evaluate(client, `Boolean(
      document.getElementById(${JSON.stringify(graphId)})?.data?.length &&
      document.getElementById(${JSON.stringify(graphId)})?._fullLayout?.xaxis &&
      document.querySelector('.performance-controls[data-target="${graphId}"]')?.dataset?.initialized === 'true' &&
      (!document.getElementById(${JSON.stringify(marketGraphId)}) || (
        document.getElementById(${JSON.stringify(marketGraphId)})?.data?.length &&
        document.getElementById(${JSON.stringify(marketGraphId)})?._fullLayout?.xaxis &&
        document.querySelector('.market-controls[data-target="${marketGraphId}"]')?.dataset?.initialized === 'true'
      ))
    )`);
    if (ready) break;
    if (attempt === 159) throw new Error(`Plotly graph ${graphId} did not become ready`);
    await delay(100);
  }

  const result = await evaluate(client, `(async function () {
    const graph = document.getElementById(${JSON.stringify(graphId)});
    const controls = document.querySelector('.performance-controls[data-target="${graphId}"]');
    const equity = graph.data.filter((trace) => trace.meta?.panel === 'equity');
    const benchmark = equity.find((trace) => trace.meta?.is_benchmark === true);
    if (!benchmark) throw new Error('Performance graph has no benchmark equity trace');
    const benchmarkDates = Array.from(benchmark.x);
    const startIndex = benchmarkDates.length > 10 ? Math.floor(benchmarkDates.length * 0.2) : 0;
    const endIndex = benchmarkDates.length > 10 ? Math.ceil(benchmarkDates.length * 0.8) : benchmarkDates.length - 1;
    const rangeStart = benchmarkDates[startIndex];
    const rangeEnd = benchmarkDates[endIndex];
    const performanceRangeUpdate = {'xaxis.range': [rangeStart, rangeEnd]};
    if (graph._fullLayout.xaxis2) performanceRangeUpdate['xaxis2.range'] = [rangeStart, rangeEnd];
    await Plotly.relayout(graph, performanceRangeUpdate);
    controls.querySelector('[data-action="rebase-visible"]').click();
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
    const rebaseStatus = controls.querySelector('[data-role="status"]').textContent;
    const firstValue = (trace) => {
      const index = Array.from(trace.x).findIndex((date) => new Date(date) >= new Date(rangeStart));
      return Number(trace.y[index]);
    };
    const updatedEquity = graph.data.filter((trace) => trace.meta?.panel === 'equity');
    const updatedBenchmark = updatedEquity.find((trace) => trace.meta?.is_benchmark === true);
    const leftValues = updatedEquity
      .filter((trace) => trace.meta.series_key !== 'dca_visible_range')
      .map((trace) => ({key: trace.meta.series_key, value: firstValue(trace)}));
    const aligned = updatedEquity
      .filter((trace) => trace.meta.series_key !== 'dca_visible_range')
      .every((trace) => Math.abs(firstValue(trace) - firstValue(updatedBenchmark)) < 1e-6);

    const visibleCount = benchmarkDates.filter((date) => new Date(date) >= new Date(rangeStart) && new Date(date) <= new Date(rangeEnd)).length;
    const dcaCount = Math.min(50, visibleCount);
    controls.querySelector('[data-role="dca-count"]').value = String(dcaCount);
    controls.querySelector('[data-action="build-dca"]').click();
    await new Promise((resolveWait) => setTimeout(resolveWait, 700));
    const dcaTraces = graph.data.filter((trace) => trace.meta?.series_key === 'dca_visible_range');
    const dcaStatus = controls.querySelector('[data-role="status"]').textContent;

    const nonBenchmark = equity.find((trace) => !trace.meta?.is_benchmark && trace.meta?.series_key !== 'dca_visible_range');
    const strategyCheckbox = controls.querySelector('input[data-series="' + nonBenchmark.meta.series_key + '"]');
    strategyCheckbox.checked = false;
    strategyCheckbox.dispatchEvent(new Event('change'));
    await new Promise((resolveWait) => setTimeout(resolveWait, 300));
    const strategyHidden = graph.data
      .filter((trace) => trace.meta?.series_key === nonBenchmark.meta.series_key)
      .every((trace) => trace.visible === 'legendonly');

    const marketGraph = document.getElementById(${JSON.stringify(marketGraphId)});
    let overlayHidden = true;
    let marketAutoYValid = true;
    let marketYRange = null;
    let derivativeAutoYValid = true;
    let derivativeYRange = null;
    if (marketGraph) {
      const marketControls = document.querySelector('.market-controls[data-target="${marketGraphId}"]');
      const overlayCheckbox = marketControls.querySelector('input[data-series]');
      const overlayKey = overlayCheckbox.dataset.series;
      overlayCheckbox.checked = false;
      overlayCheckbox.dispatchEvent(new Event('change'));
      await new Promise((resolveWait) => setTimeout(resolveWait, 250));
      overlayHidden = marketGraph.data
        .filter((trace) => trace.meta?.series_key === overlayKey)
        .every((trace) => trace.visible === 'legendonly');
      overlayCheckbox.checked = true;
      overlayCheckbox.dispatchEvent(new Event('change'));
      const candle = marketGraph.data.find((trace) => trace.type === 'candlestick');
      const priceTrace = candle || marketGraph.data.find(
        (trace) => trace.meta?.panel === 'price' && trace.x && trace.y
      );
      if (!priceTrace) throw new Error('Market graph has no candlestick or price trace');
      const priceDates = Array.from(priceTrace.x);
      const marketStart = priceDates[Math.floor(priceDates.length * 0.2)];
      const marketEnd = priceDates[Math.max(Math.floor(priceDates.length * 0.6), 1)];
      const marketRangeUpdate = {'xaxis.range': [marketStart, marketEnd]};
      if (marketGraph._fullLayout.xaxis2) marketRangeUpdate['xaxis2.range'] = [marketStart, marketEnd];
      await Plotly.relayout(marketGraph, marketRangeUpdate);
      await new Promise((resolveWait) => setTimeout(resolveWait, 700));
      const selectedMarketIndices = priceDates
        .map((date, index) => ({date: new Date(date), index}))
        .filter((item) => item.date >= new Date(marketStart) && item.date <= new Date(marketEnd))
        .map((item) => item.index);
      const visiblePrices = candle
        ? selectedMarketIndices.flatMap((index) => [Number(candle.low[index]), Number(candle.high[index])])
        : selectedMarketIndices.map((index) => Number(priceTrace.y[index]));
      const visibleLow = Math.min(...visiblePrices);
      const visibleHigh = Math.max(...visiblePrices);
      marketYRange = marketGraph._fullLayout.yaxis.range.map(Number);
      marketAutoYValid = marketYRange[0] < visibleLow && marketYRange[1] > visibleHigh && marketYRange[1] - marketYRange[0] < visibleHigh * 2;
      const derivativeTraces = marketGraph.data.filter((trace) => trace.yaxis === 'y2' && trace.visible !== 'legendonly' && trace.x && trace.y);
      const derivativeValues = [];
      derivativeTraces.forEach((trace) => {
        Array.from(trace.x).forEach((date, index) => {
          const value = Number(trace.y[index]);
          if (new Date(date) >= new Date(marketStart) && new Date(date) <= new Date(marketEnd) && Number.isFinite(value)) derivativeValues.push(value);
        });
      });
      derivativeYRange = derivativeTraces.length ? marketGraph._fullLayout.yaxis2.range.map(Number) : null;
      const derivativeLow = derivativeValues.length ? Math.min(...derivativeValues, 0) : null;
      const derivativeHigh = derivativeValues.length ? Math.max(...derivativeValues, 0) : null;
      derivativeAutoYValid = !derivativeTraces.length || (
        derivativeYRange && derivativeYRange[0] < derivativeLow && derivativeYRange[1] > derivativeHigh &&
        derivativeYRange[1] - derivativeYRange[0] < Math.max((derivativeHigh - derivativeLow) * 1.5, 0.02)
      );
    }

    return {
      aligned,
      rebaseStatus,
      leftValues,
      dcaTraceCount: dcaTraces.length,
      dcaPoints: dcaTraces[0]?.x?.length || 0,
      dcaCount,
      visibleCount,
      dcaStatus,
      strategyHidden,
      overlayHidden,
      marketAutoYValid,
      marketYRange,
      derivativeAutoYValid,
      derivativeYRange,
      benchmarkShape: {
        xLength: benchmark?.x?.length,
        yLength: benchmark?.y?.length,
        firstX: benchmark?.x?.[0],
        lastX: benchmark?.x?.[benchmark?.x?.length - 1],
        firstY: benchmark?.y?.[0]
      },
      template: document.querySelector('meta[name="quant-report-template"]')?.content
    };
  }())`);
  client.close();
  if (!result.aligned || !result.rebaseStatus.startsWith('已从 ') || result.dcaTraceCount !== 2 || !result.dcaStatus.includes(`${result.dcaCount} 笔`) || result.dcaPoints !== result.visibleCount || !result.strategyHidden || !result.overlayHidden || !result.marketAutoYValid || !result.derivativeAutoYValid) {
    throw new Error(`Report interaction smoke test failed: ${JSON.stringify(result)}`);
  }
  process.stdout.write(`Report UI smoke test: PASS\n${JSON.stringify(result, null, 2)}\n`);
}

try {
  await main();
} finally {
  chrome.kill('SIGTERM');
  await delay(100);
  rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});
}
