import pandas as pd
import requests
import streamlit as st
import plotly.express as px

st.set_page_config(page_title="Signal Visualizer", layout="wide")
st.title("📊 Визуализация сигналов")

# --------------------
# Чтение query params и загрузка сессии
# --------------------
query_params = st.query_params
session_token = query_params.get("session", None)
api_url = query_params.get("api_url", "http://localhost:8000")

signal_codes = query_params.get("signals", [])
if isinstance(signal_codes, str):
    signal_codes = [signal_codes]

CODE = ""

if session_token:
    try:
        resp = requests.get(f"{api_url}/api/visualize/session/{session_token}")
        resp.raise_for_status()
        payload = resp.json()
        signal_codes = payload.get("signals", signal_codes)
        CODE = payload.get("code", CODE)
    except Exception as e:
        st.error(f"Не удалось получить данные сессии: {e}")

# --------------------
# Состояние
# --------------------
if "signals_data" not in st.session_state:
    st.session_state.signals_data = None  # base df (только исходные сигналы)
if "selected_signals" not in st.session_state:
    st.session_state.selected_signals = set()
if "plot_areas" not in st.session_state:
    st.session_state.plot_areas = []
if "derived_signals" not in st.session_state:
    # виртуальные сигналы: name -> df (Series в DataFrame с индексом datetime)
    st.session_state.derived_signals = {}  # { "SIG@cut1": DataFrame({name: series}) }

def compute_stats_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Статистика только по числовым данным. Нечисловое -> NaN."""
    if df is None or df.empty:
        return pd.DataFrame()

    num = df.apply(pd.to_numeric, errors="coerce")

    out = pd.DataFrame(index=num.columns)
    out["count"] = num.count()
    out["min"] = num.min()
    out["max"] = num.max()
    out["mean"] = num.mean()
    out["std"] = num.std()
    out["median"] = num.median()

    # диапазон времени по НЕ NaN
    starts, ends = [], []
    for col in num.columns:
        s = num[col].dropna()
        if s.empty:
            starts.append(pd.NaT); ends.append(pd.NaT)
        else:
            starts.append(s.index.min()); ends.append(s.index.max())
    out["start"] = starts
    out["end"] = ends

    return out




# --------------------
# Загрузка данных сигналов (исходных) с backend
# --------------------
def load_signals(signal_codes):
    if not signal_codes:
        st.info("Список сигналов пуст — ничего загружать.")
        return None, [], []
    try:
        response = requests.post(
            f"{api_url}/api/signal-data",
            json={"signal_names": signal_codes, "format": "json"}
        )
        response.raise_for_status()
        result = response.json()
        found = result.get("found", [])
        not_found = result.get("not_found", [])
        data_dict = result.get("data", {})

        if not data_dict:
            st.warning("Нет данных по запрошенным сигналам.")
            return None, found, not_found

        dfs = []
        for sig, records in data_dict.items():
            if not records:
                continue
            df = pd.DataFrame(records)
            if "datetime" not in df.columns or "value" not in df.columns:
                continue
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"])
            df = df.set_index("datetime").sort_index()
            df = df.rename(columns={"value": sig})
            dfs.append(df[[sig]])

        if not dfs:
            return None, found, not_found
        return pd.concat(dfs, axis=1).sort_index(), found, not_found

    except Exception as e:
        st.error(f"❌ Ошибка загрузки данных: {e}")
        return None, [], []

if signal_codes and st.session_state.signals_data is None:
    with st.spinner("Загружаем данные сигналов..."):
        df_all, found, not_found = load_signals(signal_codes)
        st.session_state.signals_data = df_all
        st.success(f"✅ Загружено сигналов: {len(found)}")
        if not_found:
            st.warning(f"⚠️ Не найдены: {', '.join(not_found)}")

# --------------------
# Утилиты для работы со "всеми" сигналами (base + derived)
# --------------------
def get_all_signals_df():
    """Объединяет исходные сигналы и виртуальные (обрезанные) в один DataFrame по времени."""
    base = st.session_state.signals_data
    derived = st.session_state.derived_signals

    if base is None and not derived:
        return None

    dfs = []
    if base is not None:
        dfs.append(base)

    # derived хранится как отдельные df с единственной колонкой
    for _, ddf in derived.items():
        dfs.append(ddf)

    if not dfs:
        return None

    # outer join по времени: разные сигналы могут иметь разные точки
    df_all = pd.concat(dfs, axis=1).sort_index()
    return df_all


def compute_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Основные статистики по колонкам DataFrame."""
    if df is None or df.empty:
        return pd.DataFrame()

    stats = pd.DataFrame(index=df.columns)
    stats["count"] = df.count()
    stats["min"] = df.min(numeric_only=True)
    stats["max"] = df.max(numeric_only=True)
    stats["mean"] = df.mean(numeric_only=True)
    stats["std"] = df.std(numeric_only=True)
    stats["median"] = df.median(numeric_only=True)

    # временной диапазон по НЕ NaN
    starts = []
    ends = []
    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            starts.append(pd.NaT)
            ends.append(pd.NaT)
        else:
            starts.append(s.index.min())
            ends.append(s.index.max())
    stats["start"] = starts
    stats["end"] = ends

    # порядок колонок
    stats = stats[["count", "min", "max", "mean", "std", "median", "start", "end"]]
    return stats


