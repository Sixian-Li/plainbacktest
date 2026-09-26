"""Streamlit entrypoint for the dual-SMA backtest laboratory."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import streamlit as st


LAB_DIR = Path(__file__).resolve().parent
BACKTEST_ROOT = LAB_DIR.parent
PROJECT_ROOT = BACKTEST_ROOT.parent
if str(BACKTEST_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKTEST_ROOT))

from dual_sma_lab.charts import GRID_METRICS, build_heatmap, build_single_figure  # noqa: E402
from dual_sma_lab.core import GridResult, GridSpec, run_grid, run_single  # noqa: E402
from dual_sma_lab.data_sources import (  # noqa: E402
    DatasetRef,
    discover_approved_datasets,
    load_workspace_dataset,
)


st.set_page_config(page_title="双均线策略实验台", page_icon="〽️", layout="wide")


@st.cache_data(show_spinner=False)
def dataset_catalog(root: str) -> dict[str, DatasetRef]:
    return discover_approved_datasets(Path(root))


@st.cache_data(show_spinner=False)
def cached_dataset(path: str, symbol: str, modified_ns: int) -> pd.DataFrame:
    del modified_ns
    return load_workspace_dataset(
        DatasetRef(symbol, Path(path), "approved", "workspace")
    )


@st.cache_data(show_spinner=False, max_entries=48)
def cached_single(
    data: pd.DataFrame,
    start: date,
    end: date,
    fast: int,
    slow: int,
    initial_cash: float,
    cost_bps: float,
):
    return run_single(
        data,
        start=start,
        end=end,
        fast_window=fast,
        slow_window=slow,
        initial_cash=initial_cash,
        cost_bps=cost_bps,
    )


@st.cache_data(show_spinner=False, max_entries=12)
def cached_grid(
    data: pd.DataFrame,
    start: date,
    end: date,
    spec: GridSpec,
    initial_cash: float,
    cost_bps: float,
) -> GridResult:
    return run_grid(
        data,
        start=start,
        end=end,
        spec=spec,
        initial_cash=initial_cash,
        cost_bps=cost_bps,
    )


def percent(value: object) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:,.2f}%"


def decimal(value: object, digits: int = 3) -> str:
    number = float(value)
    return "—" if not np.isfinite(number) else f"{number:,.{digits}f}"


def default_dates(symbol: str, minimum: date, maximum: date) -> tuple[date, date]:
    if symbol == "AAPL" and minimum <= date(2013, 1, 1) and maximum >= date(2022, 12, 30):
        return date(2013, 1, 1), date(2022, 12, 31)
    start = (pd.Timestamp(maximum) - pd.DateOffset(years=10) + pd.Timedelta(days=1)).date()
    return max(minimum, start), maximum


st.title("双均线策略实验台")
st.caption(
    "选择批准标的和日期，查看单组快慢线的完整账户指标，或生成自定义参数热力图。"
    "这里的结果是探索性计算，不自动登记为 validated 实验。"
)

catalog = dataset_catalog(str(PROJECT_ROOT))
symbols = list(catalog)
default_symbol_index = symbols.index("AAPL") if "AAPL" in symbols else 0
symbol = st.sidebar.selectbox(
    "标的",
    symbols,
    index=default_symbol_index,
    format_func=lambda value: catalog[value].label,
    help="只列出工作区已登记批准数据和质量门禁通过的 S&P 500 历史证券。",
)
dataset = catalog[symbol]
try:
    full_data = cached_dataset(
        str(dataset.path), dataset.symbol, dataset.path.stat().st_mtime_ns
    )
except Exception as error:
    st.error(f"数据读取失败：{error}")
    st.stop()

minimum_date = full_data["date"].iloc[0].date()
maximum_date = full_data["date"].iloc[-1].date()
selected_dates = st.sidebar.date_input(
    "回测日期（含首尾）",
    value=default_dates(symbol, minimum_date, maximum_date),
    min_value=minimum_date,
    max_value=maximum_date,
    key=f"dual_sma_dates_{symbol}",
)
if not isinstance(selected_dates, (tuple, list)) or len(selected_dates) != 2:
    st.warning("请选择完整的开始和结束日期。")
    st.stop()
start_date, end_date = selected_dates

st.sidebar.subheader("账户与成交")
initial_cash = float(
    st.sidebar.number_input("初始资金（美元）", 1_000.0, 100_000_000.0, 100_000.0, 10_000.0)
)
cost_bps = float(
    st.sidebar.number_input("单边成本（bps）", 0.0, 500.0, 5.0, 1.0)
)
price_scale = st.sidebar.radio("价格坐标", ("log", "linear"), horizontal=True)

st.sidebar.subheader("当前单组参数")
fast_window = int(st.sidebar.number_input("快线周期", 1, 500, 20, 1))
slow_window = int(st.sidebar.number_input("慢线周期", 2, 1000, 100, 1))

header_columns = st.columns(4)
header_columns[0].metric("标的", symbol)
header_columns[1].metric("数据状态", dataset.status)
header_columns[2].metric("数据范围", f"{minimum_date} → {maximum_date}")
header_columns[3].metric("数据行", f"{len(full_data):,}")

if maximum_date < pd.Timestamp.today().date() - pd.Timedelta(days=10):
    st.warning(
        f"批准数据截止 {maximum_date}，明显早于当前日期。页面只使用已批准历史，不会混入影子更新。"
    )

single_result = None
single_error = None
if fast_window >= slow_window:
    single_error = "快线必须严格短于慢线。"
else:
    try:
        with st.spinner("计算当前参数账本…"):
            single_result = cached_single(
                full_data,
                start_date,
                end_date,
                fast_window,
                slow_window,
                initial_cash,
                cost_bps,
            )
    except Exception as error:
        single_error = str(error)

single_tab, grid_tab, details_tab = st.tabs(("当前参数", "参数热力图", "口径与明细"))

with single_tab:
    if single_error:
        st.error(single_error)
    else:
        assert single_result is not None
        metrics = single_result.metrics
        benchmark = single_result.benchmark_metrics
        if (
            single_result.effective_start.date() != start_date
            or single_result.effective_end.date() != end_date
        ):
            st.info(
                "按真实交易日并完成均线预热后，实际账户从 "
                f"{single_result.effective_start.date()} 开始，结束于 {single_result.effective_end.date()}。"
            )
        first_row = st.columns(4)
        first_row[0].metric("日历 CAGR", percent(metrics["cagr_pct"]), percent(float(metrics["cagr_pct"]) - float(benchmark["cagr_pct"])))
        first_row[1].metric("日历 Sharpe", decimal(metrics["sharpe"]), decimal(float(metrics["sharpe"]) - float(benchmark["sharpe"])))
        first_row[2].metric("累计收益", percent(metrics["total_return_pct"]), percent(float(metrics["total_return_pct"]) - float(benchmark["total_return_pct"])))
        first_row[3].metric("最大回撤", percent(metrics["max_drawdown_pct"]), percent(float(metrics["max_drawdown_pct"]) - float(benchmark["max_drawdown_pct"])))
        second_row = st.columns(4)
        second_row[0].metric("持仓 CAGR", percent(metrics["holding_period_cagr_pct"]))
        second_row[1].metric("持仓 Sharpe", decimal(metrics["holding_return_sharpe"]))
        second_row[2].metric("持仓交易日", f"{int(metrics['holding_sessions']):,}")
        second_row[3].metric("持仓率", percent(metrics["holding_time_pct"]))
        st.plotly_chart(
            build_single_figure(single_result, price_scale=price_scale),
            width="stretch",
            theme=None,
            config={"scrollZoom": True, "displaylogo": False},
            key="dual_sma_single_chart",
        )
        comparison = pd.DataFrame(
            [
                {
                    "路径": "双均线策略",
                    "日历 CAGR": metrics["cagr_pct"],
                    "日历 Sharpe": metrics["sharpe"],
                    "持仓 CAGR": metrics["holding_period_cagr_pct"],
                    "持仓 Sharpe": metrics["holding_return_sharpe"],
                    "最大回撤": metrics["max_drawdown_pct"],
                    "持仓率": metrics["holding_time_pct"],
                    "订单数": metrics["order_count"],
                },
                {
                    "路径": f"{symbol} Buy & Hold",
                    "日历 CAGR": benchmark["cagr_pct"],
                    "日历 Sharpe": benchmark["sharpe"],
                    "持仓 CAGR": benchmark["holding_period_cagr_pct"],
                    "持仓 Sharpe": benchmark["holding_return_sharpe"],
                    "最大回撤": benchmark["max_drawdown_pct"],
                    "持仓率": benchmark["holding_time_pct"],
                    "订单数": benchmark["order_count"],
                },
            ]
        )
        st.dataframe(
            comparison.style.format(
                {
                    "日历 CAGR": "{:.2f}%",
                    "日历 Sharpe": "{:.3f}",
                    "持仓 CAGR": "{:.2f}%",
                    "持仓 Sharpe": "{:.3f}",
                    "最大回撤": "{:.2f}%",
                    "持仓率": "{:.2f}%",
                },
                na_rep="—",
            ),
            hide_index=True,
            width="stretch",
        )

with grid_tab:
    st.subheader("自定义快线 × 慢线参数面")
    st.caption(
        "日期、标的、资金和成本沿用左侧设置；只计算 fast < slow。默认范围就是快线 1–50、慢线 15–250、步长 1。"
    )
    with st.form("dual_sma_grid_form"):
        row = st.columns(6)
        grid_fast_start = int(row[0].number_input("快线起点", 1, 1000, 1, 1))
        grid_fast_end = int(row[1].number_input("快线终点", 1, 1000, 50, 1))
        grid_fast_step = int(row[2].number_input("快线步长", 1, 100, 1, 1))
        grid_slow_start = int(row[3].number_input("慢线起点", 1, 2000, 15, 1))
        grid_slow_end = int(row[4].number_input("慢线终点", 2, 2000, 250, 1))
        grid_slow_step = int(row[5].number_input("慢线步长", 1, 100, 1, 1))
        submitted = st.form_submit_button("生成 / 刷新热力图", width="stretch", type="primary")

    current_base_signature = (symbol, start_date.isoformat(), end_date.isoformat(), initial_cash, cost_bps)
    if submitted:
        try:
            grid_spec = GridSpec(
                grid_fast_start,
                grid_fast_end,
                grid_fast_step,
                grid_slow_start,
                grid_slow_end,
                grid_slow_step,
            )
            with st.spinner(f"计算 {len(grid_spec.pairs):,} 组参数…"):
                generated = cached_grid(
                    full_data,
                    start_date,
                    end_date,
                    grid_spec,
                    initial_cash,
                    cost_bps,
                )
            st.session_state["dual_sma_grid_result"] = generated
            st.session_state["dual_sma_grid_signature"] = current_base_signature
        except Exception as error:
            st.error(f"网格计算失败：{error}")

    grid_result = st.session_state.get("dual_sma_grid_result")
    grid_signature = st.session_state.get("dual_sma_grid_signature")
    if grid_result is None:
        st.info("设置范围后点击“生成 / 刷新热力图”。")
    elif grid_signature != current_base_signature:
        st.warning("标的、日期、资金或成本已经改变；请重新生成热力图。")
    else:
        metric_label = st.selectbox("热力图指标", list(GRID_METRICS), index=0)
        st.caption(
            f"实际共同窗口：{grid_result.effective_start.date()} 至 {grid_result.effective_end.date()}；"
            f"有效参数 {len(grid_result.metrics):,} 组。所有参数共用最大均线完成预热后的同一起点。"
        )
        st.plotly_chart(
            build_heatmap(grid_result, metric_label),
            width="stretch",
            theme=None,
            config={"scrollZoom": True, "displaylogo": False},
            key=f"dual_sma_heatmap_{metric_label}",
        )
        selected_column = GRID_METRICS[metric_label][0]
        leaders = grid_result.metrics.sort_values(
            [selected_column, "fast_window", "slow_window"],
            ascending=[False, True, True],
            na_position="last",
        ).head(10)
        st.markdown("#### 当前指标机械前十（仅样本内描述）")
        st.dataframe(
            leaders[
                [
                    "fast_window",
                    "slow_window",
                    "holding_period_cagr_pct",
                    "holding_return_sharpe",
                    "cagr_pct",
                    "sharpe",
                    "total_return_pct",
                    "max_drawdown_pct",
                    "holding_time_pct",
                    "order_count",
                ]
            ].style.format(precision=3, na_rep="—"),
            hide_index=True,
            width="stretch",
        )
        st.download_button(
            "下载完整网格 CSV",
            data=grid_result.metrics.to_csv(index=False),
            file_name=f"{symbol}_dual_sma_grid_{start_date}_{end_date}.csv",
            mime="text/csv",
            width="stretch",
        )
        st.warning(
            "热力图最高点是同一日期样本内的机械结果，不是推荐参数。优先观察宽平台、边界、持仓率、成本和跨时期稳定性。"
        )

with details_tab:
    st.markdown(
        """
