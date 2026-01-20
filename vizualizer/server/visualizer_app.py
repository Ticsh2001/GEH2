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
    st.session_state.signals_data = None
if "selected_signals" not in st.session_state:
    st.session_state.selected_signals = set()
if "plot_areas" not in st.session_state:
    st.session_state.plot_areas = []
if "derived_signals" not in st.session_state:
    st.session_state.derived_signals = {}  # временные обрезанные сигналы

# --------------------
# Утилиты
# --------------------
def load_signals(signal_codes):
    if not signal_codes:
        st.info("Список сигналов пуст — ничего загружать.")
        return None, [], []
    try:
        response = requests.post(
            f"{api_url}/api/signal-data",
            json={"signal_names": signal_codes, "format": "json"},
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


def get_all_signals_df():
    base = st.session_state.signals_data
    derived = st.session_state.derived_signals
    if base is None and not derived:
        return None
    dfs = []
    if base is not None:
        dfs.append(base)
    for _, ddf in derived.items():
        dfs.append(ddf)
    if not dfs:
        return None
    # outer join по индексу времени
    return pd.concat(dfs, axis=1).sort_index()


def sanitize_numeric_column(series: pd.Series) -> pd.Series:
    # Попытка корректно привести к числу: поддержка запятой как десятичного
    if series.dtype.kind in ("i", "u", "f"):
        return series  # уже число
    s = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def compute_stats_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Статистика только по тем колонкам, где после конверсии есть числа."""
    if df is None or df.empty:
        return pd.DataFrame()

    num = df.apply(sanitize_numeric_column)
    valid_cols = [c for c in num.columns if num[c].count() > 0]
    if not valid_cols:
        return pd.DataFrame()

    num = num[valid_cols]

    out = pd.DataFrame(index=num.columns)
    out["count"] = num.count()
    out["min"] = num.min()
    out["max"] = num.max()
    out["mean"] = num.mean()
    out["std"] = num.std()
    out["median"] = num.median()

    starts, ends = [], []
    for col in num.columns:
        s = num[col].dropna()
        starts.append(s.index.min() if not s.empty else pd.NaT)
        ends.append(s.index.max() if not s.empty else pd.NaT)
    out["start"] = starts
    out["end"] = ends
    return out


def make_unique_name(base_name: str) -> str:
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
# Загрузка исходных сигналов
# --------------------
if signal_codes and st.session_state.signals_data is None:
    with st.spinner("Загружаем данные сигналов..."):
        df_all, found, not_found = load_signals(signal_codes)
        st.session_state.signals_data = df_all
        st.success(f"✅ Загружено сигналов: {len(found)}")
        if not_found:
            st.warning(f"⚠️ Не найдены: {', '.join(not_found)}")

# --------------------
# Боковая панель
# --------------------
with st.sidebar:
    st.header("Выбор сигналов")

    df_all_signals = get_all_signals_df()
    if df_all_signals is not None:
        available_signals = df_all_signals.columns.tolist()

        for signal in available_signals:
            checked = st.checkbox(signal, value=(signal in st.session_state.selected_signals))
            if checked:
                st.session_state.selected_signals.add(signal)
            else:
                st.session_state.selected_signals.discard(signal)

        st.divider()
        st.subheader("Создать обрезанный сигнал")

        base_df = st.session_state.signals_data
        if base_df is not None:
            base_choice = st.selectbox("Исходный сигнал", base_df.columns)
            s = base_df[base_choice].dropna()
            if not s.empty:
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input("Начало", value=s.index.min().date())
                with col2:
                    end_date = st.date_input("Конец", value=s.index.max().date())

                start_ts = pd.Timestamp(start_date)
                end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

                default_name = f"{base_choice}__{start_ts.date()}_{end_ts.date()}"
                new_name = st.text_input("Имя нового сигнала", value=default_name)

                col3, col4 = st.columns(2)
                if col3.button("Создать"):
                    name_unique = make_unique_name(new_name.strip())
                    cut_ser = s[(s.index >= start_ts) & (s.index <= end_ts)]
                    if cut_ser.empty:
                        st.warning("В выбранном диапазоне нет точек.")
                    else:
                        st.session_state.derived_signals[name_unique] = pd.DataFrame({name_unique: cut_ser})
                        st.success(f"Создан обрезанный сигнал: {name_unique}")
                        st.rerun()
                if col4.button("Очистить все обрезанные"):
                    st.session_state.derived_signals.clear()
                    st.session_state.selected_signals = {
                        sig for sig in st.session_state.selected_signals
                        if (st.session_state.signals_data is not None and sig in st.session_state.signals_data.columns)
                    }
                    st.experimental_rerun()

        if st.session_state.derived_signals:
            st.subheader("Удалить обрезанный сигнал")
            derived_names = list(st.session_state.derived_signals.keys())
            del_name = st.selectbox("Выберите", ["—"] + derived_names)
            if st.button("Удалить выбранный") and del_name != "—":
                st.session_state.derived_signals.pop(del_name, None)
                st.session_state.selected_signals.discard(del_name)
                st.rerun()

        st.divider()
        st.subheader("Области построения")
        c1, c2 = st.columns(2)
        if c1.button("➕ Добавить график"):
            new_id = max([a.get("id", 0) for a in st.session_state.plot_areas] + [0]) + 1
            st.session_state.plot_areas.append({"id": new_id, "signals": []})
            st.experimental_rerun()
        if c2.button("❌ Очистить все"):
            st.session_state.plot_areas = []
            st.session_state.selected_signals = set()
            st.experimental_rerun()

    else:
        st.info("📥 Данные сигналов еще не загружены.")

# --------------------
# Основная область
# --------------------
df_all_signals = get_all_signals_df()

if df_all_signals is not None and st.session_state.selected_signals:
    if not st.session_state.plot_areas:
        st.session_state.plot_areas.append({"id": 1, "signals": list(st.session_state.selected_signals)})

    for i, plot_area in enumerate(st.session_state.plot_areas):
        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader(f"График #{plot_area['id']}")
            with col2:
                if st.button("Удалить", key=f"remove_area_{i}"):
                    st.session_state.plot_areas.pop(i)
                    st.rerun()

            selected = st.multiselect(
                "Выберите сигнал(ы):",
                list(st.session_state.selected_signals),
                default=plot_area.get("signals", []),
                key=f"signals_sel_{i}",
            )
            st.session_state.plot_areas[i]["signals"] = selected

            if selected:
                # График строим на исходных данных (не трогаем значения)
                df_plot = df_all_signals[selected].copy()

                fig = px.line(df_plot, x=df_plot.index, y=selected, title=f"График #{plot_area['id']}")
                fig.update_layout(
                    height=350,
                    legend_title_text="Сигналы",
                    xaxis_title="Время",
                    yaxis_title="Значение",
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig, use_container_width=True)

                # ---- Статистика под графиком (по числовым данным, с поддержкой запятой) ----
                st.markdown("**📊 Статистика (по всему сигналу):**")
                stats_df = compute_stats_numeric(df_plot)

                if stats_df.empty:
                    st.info("Нет числовых данных для расчёта статистики.")
                else:
                    show_df = stats_df.copy()
                    show_df["start"] = pd.to_datetime(show_df["start"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
                    show_df["end"] = pd.to_datetime(show_df["end"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
                    st.dataframe(
                        show_df.style.format(
                            {
                                "count": "{:.0f}",
                                "min": "{:.6g}",
                                "max": "{:.6g}",
                                "mean": "{:.6g}",
                                "std": "{:.6g}",
                                "median": "{:.6g}",
                            },
                            na_rep="",
                        ),
                        use_container_width=True,
                    )
            else:
                st.info("Выберите сигналы для отображения.")
        st.divider()

elif df_all_signals is None:
    st.info("📥 Данные сигналов ещё не загружены.")
else:
    st.info("👈 Выберите сигналы слева для визуализации.")

# --------------------
# Инфо панель
# --------------------
if df_all_signals is not None:
    with st.expander("ℹ️ Информация о данных"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Всего сигналов (вкл. обрезанные)", len(df_all_signals.columns))
        with col2:
            st.metric("Количество записей", len(df_all_signals))
        with col3:
            try:
                dt_range = df_all_signals.index.max() - df_all_signals.index.min()
                st.metric("Диапазон времени", str(dt_range).split(".")[0])
            except Exception:
                st.metric("Диапазон времени", "—")

if CODE:
    with st.expander("🧩 Сгенерированный код"):
        st.code(CODE, language="text")