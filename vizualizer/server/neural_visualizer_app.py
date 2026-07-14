import streamlit as st
import pandas as pd
import plotly.express as px
import requests

st.set_page_config(page_title="Neural Dataset Visualizer", layout="wide")
st.title("📊 Визуализация обработанных датасетов")

query_params = st.query_params
config = query_params.get("config", "")
api_url = query_params.get("api_url", "http://localhost:8000")
project_code = query_params.get("code", "")

if not config or not project_code:
    st.error("Не заданы config или code проекта")
    st.stop()

@st.cache_data(show_spinner=False)
def load_elements(_config: str, _code: str) -> list:
    try:
        resp = requests.get(f"{api_url}/api/nn/list", params={"config": _config, "code": _code})
        resp.raise_for_status()
        return resp.json().get("elements", [])
    except Exception as e:
        st.error(f"Ошибка загрузки списка датасетов: {e}")
        return []

@st.cache_data(show_spinner=False)
def load_data(element_id: str, _config: str, _code: str, port: str = "out-0") -> pd.DataFrame:
    try:
        params = {"config": _config, "code": _code, "port": port}
        resp = requests.get(f"{api_url}/api/nn/data/{element_id}/full", params=params)
        resp.raise_for_status()
        df = pd.DataFrame(resp.json())
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
        return df
    except Exception as e:
        st.error(f"Ошибка загрузки данных: {e}")
        return pd.DataFrame()

elements = load_elements(config, project_code)

if not elements:
    st.sidebar.warning("Нет сохранённых данных для этого проекта. Примените элементы в конструкторе.")
    st.stop()

st.sidebar.header("Доступные датасеты")
selected = []
for elem in elements:
    elem_id = elem["element_id"]
    label = f"{elem_id} ({elem.get('description', '')})"
    if st.sidebar.checkbox(label, key=f"elem_{elem_id}"):
        selected.append(elem)

if not selected:
    st.info("Выберите датасеты для визуализации в боковом меню.")
    st.stop()

# Загружаем данные и отображаем
for elem in selected:
    elem_id = elem["element_id"]
    st.subheader(f"Датасет: {elem_id}")
    df = load_data(elem_id, config, project_code)
    if df.empty:
        st.warning("Пустой датасет")
        continue
    all_cols = [c for c in df.columns if c != 'datetime']
    selected_cols = st.multiselect(f"Столбцы для {elem_id}", all_cols, default=all_cols[:2] if len(all_cols)>=2 else all_cols, key=f"cols_{elem_id}")
    if not selected_cols:
        continue
    if 'datetime' in df.columns:
        min_date = df['datetime'].min().date()
        max_date = df['datetime'].max().date()
        date_range = st.date_input(f"Диапазон дат для {elem_id}", [min_date, max_date], key=f"dr_{elem_id}")
        if len(date_range) == 2:
            start, end = date_range
            df = df[(df['datetime'].dt.date >= start) & (df['datetime'].dt.date <= end)]
    if 'datetime' in df.columns:
        fig = px.line(df, x='datetime', y=selected_cols, title=elem_id)
    else:
        fig = px.line(df, y=selected_cols, title=elem_id)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown("**Статистика:**")
    st.dataframe(df[selected_cols].describe())
    st.divider()