# visualizer_app.py — замени/обнови

import pandas as pd
import requests
import streamlit as st
import plotly.express as px
import numpy as np
import plotly.graph_objects as go
from typing import List  # добавь в начало файла если нет

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
    st.session_state.synthetic_computed = {}  # уже вычисленные синтетические сигналы
if "signal_groups" not in st.session_state:
    st.session_state.signal_groups = {"project": set(), "dependencies": set()}


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


# visualizer_app.py — замени функцию resolve_and_load_all_signals

def resolve_and_load_all_signals(input_signals: List[str]) -> tuple[pd.DataFrame | None, List[str], List[str]]:
    """
    Разворачивает зависимости и загружает все сигналы (базовые + синтетические).
    
    Returns:
        df_all: DataFrame со всеми сигналами
        found: список найденных сигналов
        not_found: список ненайденных сигналов
    """
    if not input_signals:
        return None, [], []
    
    try:
        # 1. Разворачиваем зависимости через API
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
        
        # === СОХРАНЯЕМ ГРУППИРОВКУ СИГНАЛОВ ===
        # Сигналы из текущего проекта (исходные входные)
        project_signals = set(input_signals)
        
        # Сигналы из зависимостей (все остальные)
        dependency_signals = set()
        for syn_name, syn_data in synthetic_signals.items():
            if syn_name not in project_signals:
                dependency_signals.add(syn_name)
            for dep in syn_data.get("dependencies", []):
                if dep not in project_signals:
                    dependency_signals.add(dep)
        
        # Также добавляем базовые сигналы, которые не из проекта
        for bs in base_signals:
            if bs not in project_signals:
                dependency_signals.add(bs)
        
        # Сохраняем в session_state для использования в сайдбаре
        st.session_state.signal_groups = {
            "project": project_signals,       # входные сигналы текущего проекта
            "dependencies": dependency_signals # сигналы из развёрнутых зависимостей
        }
        
        st.info(f"📊 Сигналов проекта: {len(project_signals)} | Из зависимостей: {len(dependency_signals)}")
        
        if synthetic_signals:
            with st.expander("🔗 Граф зависимостей синтетических сигналов"):
                for syn_name in computation_order:
                    deps = synthetic_signals[syn_name].get("dependencies", [])
                    marker = "📌" if syn_name in project_signals else "🔗"
                    st.text(f"  {marker} {syn_name} ← {deps}")
        
        # 2. Загружаем базовые сигналы
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
        
        # 3. Вычисляем синтетические сигналы в правильном порядке
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


# --- остальной код без изменений, начиная с get_all_signals_df ---

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

# --- синтетический сигнал из CODE (считаем один раз, потом не пересчитываем) ---
code_signal_name = st.session_state.code_signal_name
df_for_code = get_all_signals_df(exclude={code_signal_name} if code_signal_name else None)

# Ключ "какой CODE мы уже считали" (можно оставить просто CODE; session_token добавил на всякий)
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
    # если CODE исчез — удаляем синтетический сигнал и сбрасываем ключ
    if code_signal_name:
        st.session_state.derived_signals.pop(code_signal_name, None)
        st.session_state.selected_signals.discard(code_signal_name)
        st.session_state.code_signal_name = None
    st.session_state.code_key = None

# --- итоговый DataFrame со всеми сигналами ---
df_all_signals = get_all_signals_df()

with st.sidebar:
    st.header("Выбор сигналов")

    if df_all_signals is not None:
        available_signals = df_all_signals.columns.tolist()
        
        # Получаем группы сигналов
        signal_groups = st.session_state.get("signal_groups", {
            "project": set(available_signals),
            "dependencies": set()
        })
        
        project_signals = [s for s in available_signals if s in signal_groups.get("project", set())]
        dependency_signals = [s for s in available_signals if s in signal_groups.get("dependencies", set())]
        
        # === СИГНАЛЫ ПРОЕКТА ===
        if project_signals:
            st.subheader("📌 Сигналы проекта")
            for signal in project_signals:
                # Помечаем синтетические сигналы
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
        
        # === СИГНАЛЫ ИЗ ЗАВИСИМОСТЕЙ ===
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
        
        # === Быстрые действия ===
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
            st.session_state.plot_areas.append({"id": new_id, "signals": []})
            st.rerun()
        if col_b.button("❌ Очистить все"):
            st.session_state.plot_areas = []
            st.session_state.selected_signals = set()
            st.rerun()
    else:
        st.info("📥 Данные сигналов еще не загружены.")

if df_all_signals is not None and st.session_state.selected_signals:
    if not st.session_state.plot_areas:
        st.session_state.plot_areas.append(
            {"id": 1, "signals": list(st.session_state.selected_signals)}
        )

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

                # Для графика приводим к числам (поддержка запятых)
                df_plot_num = df_plot.apply(sanitize_numeric_column)

                valid_index = df_plot_num.dropna(how="all").index
                if len(valid_index) == 0:
                    st.warning("Нет числовых данных для выбранных сигналов.")
                else:
                    ts_idx = st.slider(
                        "Вертикальная линия (время)",
                        min_value=0,
                        max_value=len(valid_index) - 1,
                        value=len(valid_index) - 1,
                        key=f"vline_{i}",
                    )
                    ts = valid_index[ts_idx]

                    # график с вертикальной линией
                    fig = px.line(
                        df_plot_num,
                        x=df_plot_num.index,
                        y=selected,
                        title=f"График #{plot_area['id']}",
                        render_mode="webgl"
                    )
                    fig.add_vline(x=ts, line_width=2, line_dash="dash", line_color="red")
                    fig.update_layout(
                        uirevision=f"plot_area_{plot_area['id']}",
                        height=650,
                        legend_title_text="Сигналы",
                        xaxis_title="Время",
                        yaxis_title="Значение",
                        margin=dict(l=20, r=20, t=40, b=20),
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # значения на линии
                    nearest = df_plot_num.reindex(df_plot_num.index.union([ts])).sort_index()
                    nearest = nearest.ffill().loc[ts]

                    # статистика + колонка значений на линии
                    st.markdown("**📊 Статистика (по всему сигналу):**")
                    stats_df = compute_stats_numeric(df_plot)
                    if stats_df.empty:
                        st.info("Нет числовых данных для расчёта статистики.")
                    else:
                        stats_view = stats_df.copy()
                        stats_view["value"] = nearest.reindex(stats_view.index)
                        stats_view["start"] = (
                            pd.to_datetime(stats_view["start"], errors="coerce")
                            .dt.strftime("%Y-%m-%d %H:%M:%S")
                        )
                        stats_view["end"] = (
                            pd.to_datetime(stats_view["end"], errors="coerce")
                            .dt.strftime("%Y-%m-%d %H:%M:%S")
                        )
                        st.dataframe(
                            stats_view.style.format(
                                {
                                    "count": "{:.0f}",
                                    "min": "{:.6g}",
                                    "max": "{:.6g}",
                                    "mean": "{:.6g}",
                                    "std": "{:.6g}",
                                    "median": "{:.6g}",
                                    "value_at_line": "{:.6g}",
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
            st.metric("Всего сигналов (вкл. обрезанные/синтет.)", len(df_all_signals.columns))
        with col2:
            st.metric("Количество записей", len(df_all_signals))
        with col3:
            try:
                dt_range = df_all_signals.index.max() - df_all_signals.index.min()
                st.metric("Диапазон времени", str(dt_range).split(".")[0])
            except Exception:
                st.metric("Диапазон времени", "—")

if CODE:
    with st.expander("🧩 Сгенерированный код (оригинал)"):
        st.code(CODE, language="text")