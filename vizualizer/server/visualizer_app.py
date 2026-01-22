# visualizer_app.py — обновлённая версия

import pandas as pd
import requests
import streamlit as st
import plotly.express as px
import numpy as np
import plotly.graph_objects as go
from typing import List
from datetime import datetime, time

from code_signal import compute_code_signal, sanitize_numeric_column

st.set_page_config(page_title="Signal Visualizer", layout="wide")
st.title("📊 Визуализация сигналов")

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

if "signals_data" not in st.session_state:
    st.session_state.signals_data = None
if "selected_signals" not in st.session_state:
    st.session_state.selected_signals = set()
if "plot_areas" not in st.session_state:
    st.session_state.plot_areas = []
if "derived_signals" not in st.session_state:
    st.session_state.derived_signals = {}
if "code_signal_name" not in st.session_state:
    st.session_state.code_signal_name = None
if "synthetic_computed" not in st.session_state:
    st.session_state.synthetic_computed = {}
if "signal_groups" not in st.session_state:
    st.session_state.signal_groups = {"project": set(), "dependencies": set()}
if "global_cursor_time" not in st.session_state:
    st.session_state.global_cursor_time = None


def load_base_signals_data(signal_names: List[str]) -> pd.DataFrame | None:
    """Загружает данные базовых сигналов из архива"""
    if not signal_names:
        return None
    
    try:
        response = requests.post(
            f"{api_url}/api/signal-data",
            json={"signal_names": signal_names, "format": "json"},
        )
        response.raise_for_status()
        result = response.json()
        
        found = result.get("found", [])
        not_found = result.get("not_found", [])
        data_dict = result.get("data", {})
        
        if not_found:
            st.warning(f"⚠️ Базовые сигналы не найдены в архиве: {', '.join(not_found)}")
        
        if not data_dict:
            return None
        
        frames = []
        for sig, records in data_dict.items():
            if not records:
                continue
            df = pd.DataFrame(records)
            if "datetime" not in df or "value" not in df:
                continue
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"])
            df = df.set_index("datetime").sort_index()
            df = df.rename(columns={"value": sig})
            frames.append(df[[sig]])
        
        if not frames:
            return None
        
        return pd.concat(frames, axis=1).sort_index()
    
    except Exception as exc:
        st.error(f"❌ Ошибка загрузки базовых сигналов: {exc}")
        return None