#### 固定交易语义

- 使用工作区批准的调整日线；均线始终先用所选区间之前的历史预热。
- 每个完整收盘后，快线严格高于慢线则目标满仓；快线等于或低于慢线则目标空仓。
- 状态变化统一在下一正常交易日开盘成交；最后一个收盘信号没有下一根 K 线时不成交。
- 允许碎股，不加杠杆，现金不计息；买入价上调、卖出价下调所设单边成本。
- Buy & Hold 也在区间第一个收盘确认、第二个交易日开盘按同成本买入，确保起点一致。

#### 指标口径

- **日历 CAGR / Sharpe**：使用完整账户日收益，包括空仓的零收益交易日。
- **持仓 CAGR**：净账户期末增长按“开盘成交后仍持股”的交易日数，以 252 日几何年化；买入日计入、卖出日不计入。
- **持仓 Sharpe**：只保留真正受仓位影响的账户日收益；包含买入日至收盘、连续持有的收盘到收盘，以及卖出日前收盘至卖出开盘，因此卖出跳空和成本不会遗漏。
        """
    )
    if single_result is not None:
        left, middle, right = st.columns(3)
        left.download_button(
            "下载当前每日账本",
            single_result.daily.to_csv(index=False),
            f"{symbol}_dual_sma_daily.csv",
            "text/csv",
            width="stretch",
        )
        middle.download_button(
            "下载当前订单",
            single_result.orders.to_csv(index=False),
            f"{symbol}_dual_sma_orders.csv",
            "text/csv",
            width="stretch",
        )
        right.download_button(
            "下载当前已平仓交易",
            single_result.trades.to_csv(index=False),
            f"{symbol}_dual_sma_trades.csv",
            "text/csv",
            width="stretch",
        )
