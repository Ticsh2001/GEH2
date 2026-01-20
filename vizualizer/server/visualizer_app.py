import pandas as pd
import requests
import streamlit as st
import plotly.express as px  # ИЗМЕНЕНО: импортируем plotly express

st.set_page_config(page_title="Signal Visualizer", layout="wide")
st.title("📊 Вихзуализация сигналов")

# --------------------
# Чтение query params и загрузка сессии (без изменений)
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
# Состояние (без изменений)
# --------------------
if "signals_data" not in st.session_state:
    st.session_state.signals_data = None
if "selected_signals" not in st.session_state:
    st.session_state.selected_signals = set()
if "plot_areas" not in st.session_state:
    st.session_state.plot_areas = []

# --------------------
# Загрузка данных сигналов (без изменений)
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
            if not records: continue
            df = pd.DataFrame(records).set_index("datetime").rename(columns={"value": sig})
            df.index = pd.to_datetime(df.index)
            dfs.append(df[[sig]])

        if not dfs: return None, found, not_found
        return pd.concat(dfs, axis=1).sort_index(), found, not_found

    except Exception as e:
        st.error(f"❌ Ошибка загрузки данных: {e}")
        return None, [], []

if signal_codes and st.session_state.signals_data is None:
    with st.spinner("Загружаем данные сигналов..."):
        df_all, found, not_found = load_signals(signal_codes)
        st.session_state.signals_data = df_all
        st.success(f"✅ Загружено сигналов: {len(found)}")
        if not_found: st.warning(f"⚠️ Не найдены: {', '.join(not_found)}")

# --------------------
# Боковая панель (без изменений)
# --------------------
with st.sidebar:
    st.header("Выбор сигналов")
    if st.session_state.signals_data is not None:
        available_signals = st.session_state.signals_data.columns.tolist()
        for signal in available_signals:
            is_selected = st.checkbox(
                signal, value=(signal in st.session_state.selected_signals), key=f"signal_{signal}"
            )
            if is_selected: st.session_state.selected_signals.add(signal)
            else: st.session_state.selected_signals.discard(signal)
        
        st.divider()
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
# Основная область (ЗДЕСЬ ГЛАВНЫЕ ИЗМЕНЕНИЯ)
# --------------------
if st.session_state.signals_data is not None and len(st.session_state.selected_signals) > 0:
    if not st.session_state.plot_areas:
        st.session_state.plot_areas.append({
            "id": 1, "signals": list(st.session_state.selected_signals)
        })

    for i, plot_area in enumerate(st.session_state.plot_areas):
        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1: st.subheader(f"График #{plot_area['id']}")
            with col2:
                if st.button("Remove", key=f"remove_{i}"):
                    st.session_state.plot_areas.pop(i)
                    st.rerun()

            area_signals = st.multiselect(
                "Выберите сигнал:",
                options=list(st.session_state.selected_signals),
                default=plot_area.get("signals", []),
                key=f"area_signals_{i}"
            )
            st.session_state.plot_areas[i]["signals"] = area_signals

            if area_signals:
                df_plot = st.session_state.signals_data[area_signals].copy()
                
                # ИЗМЕНЕНО: Вместо HoloViews используем Plotly Express
                fig = px.line(df_plot, x=df_plot.index, y=area_signals,
                              title=f"Сигналы графика #{plot_area['id']}")
                fig.update_layout(
                    height=350,
                    legend_title_text='Сигналы',
                    xaxis_title='Datetime',
                    yaxis_title='Value'
                )
                
                # ИЗМЕНЕНО: Отображаем график с помощью st.plotly_chart
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Выберите сигналы для отображения в этой области")
        st.divider()

elif st.session_state.signals_data is None:
    st.info("📥 Awaiting signal data...")
else:
    st.info("👈 Выберите сигналы слева для визуализации")

# --------------------
# Инфо панель и код (без изменений)
# --------------------
if st.session_state.signals_data is not None:
    with st.expander("ℹ️ Data Info"):
        col1, col2, col3 = st.columns(3)
        with col1: st.metric("Всего сигналов", len(st.session_state.signals_data.columns))
        with col2: st.metric("Количество точек", len(st.session_state.signals_data))
        with col3:
            time_range = st.session_state.signals_data.index.max() - st.session_state.signals_data.index.min()
            st.metric("Time Range", str(time_range).split('.')[0])

if CODE:
    with st.expander("🧩 Сгенерированный код"):
        st.code(CODE, language="text")