def resolve_and_load_all_signals(input_signals: List[str]) -> tuple[pd.DataFrame | None, List[str], List[str]]:
    if not input_signals:
        return None, [], []
    
    try:
        with st.spinner("🔍 Разворачиваем зависимости сигналов..."):
            resolve_resp = requests.post(
                f"{api_url}/api/resolve-signals",
                json={"signals": input_signals}
            )
            resolve_resp.raise_for_status()
            resolve_data = resolve_resp.json()
        
        base_signals = resolve_data.get("base_signals", [])
        synthetic_signals = resolve_data.get("synthetic_signals", {})
        computation_order = resolve_data.get("computation_order", [])
        
        project_signals = set(input_signals)
        dependency_signals = set()
        for syn_name, syn_data in synthetic_signals.items():
            if syn_name not in project_signals:
                dependency_signals.add(syn_name)
            for dep in syn_data.get("dependencies", []):
                if dep not in project_signals:
                    dependency_signals.add(dep)
        
        for bs in base_signals:
            if bs not in project_signals:
                dependency_signals.add(bs)
        
        st.session_state.signal_groups = {
            "project": project_signals,
            "dependencies": dependency_signals
        }
        
        st.info(f"📊 Сигналов проекта: {len(project_signals)} | Из зависимостей: {len(dependency_signals)}")
        
        if synthetic_signals:
            with st.expander("🔗 Граф зависимостей синтетических сигналов"):
                for syn_name in computation_order:
                    deps = synthetic_signals[syn_name].get("dependencies", [])
                    marker = "📌" if syn_name in project_signals else "🔗"
                    st.text(f"  {marker} {syn_name} ← {deps}")
        
        df_all = None
        found_signals = []
        not_found_signals = []
        
        if base_signals:
            with st.spinner(f"📥 Загружаем {len(base_signals)} базовых сигналов..."):
                df_all = load_base_signals_data(base_signals)
                if df_all is not None:
                    found_signals = list(df_all.columns)
                    not_found_signals = [s for s in base_signals if s not in df_all.columns]
        
        if df_all is None:
            df_all = pd.DataFrame()
        
        if computation_order:
            with st.spinner(f"⚙️ Вычисляем {len(computation_order)} синтетических сигналов..."):
                progress_bar = st.progress(0)
                
                for idx, syn_name in enumerate(computation_order):
                    syn_data = synthetic_signals[syn_name]
                    formula = syn_data.get("formula", "")
                    
                    if not formula:
                        st.warning(f"⚠️ Синтетический сигнал '{syn_name}' не имеет формулы")
                        continue
                    
                    if df_all.empty:
                        st.warning(f"⚠️ Нет данных для вычисления '{syn_name}'")
                        continue
                    
                    try:
                        syn_series = compute_code_signal(
                            formula,
                            df_all,
                            warn_callback=lambda msg, name=syn_name: st.warning(f"[{name}] {msg}", icon="⚠️")
                        )
                        syn_series.name = syn_name
                        df_all[syn_name] = syn_series
                        found_signals.append(syn_name)
                        st.session_state.synthetic_computed[syn_name] = formula
                        
                    except Exception as e:
                        st.error(f"❌ Ошибка вычисления '{syn_name}': {e}")
                        not_found_signals.append(syn_name)
                    
                    progress_bar.progress((idx + 1) / len(computation_order))
                
                progress_bar.empty()
        
        return df_all if not df_all.empty else None, found_signals, not_found_signals
    
    except requests.exceptions.HTTPError as http_err:
        error_detail = ""
        try:
            error_detail = http_err.response.json().get("detail", "")
        except:
            pass
        st.error(f"❌ Ошибка API: {error_detail or http_err}")
        return None, [], []
    except Exception as exc:
        st.error(f"❌ Ошибка загрузки данных: {exc}")
        import traceback
        st.code(traceback.format_exc())
        return None, [], []


# ========== ЗАГРУЗКА ДАННЫХ ==========
if signal_codes and st.session_state.signals_data is None:
    df_base, found_codes, not_found_codes = resolve_and_load_all_signals(signal_codes)
    st.session_state.signals_data = df_base
    
    if found_codes:
        st.success(f"✅ Загружено сигналов: {len(found_codes)}")
    if not_found_codes:
        st.warning(f"⚠️ Не найдены: {', '.join(not_found_codes)}")


def get_all_signals_df(exclude: set[str] | None = None):
    exclude = exclude or set()
    base = st.session_state.signals_data
    derived = st.session_state.derived_signals

    dfs = []
    if base is not None:
        dfs.append(base)
    for name, ddf in derived.items():
        if name in exclude:
            continue
        dfs.append(ddf)

    if not dfs:
        return None
    return pd.concat(dfs, axis=1).sort_index()


