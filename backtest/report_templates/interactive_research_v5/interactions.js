(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.QuantReportInteractions = api;
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', api.init);
    } else {
      window.setTimeout(api.init, 0);
    }
  }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function timestamp(value) {
    return new Date(value).getTime();
  }

  function visibleIndices(dates, range) {
    if (!range || range.length !== 2) return dates.map(function (_, index) { return index; });
    const start = timestamp(range[0]);
    const end = timestamp(range[1]);
    return dates.reduce(function (indices, date, index) {
      const current = timestamp(date);
      if (current >= start && current <= end) indices.push(index);
      return indices;
    }, []);
  }

  function evenlySpacedPositions(length, count) {
    if (!Number.isInteger(count) || count < 1) throw new Error('定投次数必须是正整数');
    if (count > length) throw new Error('定投次数不能超过区间内交易日数');
    if (count === 1) return [0];
    const positions = [];
    for (let index = 0; index < count; index += 1) {
      positions.push(Math.round(index * (length - 1) / (count - 1)));
    }
    return positions;
  }

  function drawdown(values) {
    let peak = -Infinity;
    return values.map(function (value) {
      peak = Math.max(peak, value);
      return peak > 0 ? (value / peak - 1) * 100 : 0;
    });
  }

  function calculateDca(dates, proxyValues, range, count, costBps) {
    if (dates.length !== proxyValues.length) throw new Error('日期与 Buy & Hold 数据长度不一致');
    const selected = visibleIndices(dates, range).filter(function (index) {
      return Number.isFinite(Number(proxyValues[index])) && Number(proxyValues[index]) > 0;
    });
    if (!selected.length) throw new Error('当前区间没有可用交易日');
    const schedulePositions = evenlySpacedPositions(selected.length, count);
    const schedule = new Set(schedulePositions);
    const budget = Number(proxyValues[selected[0]]);
    const tranche = budget / count;
    const costRate = Number(costBps) / 10000;
    let cash = budget;
    let units = 0;
    const outputDates = [];
    const equity = [];
    selected.forEach(function (sourceIndex, position) {
      const price = Number(proxyValues[sourceIndex]);
      if (schedule.has(position)) {
        units += tranche / (price * (1 + costRate));
        cash -= tranche;
        if (Math.abs(cash) < 1e-8) cash = 0;
      }
      outputDates.push(dates[sourceIndex]);
      equity.push(cash + units * price);
    });
    return {
      dates: outputDates,
      equity: equity,
      drawdown: drawdown(equity),
      budget: budget,
      firstDate: outputDates[0],
      lastDate: outputDates[outputDates.length - 1],
      scheduleCount: schedulePositions.length
    };
  }

  function rebaseValues(values, sourceAnchor, targetAnchor) {
    const source = Number(sourceAnchor);
    const target = Number(targetAnchor);
    if (!Number.isFinite(source) || source === 0 || !Number.isFinite(target)) {
      throw new Error('区间左端没有可用于对齐的净值');
    }
    const scale = target / source;
    return values.map(function (value) {
      const number = Number(value);
      return Number.isFinite(number) ? number * scale : null;
    });
  }

  function graphRange(graph) {
    const axes = graph._fullLayout || graph.layout || {};
    // In our two-row figures the visible range selector belongs to xaxis2;
    // xaxis is matched to it but can retain a stale full-history range after
    // relayout/restyle. Prefer the selector axis and fall back to xaxis.
    const axis = axes.xaxis2 || axes.xaxis;
    return axis && Array.isArray(axis.range) ? axis.range.slice() : null;
  }

  function seriesMeta(trace) {
    return trace && trace.meta && typeof trace.meta === 'object' ? trace.meta : {};
  }

  function seriesTraceIndices(graph, key) {
    const indices = [];
    graph.data.forEach(function (trace, index) {
      if (seriesMeta(trace).series_key === key) indices.push(index);
    });
    return indices;
  }

  function originalKey(trace) {
    const meta = seriesMeta(trace);
    return meta.series_key && meta.panel ? meta.series_key + '::' + meta.panel : '';
  }

  function firstVisiblePoint(trace, range) {
    const indices = visibleIndices(Array.from(trace.x), range);
    for (const index of indices) {
      const value = Number(trace.y[index]);
      if (Number.isFinite(value)) return {index: index, value: value, date: trace.x[index]};
    }
    return null;
  }

  function resetDrawdownFrom(trace, startIndex) {
    const output = Array.from(trace.y, function () { return null; });
    let peak = -Infinity;
    for (let index = startIndex; index < trace.y.length; index += 1) {
      const value = Number(trace.y[index]);
      if (!Number.isFinite(value)) continue;
      peak = Math.max(peak, value);
      output[index] = peak > 0 ? (value / peak - 1) * 100 : 0;
    }
    return output;
  }

  function paddedRange(values, includeZero) {
    const finite = values.map(Number).filter(Number.isFinite);
    if (!finite.length) return null;
    let low = Math.min.apply(null, finite);
    let high = Math.max.apply(null, finite);
    if (includeZero) {
      low = Math.min(low, 0);
      high = Math.max(high, 0);
    }
    const padding = Math.max((high - low) * 0.08, Math.max(Math.abs(low), Math.abs(high)) * 0.015, 0.002);
    return [low - padding, high + padding];
  }

  function marketPriceTrace(graph) {
    return graph.data.find(function (trace) { return trace.type === 'candlestick'; }) ||
      graph.data.find(function (trace) {
        return seriesMeta(trace).panel === 'price' && trace.x && trace.y;
      });
  }

  function visibleMarketPrices(trace, range) {
    if (!trace || !trace.x) return [];
    const selected = visibleIndices(Array.from(trace.x), range);
    if (trace.type === 'candlestick') {
      return selected.reduce(function (values, index) {
        const low = Number(trace.low[index]);
        const high = Number(trace.high[index]);
        if (Number.isFinite(low)) values.push(low);
        if (Number.isFinite(high)) values.push(high);
        return values;
      }, []);
    }
    return selected.map(function (index) { return Number(trace.y[index]); }).filter(Number.isFinite);
  }

  function initMarketControls() {
    document.querySelectorAll('.market-controls').forEach(function (controls) {
      if (controls.dataset.initialized === 'true') return;
      const graph = document.getElementById(controls.dataset.target);
      const priceTrace = marketPriceTrace(graph);
      const status = controls.querySelector('[data-role="status"]');
      let autoVisible = true;
      let updating = false;

      controls.querySelectorAll('input[data-series]').forEach(function (checkbox) {
        checkbox.addEventListener('change', function () {
          const indices = seriesTraceIndices(graph, checkbox.dataset.series);
          if (indices.length) {
            Plotly.restyle(graph, {visible: checkbox.checked ? true : 'legendonly'}, indices).then(function () {
              window.requestAnimationFrame(applyVisibleY);
            });
          }
        });
      });

      function applyVisibleY() {
        if (!autoVisible || updating || !priceTrace) return;
        const range = graphRange(graph);
        const priceRange = paddedRange(visibleMarketPrices(priceTrace, range), false);
        if (!priceRange) return;
        const derivativeValues = [];
        graph.data.forEach(function (trace) {
          if (trace.yaxis !== 'y2' || trace.visible === 'legendonly' || !trace.x || !trace.y) return;
          visibleIndices(Array.from(trace.x), range).forEach(function (index) {
            const value = Number(trace.y[index]);
            if (Number.isFinite(value)) derivativeValues.push(value);
          });
        });
        const derivativeRange = paddedRange(derivativeValues, true);
        const layout = {'yaxis.autorange': false, 'yaxis.range': priceRange};
        if (derivativeRange) {
          layout['yaxis2.autorange'] = false;
          layout['yaxis2.range'] = derivativeRange;
        }
        updating = true;
        Plotly.relayout(graph, layout).then(function () {
          updating = false;
          status.textContent = '价格与日变化纵轴均自动跟随可见区间';
        });
      }

      graph.on('plotly_relayout', function (event) {
        if (updating || !autoVisible) return;
        const changedX = Object.keys(event).some(function (key) {
          return key.indexOf('xaxis.range') === 0 || key.indexOf('xaxis2.range') === 0 || key === 'xaxis.autorange' || key === 'xaxis2.autorange';
        });
        if (changedX) window.requestAnimationFrame(applyVisibleY);
      });
      controls.querySelector('[data-action="visible-y"]').addEventListener('click', function () {
        autoVisible = true;
        applyVisibleY();
      });
      controls.querySelector('[data-action="manual-y"]').addEventListener('click', function () {
        autoVisible = false;
        status.textContent = '手动模式：可拖动纵轴，或输入上下限';
      });
      controls.querySelector('[data-action="apply-y"]').addEventListener('click', function () {
        const minimum = Number(controls.querySelector('[data-role="y-min"]').value);
        const maximum = Number(controls.querySelector('[data-role="y-max"]').value);
        if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || minimum >= maximum) {
          status.textContent = '上下限无效';
          return;
        }
        autoVisible = false;
        Plotly.relayout(graph, {'yaxis.autorange': false, 'yaxis.range': [minimum, maximum]});
        status.textContent = '已应用手动上下限';
      });
      controls.querySelector('[data-action="full-range"]').addEventListener('click', function () {
        autoVisible = true;
        updating = true;
        Plotly.relayout(graph, {'xaxis.autorange': true, 'xaxis2.autorange': true, 'yaxis.autorange': true, 'yaxis2.autorange': true}).then(function () {
          updating = false;
          applyVisibleY();
        });
      });
      controls.dataset.initialized = 'true';
      window.requestAnimationFrame(applyVisibleY);
    });
  }

  function initPerformanceControls() {
    document.querySelectorAll('.performance-controls').forEach(function (controls) {
      if (controls.dataset.initialized === 'true') return;
      const graph = document.getElementById(controls.dataset.target);
      const status = controls.querySelector('[data-role="status"]');
      const budgetLabel = controls.querySelector('[data-role="dca-budget"]');
      const benchmarkKey = controls.dataset.benchmarkKey;
      const originals = new Map();

      function rememberOriginals() {
        graph.data.forEach(function (trace) {
          const key = originalKey(trace);
          if (!key || originals.has(key)) return;
          originals.set(key, Array.from(trace.y));
        });
      }
      rememberOriginals();

      controls.querySelectorAll('input[data-series]').forEach(function (checkbox) {
        checkbox.addEventListener('change', function () {
          const indices = seriesTraceIndices(graph, checkbox.dataset.series);
          if (indices.length) Plotly.restyle(graph, {visible: checkbox.checked ? true : 'legendonly'}, indices);
        });
      });

      controls.querySelector('[data-action="rebase-visible"]').addEventListener('click', async function () {
        try {
          const range = graphRange(graph);
          const benchmark = graph.data.find(function (trace) {
            const meta = seriesMeta(trace);
            return meta.series_key === benchmarkKey && meta.panel === 'equity';
          });
          const benchmarkOriginal = originals.get(originalKey(benchmark));
          const benchmarkView = Object.assign({}, benchmark, {y: benchmarkOriginal});
          const anchor = firstVisiblePoint(benchmarkView, range);
          if (!anchor) throw new Error('当前区间没有 Buy & Hold 净值');

          const updates = [];
          graph.data.forEach(function (trace, traceIndex) {
            const meta = seriesMeta(trace);
            if (!meta.series_key || meta.panel !== 'equity') return;
            const raw = originals.get(originalKey(trace)) || Array.from(trace.y);
            const view = Object.assign({}, trace, {y: raw});
            const ownAnchor = firstVisiblePoint(view, range);
            if (!ownAnchor) return;
            const rebased = rebaseValues(raw, ownAnchor.value, anchor.value);
            updates.push({index: traceIndex, y: rebased});
            const drawdownIndex = graph.data.findIndex(function (candidate) {
              const candidateMeta = seriesMeta(candidate);
              return candidateMeta.series_key === meta.series_key && candidateMeta.panel === 'drawdown';
            });
            if (drawdownIndex >= 0) {
              const intervalDrawdown = resetDrawdownFrom({y: rebased}, ownAnchor.index);
              updates.push({index: drawdownIndex, y: intervalDrawdown});
            }
          });
          for (const update of updates) {
            await Plotly.restyle(graph, {y: [update.y]}, [update.index]);
          }
          if (range) {
            await Plotly.relayout(graph, {'xaxis.range': range, 'xaxis2.range': range});
          }
          status.textContent = '已从 ' + String(anchor.date).slice(0, 10) + ' 对齐至 Buy & Hold 的 $' + anchor.value.toLocaleString(undefined, {maximumFractionDigits: 2}) + '；回撤也从该日重新计算';
        } catch (error) {
          status.textContent = error.message;
        }
      });

      controls.querySelector('[data-action="restore-equity"]').addEventListener('click', function () {
        graph.data.forEach(function (trace, index) {
          const key = originalKey(trace);
          if (originals.has(key)) Plotly.restyle(graph, {y: [originals.get(key)]}, [index]);
        });
        status.textContent = '已恢复原始全历史净值与回撤';
      });

      controls.querySelector('[data-action="build-dca"]').addEventListener('click', async function () {
        try {
          const count = Number(controls.querySelector('[data-role="dca-count"]').value);
          const benchmark = graph.data.find(function (trace) {
            const meta = seriesMeta(trace);
            return meta.series_key === benchmarkKey && meta.panel === 'equity';
          });
          // Rebasing always uses Buy & Hold as the target, so this trace is never
          // scaled. Reading it directly also remains robust across Plotly redraws.
          const rawBenchmark = Array.from(benchmark.y);
          const range = graphRange(graph);
          const result = calculateDca(Array.from(benchmark.x), rawBenchmark, range, count, Number(controls.dataset.costBps));
          const oldIndices = seriesTraceIndices(graph, 'dca_visible_range').sort(function (a, b) { return b - a; });
          if (oldIndices.length) {
            await Plotly.deleteTraces(graph, oldIndices);
            originals.delete('dca_visible_range::equity');
            originals.delete('dca_visible_range::drawdown');
          }
          await Plotly.addTraces(graph, [
            {
              type: 'scatter', mode: 'lines', x: result.dates, y: result.equity,
              xaxis: 'x', yaxis: 'y', name: '区间等额定投（' + controls.dataset.costBps + 'bps）',
              line: {color: '#00a878', width: 2.4},
              meta: {series_key: 'dca_visible_range', panel: 'equity', label: '区间等额定投'}
            },
            {
              type: 'scatter', mode: 'lines', x: result.dates, y: result.drawdown,
              xaxis: 'x2', yaxis: 'y2', name: '区间等额定投（' + controls.dataset.costBps + 'bps）', showlegend: false,
              line: {color: '#00a878', width: 2.4},
              meta: {series_key: 'dca_visible_range', panel: 'drawdown', label: '区间等额定投'}
            }
          ]);
          rememberOriginals();
          const dcaCheckbox = controls.querySelector('input[data-series="dca_visible_range"]');
          dcaCheckbox.disabled = false;
          dcaCheckbox.checked = true;
          if (range) {
            await Plotly.relayout(graph, {'xaxis.range': range, 'xaxis2.range': range});
          }
          budgetLabel.textContent = '总预算：$' + result.budget.toLocaleString(undefined, {maximumFractionDigits: 2});
          status.textContent = result.firstDate.toString().slice(0, 10) + ' 至 ' + result.lastDate.toString().slice(0, 10) + '，已按 ' + result.scheduleCount + ' 笔等间隔投入';
        } catch (error) {
          status.textContent = error.message;
        }
      });
      controls.dataset.initialized = 'true';
    });
  }

  function init() {
    initMarketControls();
    initPerformanceControls();
  }

  return {
    calculateDca: calculateDca,
    drawdown: drawdown,
    evenlySpacedPositions: evenlySpacedPositions,
    graphRange: graphRange,
    init: init,
    marketPriceTrace: marketPriceTrace,
    paddedRange: paddedRange,
    rebaseValues: rebaseValues,
    visibleIndices: visibleIndices,
    visibleMarketPrices: visibleMarketPrices
  };
}));
