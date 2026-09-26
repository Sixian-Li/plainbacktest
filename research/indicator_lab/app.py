"""Streamlit entrypoint for the Quant indicator laboratory."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys

import pandas as pd
import streamlit as st


LAB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = LAB_DIR.parents[1]
if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))

from charts import build_indicator_figure  # noqa: E402
from core import (  # noqa: E402
    DEFAULT_REGISTRY,
    LabConfig,
    MA_METHODS,
    MovingAverageSpec,
    StochRsiSpec,
    parse_moving_average_rows,
)
from data_sources import (  # noqa: E402
    DatasetRef,
    discover_approved_datasets,
    load_uploaded_dataset,
    load_workspace_dataset,
)


st.set_page_config(page_title="Quant 指标实验台", page_icon="📈", layout="wide")

DEFAULT_CONFIG = LabConfig(
    moving_averages=(
        MovingAverageSpec("SMA 50", "SMA", 50),
        MovingAverageSpec("SMA 100", "SMA", 100),
        MovingAverageSpec("SMA 200", "SMA", 200),
    ),
    stochrsi=StochRsiSpec(),
)


def moving_average_editor_frame(config: LabConfig) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "enabled": True,
                "label": spec.label,
                "method": spec.method,
                "period": spec.period,
                "smooth_method": spec.smooth_method or "None",
                "smooth_period": spec.smooth_period or 1,
            }
            for spec in config.moving_averages
        ]
    )


@st.cache_data(show_spinner=False)
def dataset_catalog(root: str) -> dict[str, DatasetRef]:
    return discover_approved_datasets(Path(root))


@st.cache_data(show_spinner=False)
def cached_workspace_data(path: str, symbol: str, modified_ns: int) -> pd.DataFrame:
    del modified_ns
    return load_workspace_dataset(
        DatasetRef(
            symbol=symbol,
            path=Path(path),
            status="approved",
            source_group="workspace",
        )
    )


def load_selected_data() -> tuple[pd.DataFrame, str, str]:
    mode = st.sidebar.radio(
        "数据源",
        ("工作区批准数据", "上传 canonical CSV"),
        help="工作区列表只纳入 approved 数据；上传文件仅用于观察，不自动获得批准状态。",
        key="data_source_mode",
    )
    if mode == "工作区批准数据":
        catalog = dataset_catalog(str(PROJECT_ROOT))
        symbols = list(catalog)
        default_index = symbols.index("AAPL") if "AAPL" in symbols else 0
        symbol = st.sidebar.selectbox(
            "标的", symbols, index=default_index, key="workspace_symbol"
        )
        dataset = catalog[symbol]
        data = cached_workspace_data(
            str(dataset.path), dataset.symbol, dataset.path.stat().st_mtime_ns
        )
        return data, dataset.symbol, dataset.status

    upload = st.sidebar.file_uploader("上传 CSV", type=("csv",))
    upload_symbol = st.sidebar.text_input(
        "标的代码（文件没有 Symbol 列时必填）", value=""
    )
    if upload is None:
        st.info("上传包含 Date/Close，最好同时包含 Symbol/Open/High/Low/Volume 的 CSV。")
        st.stop()
    data = load_uploaded_dataset(BytesIO(upload.getvalue()), symbol=upload_symbol)
    symbol = str(data["symbol"].iloc[0])
    return data, symbol, "uploaded_unapproved"


st.title("Quant 动态指标与策略实验台")
st.caption(
    "更换数据源不会改变指标定义。当前页面只做指标观察，不产生订单、交易账本或正式策略收益。"
)

try:
    full_data, symbol, data_status = load_selected_data()
except Exception as error:
    st.error(f"数据读取失败：{error}")
    st.stop()

with st.sidebar.expander("指标配置预设", expanded=False):
    preset_upload = st.file_uploader(
        "载入此前下载的 JSON", type=("json",), key="indicator_preset_upload"
    )
    if st.button("应用预设", disabled=preset_upload is None, width="stretch"):
        try:
            loaded = LabConfig.from_json(preset_upload.getvalue().decode("utf-8"))
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
            st.error(f"预设无效：{error}")
        else:
            st.session_state["loaded_lab_config"] = loaded
            st.session_state["preset_generation"] = (
                int(st.session_state.get("preset_generation", 0)) + 1
            )
            st.rerun()

initial_config = st.session_state.get("loaded_lab_config", DEFAULT_CONFIG)
widget_suffix = str(st.session_state.get("preset_generation", 0))

minimum_date = full_data["date"].iloc[0].date()
maximum_date = full_data["date"].iloc[-1].date()
selected_dates = st.sidebar.date_input(
    "显示区间",
    value=(minimum_date, maximum_date),
    min_value=minimum_date,
    max_value=maximum_date,
)
if not isinstance(selected_dates, (tuple, list)) or len(selected_dates) != 2:
    st.warning("请选择完整的起止日期。")
    st.stop()
display_start, display_end = selected_dates

st.sidebar.subheader("价格图")
price_style = st.sidebar.radio(
    "价格形式", ("Close", "Candlestick"), horizontal=True
)
price_scale = st.sidebar.radio(
    "价格坐标",
    ("log", "linear"),
    index=0 if initial_config.price_scale == "log" else 1,
    horizontal=True,
    key=f"price_scale_{widget_suffix}",
)

st.sidebar.subheader("StochRSI")
rsi_period = st.sidebar.number_input(
    "RSI 周期",
    2,
    500,
    initial_config.stochrsi.rsi_period,
    1,
    key=f"rsi_period_{widget_suffix}",
)
stoch_period = st.sidebar.number_input(
    "Stoch 区间周期",
    2,
    500,
    initial_config.stochrsi.stoch_period,
    1,
    key=f"stoch_period_{widget_suffix}",
)
k_method = st.sidebar.selectbox(
    "K 第一次平滑方法",
    MA_METHODS,
    index=MA_METHODS.index(initial_config.stochrsi.k_method),
    key=f"k_method_{widget_suffix}",
)
k_period = st.sidebar.number_input(
    "K 第一次平滑周期",
    1,
    200,
    initial_config.stochrsi.k_period,
    1,
    key=f"k_period_{widget_suffix}",
)
d_method = st.sidebar.selectbox(
    "D 第二次平滑方法",
    MA_METHODS,
    index=MA_METHODS.index(initial_config.stochrsi.d_method),
    key=f"d_method_{widget_suffix}",
)
d_period = st.sidebar.number_input(
    "D 第二次平滑周期",
    1,
    200,
    initial_config.stochrsi.d_period,
    1,
    key=f"d_period_{widget_suffix}",
)
thresholds = st.sidebar.slider(
    "下沿 / 上沿",
    0.0,
    1.0,
    (initial_config.lower_threshold, initial_config.upper_threshold),
    0.01,
    key=f"thresholds_{widget_suffix}",
)
show_raw = st.sidebar.checkbox("显示 Raw StochRSI", True)
show_k = st.sidebar.checkbox("显示 K", True)
show_d = st.sidebar.checkbox("显示 D", True)
show_rsi = st.sidebar.checkbox("显示 RSI（归一化）", False)

with st.expander("均线与二次平滑配置", expanded=True):
    st.caption(
        "可增加或删除行。第二次平滑选 None 表示只画第一层均线；例如 SMA 100 再用 EMA 10 平滑。"
    )
    edited_ma = st.data_editor(
        moving_average_editor_frame(initial_config),
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "enabled": st.column_config.CheckboxColumn("启用"),
            "label": st.column_config.TextColumn("图例名称"),
            "method": st.column_config.SelectboxColumn(
                "均线方法", options=list(MA_METHODS), required=True
            ),
            "period": st.column_config.NumberColumn(
                "周期", min_value=1, max_value=1000, step=1, required=True
            ),
            "smooth_method": st.column_config.SelectboxColumn(
                "第二次平滑", options=["None", *MA_METHODS], required=True
            ),
            "smooth_period": st.column_config.NumberColumn(
                "二次周期", min_value=1, max_value=500, step=1, required=True
            ),
        },
        key=f"moving_average_editor_{widget_suffix}",
    )

try:
    ma_specs = parse_moving_average_rows(edited_ma.to_dict("records"))
    stoch_spec = StochRsiSpec(
        rsi_period=int(rsi_period),
        stoch_period=int(stoch_period),
        k_period=int(k_period),
        d_period=int(d_period),
        k_method=k_method,
        d_method=d_method,
    )
    config = LabConfig(
        moving_averages=ma_specs,
        stochrsi=stoch_spec,
        lower_threshold=float(thresholds[0]),
        upper_threshold=float(thresholds[1]),
        price_scale=price_scale,
    )
except (TypeError, ValueError) as error:
    st.error(f"参数无效：{error}")
    st.stop()

with st.spinner("重新计算指标…"):
    ma_result = DEFAULT_REGISTRY.run("moving_averages", full_data, ma_specs)
    stoch_result = DEFAULT_REGISTRY.run("stochrsi", full_data, stoch_spec)

mask = full_data["date"].between(
    pd.Timestamp(display_start), pd.Timestamp(display_end), inclusive="both"
)
display_data = full_data.loc[mask].reset_index(drop=True)
if display_data.empty:
    st.warning("当前显示区间没有交易日。")
    st.stop()
selected_index = full_data.index[mask]
price_lines = {
    label: values.loc[selected_index].reset_index(drop=True)
    for label, values in ma_result.price_lines.items()
}
oscillator_choices = {
    "Raw StochRSI": show_raw,
    "K": show_k,
    "D": show_d,
    "RSI": show_rsi,
}
oscillator_lines = {
    label: values.loc[selected_index].reset_index(drop=True)
    for label, values in stoch_result.oscillator_lines.items()
    if oscillator_choices[label]
}

metric_columns = st.columns(4)
metric_columns[0].metric("标的", symbol)
metric_columns[1].metric("批准状态", data_status)
metric_columns[2].metric("全部交易日", f"{len(full_data):,}")
metric_columns[3].metric("显示交易日", f"{len(display_data):,}")

figure = build_indicator_figure(
    display_data,
    symbol=symbol,
    price_lines=price_lines,
    oscillator_lines=oscillator_lines,
    lower_threshold=config.lower_threshold,
    upper_threshold=config.upper_threshold,
    price_scale=config.price_scale,
    price_style=price_style,
)
st.plotly_chart(
    figure,
    width="stretch",
    theme=None,
    config={"scrollZoom": True, "displaylogo": False},
    key="indicator_chart",
)

left, right = st.columns((2, 1))
with left:
    st.info(
        "指标始终在全部历史上先计算，再裁切显示区间，因此缩短日期不会破坏预热。"
        "参数变化只重算观察图；尚未执行任何买卖或账户收益计算。"
    )
with right:
    st.download_button(
        "下载当前指标配置 JSON",
        data=config.to_json(),
        file_name="indicator_lab_config.json",
        mime="application/json",
        width="stretch",
    )

with st.expander("当前配置与插件接口"):
    st.code(config.to_json(), language="json")
    st.write(
        "已注册模块："
        + "、".join(DEFAULT_REGISTRY.ids())
        + "。以后策略模块可通过同一注册表返回价格线、振荡线和信号标记；正式收益仍须进入回测流水线。"
    )