def compute_stats_numeric(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    numeric = df.apply(sanitize_numeric_column)
    valid_cols = [col for col in numeric.columns if numeric[col].count() > 0]
    if not valid_cols:
        return pd.DataFrame()

    numeric = numeric[valid_cols]
    stats = pd.DataFrame(index=numeric.columns)
    stats["count"] = numeric.count()
    stats["min"] = numeric.min()
    stats["max"] = numeric.max()
    stats["mean"] = numeric.mean()
    stats["std"] = numeric.std()
    stats["median"] = numeric.median()

    starts, ends = [], []
    for col in numeric.columns:
        series = numeric[col].dropna()
        starts.append(series.index.min() if not series.empty else pd.NaT)
        ends.append(series.index.max() if not series.empty else pd.NaT)

    stats["start"] = starts
    stats["end"] = ends
    return stats


def make_unique_name(base_name: str) -> str:
    existing = set()
    if st.session_state.signals_data is not None:
        existing |= set(st.session_state.signals_data.columns)
    existing |= set(st.session_state.derived_signals.keys())
    if base_name not in existing:
        return base_name
    idx = 2
    while f"{base_name}_{idx}" in existing:
        idx += 1
    return f"{base_name}_{idx}"


if signal_codes and st.session_state.signals_data is None:
    with st.spinner("Загружаем данные сигналов..."):
        df_all, found_codes, not_found_codes = resolve_and_load_all_signals(signal_codes)
        st.success(f"✅ Загружено сигналов: {len(found_codes)}")
        if not_found_codes:
            st.warning(f"⚠️ Не найдены: {', '.join(not_found_codes)}")

# --- синтетический сигнал из CODE ---
code_signal_name = st.session_state.code_signal_name
df_for_code = get_all_signals_df(exclude={code_signal_name} if code_signal_name else None)
code_key = (session_token, CODE)

already_have_series = (
    st.session_state.code_signal_name is not None
    and st.session_state.code_signal_name in st.session_state.derived_signals
)

if CODE and df_for_code is not None:
    need_recalc = (st.session_state.get("code_key") != code_key) or (not already_have_series)

    if need_recalc:
        try:
            synthetic_series = compute_code_signal(
                CODE,
                df_for_code,
                warn_callback=lambda msg: st.warning(msg, icon="⚠️"),
            )
            target_name = code_signal_name or make_unique_name("CODE_RESULT")
            synthetic_series.name = target_name

            st.session_state.derived_signals[target_name] = pd.DataFrame({target_name: synthetic_series})
            st.session_state.code_signal_name = target_name
            st.session_state.selected_signals.add(target_name)

            st.session_state.code_key = code_key
            st.success(f"Синтетический сигнал обновлён: {target_name}")
        except Exception as exc:
            st.warning(f"Не удалось вычислить CODE: {exc}")

elif not CODE:
    if code_signal_name:
        st.session_state.derived_signals.pop(code_signal_name, None)
        st.session_state.selected_signals.discard(code_signal_name)
        st.session_state.code_signal_name = None
    st.session_state.code_key = None

df_all_signals = get_all_signals_df()

with st.sidebar:
    st.header("Выбор сигналов")

    if df_all_signals is not None:
        available_signals = df_all_signals.columns.tolist()
        
        signal_groups = st.session_state.get("signal_groups", {
            "project": set(available_signals),
            "dependencies": set()
        })
        
        project_signals = [s for s in available_signals if s in signal_groups.get("project", set())]
        dependency_signals = [s for s in available_signals if s in signal_groups.get("dependencies", set())]
        
        if project_signals:
            st.subheader("📌 Сигналы проекта")
            for signal in project_signals:
                is_synthetic = signal in st.session_state.get("synthetic_computed", {})
                label = f"⚙️ {signal}" if is_synthetic else signal
                
                checked = st.checkbox(
                    label,
                    value=(signal in st.session_state.selected_signals),
                    key=f"proj_{signal}"
                )
                if checked:
                    st.session_state.selected_signals.add(signal)
                else:
                    st.session_state.selected_signals.discard(signal)
        
        if dependency_signals:
            st.divider()
            with st.expander(f"🔗 Из зависимостей ({len(dependency_signals)})", expanded=False):
                for signal in dependency_signals:
                    is_synthetic = signal in st.session_state.get("synthetic_computed", {})
                    label = f"⚙️ {signal}" if is_synthetic else signal
                    
                    checked = st.checkbox(
                        label,
                        value=(signal in st.session_state.selected_signals),
                        key=f"dep_{signal}"
                    )
                    if checked:
                        st.session_state.selected_signals.add(signal)
                    else:
                        st.session_state.selected_signals.discard(signal)
        
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Все проекта"):
                st.session_state.selected_signals.update(project_signals)
                st.rerun()
        with col2:
            if st.button("❌ Снять все"):
                st.session_state.selected_signals.clear()
                st.rerun()

        st.divider()
        st.subheader("Создать обрезанный сигнал")

        base_df = st.session_state.signals_data
        if base_df is not None and not base_df.empty:
            base_choice = st.selectbox("Исходный сигнал", base_df.columns)
            series = base_df[base_choice].dropna()
            if not series.empty:
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input(
                        "Начало",
                        value=series.index.min().date(),
                    )
                with col2:
                    end_date = st.date_input(
                        "Конец",
                        value=series.index.max().date(),
                    )

                start_ts = pd.Timestamp(start_date)
                end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(
                    microseconds=1
                )

                default_name = f"{base_choice}__{start_ts.date()}_{end_ts.date()}"
                new_name = st.text_input("Имя нового сигнала", value=default_name)

                col3, col4 = st.columns(2)
                if col3.button("Создать"):
                    name_unique = make_unique_name(new_name.strip())
                    cut_series = series[(series.index >= start_ts) & (series.index <= end_ts)]
                    if cut_series.empty:
                        st.warning("В выбранном диапазоне нет точек.")
                    else:
                        st.session_state.derived_signals[name_unique] = pd.DataFrame(
                            {name_unique: cut_series}
                        )
                        st.success(f"Создан обрезанный сигнал: {name_unique}")
                        st.rerun()
                if col4.button("Очистить все обрезанные"):
                    st.session_state.derived_signals = {
                        k: v
                        for k, v in st.session_state.derived_signals.items()
                        if k == st.session_state.code_signal_name
                    }
                    st.session_state.selected_signals = {
                        sig
                        for sig in st.session_state.selected_signals
                        if (st.session_state.signals_data is not None and sig in st.session_state.signals_data.columns)
                        or sig == st.session_state.code_signal_name
                    }
                    st.rerun()

        if st.session_state.derived_signals:
            st.subheader("Удалить обрезанный/синтетический сигнал")
            derived_names = [name for name in st.session_state.derived_signals.keys()]
            delete_candidate = st.selectbox("Выберите", ["—"] + derived_names)
            if st.button("Удалить выбранный") and delete_candidate != "—":
                st.session_state.derived_signals.pop(delete_candidate, None)
                st.session_state.selected_signals.discard(delete_candidate)
                if delete_candidate == st.session_state.code_signal_name:
                    st.session_state.code_signal_name = None
                st.rerun()

        st.divider()
        st.subheader("Области построения")
        col_a, col_b = st.columns(2)
        if col_a.button("➕ Добавить график"):
            new_id = max([area.get("id", 0) for area in st.session_state.plot_areas] + [0]) + 1
            st.session_state.plot_areas.append({
                "id": new_id, 
                "signals": [], 
                "shapes": [], 
                "cursor_time": None,  # Храним время, а не индекс
                "x_range": None,      # [start_datetime, end_datetime]
                "y_range": None       # [y_min, y_max]
            })
            st.rerun()
        if col_b.button("❌ Очистить все"):
            st.session_state.plot_areas = []
            st.session_state.selected_signals = set()
            st.session_state.global_cursor_time = None
            st.rerun()
    else:
        st.info("📥 Данные сигналов еще не загружены.")


def find_nearest_index_in_range(valid_index, target_time, x_start, x_end):
    """Находит ближайший индекс в заданном диапазоне"""
    # Фильтруем индекс по диапазону
    mask = (valid_index >= x_start) & (valid_index <= x_end)
    filtered_index = valid_index[mask]
    
    if len(filtered_index) == 0:
        return 0, valid_index[0] if len(valid_index) > 0 else None
    
    if target_time is None:
        return 0, filtered_index[0]
    
    # Находим ближайший
    diffs = abs((filtered_index - pd.to_datetime(target_time)).total_seconds())
    min_pos = diffs.argmin()
    return min_pos, filtered_index[min_pos]


if df_all_signals is not None and st.session_state.selected_signals:
    if not st.session_state.plot_areas:
        st.session_state.plot_areas.append({
            "id": 1, 
            "signals": list(st.session_state.selected_signals), 
            "shapes": [], 
            "cursor_time": None,
            "x_range": None,
            "y_range": None
        })

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
                df_plot = df_all_signals[selected].copy()
                df_plot_num = df_plot.apply(sanitize_numeric_column)

                valid_index = df_plot_num.dropna(how="all").index
                if len(valid_index) == 0:
                    st.warning("Нет числовых данных для выбранных сигналов.")
                else:
                    # === ПОЛНЫЙ ДИАПАЗОН ДАННЫХ ===
                    full_x_min = valid_index.min()
                    full_x_max = valid_index.max()
                    
                    y_data = df_plot_num.values.flatten()
                    y_data = y_data[~np.isnan(y_data)]
                    full_y_min = float(y_data.min()) if len(y_data) > 0 else 0.0
                    full_y_max = float(y_data.max()) if len(y_data) > 0 else 1.0
                    
                    # Небольшой отступ для Y
                    y_padding = (full_y_max - full_y_min) * 0.05
                    full_y_min -= y_padding
                    full_y_max += y_padding

                    # === ИНИЦИАЛИЗАЦИЯ ДИАПАЗОНОВ (если не заданы) ===
                    if plot_area.get('x_range') is None:
                        plot_area['x_range'] = [full_x_min, full_x_max]
                    
                    if plot_area.get('y_range') is None:
                        plot_area['y_range'] = [full_y_min, full_y_max]
                    
                    # Текущие диапазоны
                    current_x_start, current_x_end = plot_area['x_range']
                    current_y_min, current_y_max = plot_area['y_range']

                    # === ФИЛЬТРУЕМ ДАННЫЕ ПО ВИДИМОМУ ДИАПАЗОНУ X ===
                    x_start_ts, x_end_ts = plot_area['x_range']
                    mask_visible = (valid_index >= x_start_ts) & (valid_index <= x_end_ts)
                    visible_index = valid_index[mask_visible]
                    
                    if len(visible_index) == 0:
                        st.warning("В выбранном диапазоне X нет данных.")
                    else:
                        # === СЛАЙДЕР ВЕРТИКАЛЬНОЙ ЛИНИИ (в рамках видимого диапазона) ===
                        # Инициализируем cursor_time если не задан
                        if plot_area.get('cursor_time') is None:
                            plot_area['cursor_time'] = visible_index[len(visible_index) // 2]
                        
                        # Проверяем что cursor_time в видимом диапазоне
                        cursor_time = plot_area['cursor_time']
                        if cursor_time < x_start_ts or cursor_time > x_end_ts:
                            cursor_time = visible_index[len(visible_index) // 2]
                            plot_area['cursor_time'] = cursor_time
                        
                        # Находим текущий индекс курсора в visible_index
                        cursor_pos, _ = find_nearest_index_in_range(
                            visible_index, cursor_time, x_start_ts, x_end_ts
                        )
                        
                        # Применяем глобальный курсор если задан
                        if st.session_state.global_cursor_time is not None:
                            global_cursor = st.session_state.global_cursor_time
                            if x_start_ts <= global_cursor <= x_end_ts:
                                cursor_pos, cursor_time = find_nearest_index_in_range(
                                    visible_index, global_cursor, x_start_ts, x_end_ts
                                )
                                plot_area['cursor_time'] = cursor_time
                        
                        # ПУНКТ 4: Слайдер на всю ширину (без кнопки рядом)
                        ts_idx = st.slider(
                            "📍 Вертикальная линия (в видимом диапазоне)",
                            min_value=0,
                            max_value=len(visible_index) - 1,
                            value=min(cursor_pos, len(visible_index) - 1),
                            key=f"vline_slider_{i}",
                            help="Слайдер работает только в рамках текущего видимого диапазона X"
                        )
                        
                        # Обновляем cursor_time
                        ts = visible_index[ts_idx]
                        plot_area['cursor_time'] = ts
                        
                        # Отображаем текущую позицию
                        # Позиция линии и кнопка синхронизации на одной горизонтали
                        col_pos, col_sync = st.columns([3, 1])
                        with col_pos:
                            st.markdown(f"**📅 Позиция линии:** `{ts.strftime('%Y-%m-%d %H:%M:%S')}`")
                        with col_sync:
                            if st.button("🔄 Синхронизировать все", key=f"sync_{i}"):
                                st.session_state.global_cursor_time = ts
                                for pa in st.session_state.plot_areas:
                                    pa['cursor_time'] = ts
                                st.rerun()

                        # === ПОСТРОЕНИЕ ГРАФИКА ===
                        fig = px.line(
                            df_plot_num,
                            x=df_plot_num.index,
                            y=selected,
                            title=f"График #{plot_area['id']}",
                            render_mode="webgl"
                        )
                        
                        # Вертикальная линия курсора
                        fig.add_vline(x=ts, line_width=2, line_dash="dash", line_color="red")
                        
                        # Пользовательские маркеры
                        shapes = plot_area.get('shapes', [])
                        for shape in shapes:
                            if shape['type'] == 'vline':
                                fig.add_vline(x=shape['x'], line_dash=shape['dash'], line_color=shape['color'], line_width=1)
                            elif shape['type'] == 'hline':
                                fig.add_hline(y=shape['y'], line_dash=shape['dash'], line_color=shape['color'], line_width=1)
                        
                        # ПУНКТ 1 и 2: Layout с фиксированными диапазонами
                        fig.update_layout(
                            uirevision=f"plot_area_{plot_area['id']}",
                            height=600,
                            legend_title_text="Сигналы",
                            xaxis_title="Время",
                            yaxis_title="Значение",
                            margin=dict(l=20, r=20, t=40, b=20),
                            # ПУНКТ 1: X rangeslider на ПОЛНЫЙ диапазон данных
                            xaxis=dict(
                                range=[x_start_ts, x_end_ts],  # Видимый диапазон
                                rangeslider=dict(
                                    visible=True,
                                    thickness=0.08,
                                    bgcolor='#e0e0e0',
                                    range=[full_x_min, full_x_max]  # Полный диапазон для навигации
                                )
                            ),
                            # Y диапазон из настроек
                            yaxis=dict(
                                range=plot_area['y_range'],
                                fixedrange=False  # Позволяем зум по Y на графике
                            )
                        )
                        
                        st.plotly_chart(fig, use_container_width=True)

                        # === МАРКЕРЫ ===
                        with st.expander(f"📍 Добавить маркеры для графика #{plot_area['id']}"):
                            col_x, col_y = st.columns(2)
                            with col_x:
                                st.markdown("**Вертикальная линия (X)**")
                                x_date = st.date_input("Дата", value=ts.date(), key=f"x_date_{i}")
                                x_time = st.time_input("Время", value=ts.time(), key=f"x_time_{i}")
                                x_full = pd.Timestamp.combine(x_date, x_time)
                                if st.button("Добавить V-line", key=f"add_vline_{i}"):
                                    shapes.append({
                                        'type': 'vline',
                                        'x': x_full,
                                        'dash': 'dot',
                                        'color': 'blue'
                                    })
                                    plot_area['shapes'] = shapes
                                    st.success(f"Добавлена линия на {x_full}")
                                    st.rerun()
                            
                            with col_y:
                                st.markdown("**Горизонтальная линия (Y)**")
                                y_value = st.number_input("Значение Y", value=0.0, key=f"y_val_{i}")
                                if st.button("Добавить H-line", key=f"add_hline_{i}"):
                                    shapes.append({
                                        'type': 'hline',
                                        'y': y_value,
                                        'dash': 'dash',
                                        'color': 'green'
                                    })
                                    plot_area['shapes'] = shapes
                                    st.success(f"Добавлена линия на Y={y_value}")
                                    st.rerun()
                            
                            if shapes:
                                st.markdown("**Текущие маркеры:**")
                                for j, s in enumerate(shapes):
                                    if s['type'] == 'vline':
                                        st.text(f"  V-line: {s['x']} ({s['color']})")
                                    else:
                                        st.text(f"  H-line: Y={s['y']} ({s['color']})")
                                if st.button(f"🗑️ Очистить маркеры", key=f"clear_shapes_{i}"):
                                    plot_area['shapes'] = []
                                    st.rerun()

                        # === СТАТИСТИКА ===
                        nearest = df_plot_num.reindex(df_plot_num.index.union([ts])).sort_index()
                        nearest = nearest.ffill().loc[ts]

                        st.markdown("**📊 Статистика:**")
                        stats_df = compute_stats_numeric(df_plot)
                        if stats_df.empty:
                            st.info("Нет данных для статистики.")
                        else:
                            stats_view = stats_df.copy()
                            stats_view["value"] = nearest.reindex(stats_view.index)
                            stats_view["start"] = pd.to_datetime(stats_view["start"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
                            stats_view["end"] = pd.to_datetime(stats_view["end"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
                            st.dataframe(
                                stats_view.style.format(
                                    {
                                        "count": "{:.0f}",
                                        "min": "{:.6g}",
                                        "max": "{:.6g}",
                                        "mean": "{:.6g}",
                                        "std": "{:.6g}",
                                        "median": "{:.6g}",
                                        "value": "{:.6g}",
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

if df_all_signals is not None:
    with st.expander("ℹ️ Информация о данных"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Всего сигналов", len(df_all_signals.columns))
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