def normalize_datetime_input(dt):
    """Streamlit date_input/datetime_input может вернуть date или datetime."""
    if dt is None:
        return None
    ts = pd.to_datetime(dt)
    return ts


def make_unique_name(base_name: str) -> str:
    """Если имя занято, добавляет суффикс _2, _3..."""
    existing = set()
    if st.session_state.signals_data is not None:
        existing |= set(st.session_state.signals_data.columns)
    existing |= set(st.session_state.derived_signals.keys())

    if base_name not in existing:
        return base_name

    k = 2
    while f"{base_name}_{k}" in existing:
        k += 1
    return f"{base_name}_{k}"


# --------------------
# Sidebar: выбор сигналов + создание обрезанных
# --------------------
with st.sidebar:
    st.header("Выбор сигналов")

    df_all_signals = get_all_signals_df()
    if df_all_signals is not None:
        available_signals = df_all_signals.columns.tolist()

        # чекбоксы выбора
        for signal in available_signals:
            is_selected = st.checkbox(
                signal,
                value=(signal in st.session_state.selected_signals),
                key=f"signal_{signal}"
            )
            if is_selected:
                st.session_state.selected_signals.add(signal)
            else:
                st.session_state.selected_signals.discard(signal)

        st.divider()

        # ---- Создание обрезанного сигнала (виртуальная копия) ----
        st.subheader("Обрезать сигнал по дате")

        base_df = st.session_state.signals_data
        if base_df is None or base_df.empty:
            st.info("Сначала загрузите исходные сигналы.")
        else:
            base_signal = st.selectbox(
                "Исходный сигнал",
                options=base_df.columns.tolist(),
                key="cut_base_signal"
            )

            # границы по умолчанию — по выбранному сигналу
            ser = base_df[base_signal].dropna()
            if ser.empty:
                st.warning("У выбранного сигнала нет данных.")
            else:
                default_start = ser.index.min().to_pydatetime()
                default_end = ser.index.max().to_pydatetime()

                colA, colB = st.columns(2)
                with colA:
                    cut_start = st.date_input(
                        "Начало (дата)",
                        value=default_start.date(),
                        key="cut_start_date"
                    )
                with colB:
                    cut_end = st.date_input(
                        "Конец (дата)",
                        value=default_end.date(),
                        key="cut_end_date"
                    )

                # интерпретация: start 00:00:00, end 23:59:59.999...
                start_ts = pd.Timestamp(cut_start)
                end_ts = pd.Timestamp(cut_end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

                suggested_name = f"{base_signal}__{start_ts.date()}_{end_ts.date()}"
                new_name = st.text_input(
                    "Имя обрезанного сигнала",
                    value=suggested_name,
                    key="cut_new_name"
                )

                colC, colD = st.columns(2)
                with colC:
                    create_btn = st.button("Создать обрезанный", type="primary")
                with colD:
                    clear_cut_btn = st.button("Очистить ВСЕ обрезанные")

                if clear_cut_btn:
                    st.session_state.derived_signals = {}
                    # также убрать из выбора те, которых больше нет
                    st.session_state.selected_signals = {
                        s for s in st.session_state.selected_signals
                        if (st.session_state.signals_data is not None and s in st.session_state.signals_data.columns)
                    }
                    st.rerun()

                if create_btn:
                    if not new_name.strip():
                        st.error("Имя не должно быть пустым.")
                    else:
                        unique_name = make_unique_name(new_name.strip())

                        cut_ser = base_df[base_signal].loc[(base_df.index >= start_ts) & (base_df.index <= end_ts)].copy()
                        cut_ser = cut_ser.dropna()

                        if cut_ser.empty:
                            st.warning("В выбранном диапазоне нет данных. Сигнал не создан.")
                        else:
                            ddf = pd.DataFrame({unique_name: cut_ser})
                            st.session_state.derived_signals[unique_name] = ddf
                            st.success(f"Создан обрезанный сигнал: {unique_name}")
                            st.rerun()

        # ---- Управление обрезанными сигналами ----
        if st.session_state.derived_signals:
            st.subheader("Обрезанные (временные)")
            derived_names = sorted(st.session_state.derived_signals.keys())
            del_name = st.selectbox("Удалить обрезанный", options=["—"] + derived_names, key="del_cut_select")
            if st.button("Удалить выбранный"):
                if del_name != "—":
                    st.session_state.derived_signals.pop(del_name, None)
                    st.session_state.selected_signals.discard(del_name)
                    st.rerun()

        st.divider()

        # ---- Области построения ----
        st.subheader("Области построения")
        col1, col2 = st.columns(2)
        if col1.button("➕ Добавить график"):
            new_id = max([a.get("id", 0) for a in st.session_state.plot_areas] + [0]) + 1
            st.session_state.plot_areas.append({"id": new_id, "signals": []})
            st.rerun()
        if col2.button("❌ Очистить все"):
            st.session_state.plot_areas = []
            st.session_state.selected_signals = set()
            st.rerun()

    else:
        st.info("📥 Данные сигналов еще не загружены.")

# --------------------
# Основная область: графики + статистика под каждым графиком
# --------------------
df_all_signals = get_all_signals_df()

if df_all_signals is not None and len(st.session_state.selected_signals) > 0:
    if not st.session_state.plot_areas:
        st.session_state.plot_areas.append({"id": 1, "signals": list(st.session_state.selected_signals)})

    for i, plot_area in enumerate(st.session_state.plot_areas):
        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader(f"График #{plot_area['id']}")
            with col2:
                if st.button("Remove", key=f"remove_{i}"):
                    st.session_state.plot_areas.pop(i)
                    st.rerun()

            area_signals = st.multiselect(
                "Выберите сигнал(ы):",
                options=list(st.session_state.selected_signals),
                default=plot_area.get("signals", []),
                key=f"area_signals_{i}"
            )
            st.session_state.plot_areas[i]["signals"] = area_signals

            if area_signals:
                df_plot = df_all_signals[area_signals].copy()

                fig = px.line(
                    df_plot,
                    x=df_plot.index,
                    y=area_signals,
                    title=f"Сигналы графика #{plot_area['id']}"
                )
                fig.update_layout(
                    height=350,
                    legend_title_text="Сигналы",
                    xaxis_title="Datetime",
                    yaxis_title="Value",
                    margin=dict(l=10, r=10, t=40, b=10),
                )

                st.plotly_chart(fig, use_container_width=True)

                # ---- Статистика под графиком (по ВСЕМУ выбранному сигналу) ----
                st.markdown("**Статистика (по всему сигналу):**")
                stats_df = compute_stats(df_plot)

                if stats_df.empty:
                    st.info("Нет данных для расчёта статистики.")
                else:
                    # Форматирование чисел
                    def _fmt(x):
                        if pd.isna(x):
                            return ""
                        if isinstance(x, (pd.Timestamp,)):
                            return str(x)
                        try:
                            return f"{float(x):.6g}"
                        except Exception:
                            return str(x)

                    show_df = stats_df.copy()
                    # немного удобства: start/end как строки покороче
                    show_df["start"] = show_df["start"].astype("datetime64[ns]").dt.strftime("%Y-%m-%d %H:%M:%S")
                    show_df["end"] = show_df["end"].astype("datetime64[ns]").dt.strftime("%Y-%m-%d %H:%M:%S")

                    st.dataframe(show_df.map(_fmt), use_container_width=True)

            else:
                st.info("Выберите сигналы для отображения в этой области")

        st.divider()

elif df_all_signals is None:
    st.info("📥 Awaiting signal data...")
else:
    st.info("👈 Выберите сигналы слева для визуализации")

# --------------------
# Инфо панель и код
# --------------------
if df_all_signals is not None:
    with st.expander("ℹ️ Data Info"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Всего сигналов (вкл. обрезанные)", len(df_all_signals.columns))
        with col2:
            st.metric("Количество точек (в объединённой сетке времени)", len(df_all_signals))
        with col3:
            try:
                time_range = df_all_signals.index.max() - df_all_signals.index.min()
                st.metric("Time Range", str(time_range).split(".")[0])
            except Exception:
                st.metric("Time Range", "—")

if CODE:
    with st.expander("🧩 Сгенерированный код"):
        st.code(CODE, language="text")