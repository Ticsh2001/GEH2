# neural_visualizer_app.py
import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import numpy as np
from typing import List, Dict, Optional

st.set_page_config(page_title="Neural Dataset Visualizer", layout="wide")
st.title("🧠 Визуализация обработанных датасетов")

# Получаем параметры URL
query_params = st.query_params
config = query_params.get("config", "")
api_url = query_params.get("api_url", "http://localhost:8000")

if not config:
    st.error("❌ Не указана конфигурация (параметр 'config')")
    st.stop()

def make_url(path: str) -> str:
    """Добавляет config и api_url к относительному пути."""
    if api_url:
        full = f"{api_url}{path}"
    else:
        full = path
    if "?" in full:
        return f"{full}&config={config}"
    return f"{full}?config={config}"

# Кэш для списка элементов и данных
if "elements_cache" not in st.session_state:
    st.session_state.elements_cache = None
if "data_cache" not in st.session_state:
    st.session_state.data_cache = {}
if "selected_elements" not in st.session_state:
    st.session_state.selected_elements = {}

def load_available_elements() -> List[Dict]:
    """Загружает список всех элементов, у которых есть обработанные файлы."""
    try:
        resp = requests.get(make_url("/api/nn/list"), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("elements", [])
    except Exception as e:
        st.error(f"Ошибка загрузки списка элементов: {e}")
        return []

def load_element_columns(element_id: str, port: str = "out-0") -> List[str]:
    """Загружает список столбцов датасета (без datetime)."""
    try:
        resp = requests.get(
            make_url(f"/api/nn/data/{element_id}/columns"),
            params={"port": port, "code": get_project_code(element_id)},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("columns", [])
    except Exception as e:
        st.error(f"Ошибка загрузки столбцов для {element_id}: {e}")
        return []

def load_data(element_id: str, port: str = "out-0") -> Optional[pd.DataFrame]:
    """Загружает полный DataFrame из обработанного файла."""
    try:
        resp = requests.get(
            make_url(f"/api/nn/data/{element_id}"),
            params={"port": port, "code": get_project_code(element_id)},
            timeout=30
        )
        resp.raise_for_status()
        # Предполагаем, что API возвращает JSON с ключом 'data' (список записей)
        data = resp.json()
        records = data.get("data", [])
        if not records:
            return None
        df = pd.DataFrame(records)
        if "datetime" not in df.columns:
            st.warning(f"В датасете {element_id} отсутствует столбец 'datetime'")
        else:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"])
            df = df.set_index("datetime").sort_index()
        return df
    except Exception as e:
        st.error(f"Ошибка загрузки данных для {element_id}: {e}")
        return None

def get_project_code(element_id: str) -> str:
    """
    Извлекает код проекта из ID элемента или из метаданных.
    Пока возвращаем значение из session_state или 'unknown'.
    В будущем можно хранить маппинг.
    """
    # Можно получить из API статуса, но пока захардкодим
    return st.session_state.get("project_code", "unknown")

# ========== Боковая панель: выбор элементов ==========
with st.sidebar:
    st.header("📦 Доступные датасеты")
    
    if st.button("🔄 Обновить список"):
        st.session_state.elements_cache = None
        st.session_state.data_cache = {}
        st.rerun()
    
    if st.session_state.elements_cache is None:
        with st.spinner("Загрузка списка..."):
            st.session_state.elements_cache = load_available_elements()
    
    elements = st.session_state.elements_cache
    if not elements:
        st.info("Нет обработанных элементов. Примените обработку в конструкторе.")
    else:
        for elem in elements:
            elem_id = elem["id"]
            name = elem.get("name", elem_id)
            # Кнопка для выбора элемента
            if st.button(f"{name} ({elem_id})", key=f"btn_{elem_id}"):
                # Загружаем столбцы при первом выборе
                if elem_id not in st.session_state.selected_elements:
                    cols = load_element_columns(elem_id, port="out-0")
                    st.session_state.selected_elements[elem_id] = {
                        "name": name,
                        "columns": cols,
                        "selected_columns": [],
                        "loaded": False,
                        "data": None
                    }
                # Загружаем полные данные, если ещё не загружены
                if not st.session_state.selected_elements[elem_id]["loaded"]:
                    with st.spinner(f"Загрузка данных {name}..."):
                        df = load_data(elem_id, port="out-0")
                        if df is not None:
                            st.session_state.selected_elements[elem_id]["data"] = df
                            st.session_state.selected_elements[elem_id]["loaded"] = True
                        else:
                            st.warning(f"Не удалось загрузить данные для {name}")
    
    st.divider()
    st.subheader("Выбранные для визуализации")
    # Отображаем уже выбранные элементы с возможностью выбора столбцов
    for elem_id, info in st.session_state.selected_elements.items():
        with st.expander(f"📊 {info['name']} ({elem_id})"):
            if info["columns"]:
                selected = st.multiselect(
                    "Столбцы",
                    info["columns"],
                    default=info.get("selected_columns", []),
                    key=f"cols_{elem_id}"
                )
                info["selected_columns"] = selected
            else:
                st.warning("Нет столбцов")
            if st.button("❌ Удалить", key=f"del_{elem_id}"):
                del st.session_state.selected_elements[elem_id]
                st.rerun()

# ========== Основная область: графики ==========
if not st.session_state.selected_elements:
    st.info("👈 Выберите датасет(ы) в боковой панели для начала визуализации.")
else:
    for elem_id, info in st.session_state.selected_elements.items():
        if info["loaded"] and info["selected_columns"]:
            df = info["data"]
            if df is None or df.empty:
                st.warning(f"Нет данных для {info['name']}")
                continue
                
            st.subheader(f"📈 {info['name']} ({elem_id})")
            
            # Выбор диапазона дат
            if isinstance(df.index, pd.DatetimeIndex):
                min_date = df.index.min().date()
                max_date = df.index.max().date()
                date_range = st.date_input(
                    f"Диапазон дат для {info['name']}",
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date,
                    key=f"dates_{elem_id}"
                )
                if len(date_range) == 2:
                    start, end = date_range
                    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
                    df_plot = df.loc[mask]
                else:
                    df_plot = df
            else:
                df_plot = df
            
            # График
            cols_to_plot = [c for c in info["selected_columns"] if c in df_plot.columns]
            if cols_to_plot:
                fig = px.line(
                    df_plot,
                    x=df_plot.index,
                    y=cols_to_plot,
                    title=f"График {info['name']}",
                    render_mode="webgl" if len(df_plot) > 10000 else "auto"
                )
                fig.update_layout(
                    height=500,
                    xaxis_title="Время",
                    yaxis_title="Значение",
                    legend_title_text="Столбцы"
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # Статистика
                with st.expander(f"📊 Статистика для {info['name']}"):
                    stats_df = pd.DataFrame(index=cols_to_plot)
                    stats_df["count"] = df_plot[cols_to_plot].count()
                    stats_df["min"] = df_plot[cols_to_plot].min()
                    stats_df["max"] = df_plot[cols_to_plot].max()
                    stats_df["mean"] = df_plot[cols_to_plot].mean()
                    stats_df["std"] = df_plot[cols_to_plot].std()
                    stats_df["median"] = df_plot[cols_to_plot].median()
                    st.dataframe(stats_df.style.format("{:.4g}"))
            else:
                st.info("Выберите столбцы для отображения.")
        else:
            if not info["loaded"]:
                st.info(f"Нажмите кнопку загрузки для {info['name']}")
            else:
                st.info(f"Выберите столбцы в боковой панели для {info['name']}")