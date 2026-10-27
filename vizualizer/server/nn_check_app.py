# nn_check_app.py — визуальная проверка обученной модели на всём диапазоне данных
import os
import sys
import json
import time
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
import logging
import io

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("nn_check")
print("[nn_check_app] module loaded", flush=True)


def compute_regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Регрессионные метрики. Возвращает dict с ключами-названиями."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true = y_true[mask]
    y_pred = y_pred[mask]
    n = len(y_true)

    if n == 0:
        return {"N": 0}

    residuals = y_pred - y_true
    abs_err = np.abs(residuals)

    mse = float(np.mean(residuals ** 2))
    mae = float(np.mean(abs_err))
    rmse = float(np.sqrt(mse))

    if n > 1:
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    else:
        r2 = float("nan")

    eps = 1e-9
    nz = np.abs(y_true) > eps
    mape = float(np.mean(abs_err[nz] / np.abs(y_true[nz])) * 100.0) if nz.any() else float("nan")

    denom = np.abs(y_true) + np.abs(y_pred)
    nz2 = denom > eps
    smape = float(np.mean(2.0 * abs_err[nz2] / denom[nz2]) * 100.0) if nz2.any() else float("nan")

    if n > 1:
        ev = 1.0 - float(np.var(residuals) / np.var(y_true)) if np.var(y_true) > 0 else float("nan")
    else:
        ev = float("nan")

    return {
        "N": int(n), "MSE": mse, "RMSE": rmse, "MAE": mae, "R²": r2, "EV": ev,
        "MAPE, %": mape, "SMAPE, %": smape, "Bias (mean err)": float(np.mean(residuals)),
        "Max abs err": float(np.max(abs_err)), "Median abs err": float(np.median(abs_err)),
        "Mean actual": float(np.mean(y_true)), "Mean pred": float(np.mean(y_pred)),
        "Std actual": float(np.std(y_true)), "Std pred": float(np.std(y_pred)),
    }


def restore_section_signs(name: str) -> str:
    return name.replace("__", "§§")


def metrics_to_dataframe(metrics: dict) -> pd.DataFrame:
    fmt = {
        "MSE": "{:.6g}", "RMSE": "{:.6g}", "MAE": "{:.6g}",
        "R²": "{:.4f}", "EV": "{:.4f}", "MAPE, %": "{:.2f}", "SMAPE, %": "{:.2f}",
        "Bias (mean err)": "{:.6g}", "Max abs err": "{:.6g}", "Median abs err": "{:.6g}",
        "Mean actual": "{:.6g}", "Mean pred": "{:.6g}", "Std actual": "{:.6g}", "Std pred": "{:.6g}",
    }
    rows = []
    for k, v in metrics.items():
        if k == "N":
            rows.append({"Метрика": k, "Значение": str(v)})
        elif isinstance(v, float) and np.isnan(v):
            rows.append({"Метрика": k, "Значение": "—"})
        else:
            rows.append({"Метрика": k, "Значение": fmt.get(k, "{}").format(v)})
    return pd.DataFrame(rows)


def align_pred_with_actuals(pred_df: pd.DataFrame, actuals_df: pd.DataFrame, tol_seconds: int = 1) -> pd.DataFrame:
    if pred_df.empty or actuals_df.empty:
        return pd.DataFrame(columns=["datetime", "actual", "pred", "status"])

    a = actuals_df.rename(columns={"value": "actual"}).copy()
    p = pred_df.rename(columns={"value": "pred"}).copy()
    a = a.sort_values("datetime")
    p = p.sort_values("datetime")

    merged = pd.merge_asof(p, a[["datetime", "actual"]], on="datetime", direction="nearest", tolerance=pd.Timedelta(seconds=tol_seconds))
    merged = merged.dropna(subset=["actual"])
    return merged[["datetime", "actual", "pred", "status"]]


def _model_signature(*paths: str) -> tuple:
    sig = []
    for p in paths:
        if os.path.isfile(p):
            st_ = os.stat(p)
            sig.append((p, st_.st_mtime_ns, st_.st_size))
        else:
            sig.append((p, None, None))
    return tuple(sig)


# ----------------------------------------------------------------------
# 1. Настройки страницы и параметры
# ----------------------------------------------------------------------
st.set_page_config(page_title="NN Check", layout="wide", initial_sidebar_state="expanded")

qp = st.query_params
config_name = qp.get("config", "")
project_code = qp.get("code", "")
model_dir = qp.get("model_dir", "")
y_labeler_id = qp.get("y_labeler_id", "")
y_kks = qp.get("y_kks", "").strip()
api_url = qp.get("api_url", "http://localhost:8000")

if not (project_code and model_dir):
    st.error("Не переданы обязательные параметры (code / model_dir).")
    st.stop()

# ----------------------------------------------------------------------
# 2. Подтягиваем рантайм
# ----------------------------------------------------------------------
if model_dir not in sys.path:
    sys.path.insert(0, model_dir)

try:
    from nn_inference_runtime import InferenceSession
except Exception as e:
    st.error(f"Не удалось импортировать nn_inference_runtime: {e}")
    st.stop()

config_path = qp.get("config_path", "").strip()
model_path = qp.get("model_path", "").strip()
meta_path = qp.get("meta_path", "").strip()

if not model_path: model_path = os.path.join(model_dir, f"{project_code}.keras")
if not meta_path: meta_path = os.path.join(model_dir, f"{project_code}_meta.json")
if not config_path: config_path = os.path.join(model_dir, f"{project_code}.config.json")

for p in (config_path, model_path, meta_path):
    if not os.path.isfile(p):
        st.error(f"Не найден файл: {p}")
        st.stop()

# ----------------------------------------------------------------------
# 3. Сессия и метаданные
# ----------------------------------------------------------------------
sig = _model_signature(config_path, model_path, meta_path)

@st.cache_resource(show_spinner=False)
def _make_session(cfg, mdl, mt, _sig):
    return InferenceSession.from_config_file(config_path=cfg, model_path=mdl, metadata_path=mt)

session = _make_session(config_path, model_path, meta_path, sig)

if "model_signature" not in st.session_state:
    st.session_state.model_signature = None

if st.session_state.model_signature != sig:
    st.session_state.model_signature = sig
    st.session_state.pop("inference_result", None)
    st.session_state.pop("inference_log", None)
    st.session_state.pop("loaded_signals", None) # Сбрасываем загруженные сигналы

meta = session.metadata()
dt_fmt = meta["input_contract"]["datetime_format"]

with open(config_path, "r", encoding="utf-8") as f:
    _cfg = json.load(f)
startup_info = _cfg.get("startup_condition") or {}
startup_kks = startup_info.get("kks")

# --- Инициализация Session State ---
if "loaded_signals" not in st.session_state:
    st.session_state.loaded_signals = {}
if "custom_plots" not in st.session_state:
    st.session_state.custom_plots = []

# ----------------------------------------------------------------------
# 4. Загрузка сигналов (функция)
# ----------------------------------------------------------------------
def load_signal_from_api(name: str, config_name: str, api_url: str, dt_fmt: str) -> tuple[np.ndarray, str]:
    candidates = [name]
    restored = restore_section_signs(name)
    if restored != name:
        candidates.append(restored)

    last_error = None
    for candidate in candidates:
        try:
            url = f"{api_url}/api/signal-data?config={requests.utils.quote(config_name)}&_t={time.time()}"
            r = requests.post(url, json={"signal_names": [candidate], "format": "json"}, timeout=120)
            r.raise_for_status()
            payload = r.json()
            records = (payload.get("data") or {}).get(candidate) or []
            sources = payload.get("sources") or {}
            source_file = sources.get(candidate, "unknown")
            
            if not records:
                last_error = f"пусто для '{candidate}'"
                continue

            df = pd.DataFrame(records)
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"]).sort_values("datetime")

            raw_val = df["value"]
            if raw_val.dtype == object:
                raw_val = raw_val.astype(str).str.replace(",", ".", regex=False).str.replace("\u00a0", "", regex=False).str.strip()
            df["value"] = pd.to_numeric(raw_val, errors="coerce")
            df = df.reset_index(drop=True)

            dt_str = df["datetime"].dt.strftime(dt_fmt)
            mask = dt_str.notna() 
            
            return np.column_stack([dt_str[mask].to_numpy(), df["value"][mask].to_numpy(dtype=float)]), source_file
            
        except Exception as e:
            last_error = str(e)
            continue
    raise ValueError(f"Не удалось загрузить '{name}' (пробовали {candidates}): {last_error}")


# ----------------------------------------------------------------------
# 5. SIDEBAR (Управление)
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Управление")
    
    # Кнопки запуска и перезагрузки
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button("▶ Запустить инференс", type="primary", use_container_width=True):
            with st.spinner("Загрузка сигналов и расчет модели..."):
                # 1. Собираем все нужные KKS
                needed_signals = [inp["name"] for inp in meta["inputs"]]
                if y_kks: 
                    needed_signals.append(y_kks)
                
                # Читаем startup_kks
                if startup_kks: 
                    needed_signals.append(startup_kks)
                
                # Убираем дубликаты
                needed_signals = list(dict.fromkeys(needed_signals))
                
                # 2. Загружаем сигналы (только те, которых нет в кэше)
                new_loaded = dict(st.session_state.loaded_signals or {})
                for sig in needed_signals:
                    if sig not in new_loaded:
                        try:
                            arr, src = load_signal_from_api(sig, config_name, api_url, dt_fmt)
                            new_loaded[sig] = (arr, src)
                        except Exception as e:
                            st.warning(f"⚠️ Не удалось загрузить {sig}: {e}")
                
                st.session_state.loaded_signals = new_loaded
                
                # 3. Проверяем, что все входы модели на месте
                missing = [inp["name"] for inp in meta["inputs"] if inp["name"] not in new_loaded]
                if missing:
                    st.error(f"Ошибка: не удалось загрузить входные сигналы {missing}")
                else:
                    input_data = {k: new_loaded[k][0] for k in [i["name"] for i in meta["inputs"]]}
                    log_path = os.path.join(model_dir, f"{project_code}.inference_log.json")
                    try:
                        result = session.predict(input_data, log_path=log_path)
                    except TypeError:
                        result = session.predict(input_data)
                    
                    st.session_state["inference_result"] = result
                    st.session_state["inference_log"] = log_path
                    st.success("✅ Инференс завершен!")

    with btn_col2:
        if st.button("🔄 Перезагрузить", use_container_width=True):
            _make_session.clear()
            st.session_state.pop("inference_result", None)
            st.session_state.pop("inference_log", None)
            st.session_state.pop("loaded_signals", None)
            st.session_state.model_signature = None
            st.rerun()

    st.divider()
    
    # Кнопка добавления кастомного графика
    if st.button("➕ Добавить график", use_container_width=True):
        st.session_state.custom_plots.append(len(st.session_state.custom_plots))
        st.rerun()

# ----------------------------------------------------------------------
# 6. Основная область
# ----------------------------------------------------------------------
st.title(f"🔍 Проверка модели: {project_code}")
st.caption(meta.get("project_description", [""])[0] if meta.get("project_description") else "")

result = st.session_state.get("inference_result")
log_path = st.session_state.get("inference_log")

# Если инференс еще не запущен
if not result:
    pass#st.info("👈 Загрузите входные сигналы слева и нажмите «Запустить инференс».")
else:
    output_kks = list(result.keys())
    
    # Загружаем лог статусов
    statuses = {}
    log_data = {}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)
        for kks, items in (log_data.get("outputs") or {}).items():
            for item in items:
                ts = item.get("output_timestamp")
                statuses[(kks, ts)] = item.get("status", "OK")
    except Exception as e:
        st.warning(f"Не удалось прочитать лог: {e}")

    # Подготавливаем start_df, если он загружен
    start_df = None
    if startup_kks and startup_kks in st.session_state.loaded_signals:
        arr_start, _ = st.session_state.loaded_signals[startup_kks]
        start_df = pd.DataFrame({
            "datetime": pd.to_datetime(arr_start[:, 0], format=dt_fmt, errors="coerce"),
            "startup": pd.to_numeric(arr_start[:, 1], errors="coerce"),
        }).dropna(subset=["datetime"]).sort_values("datetime")
        start_df["startup"] = start_df["startup"].fillna(0)

    for kks in output_kks:
        arr = result[kks]
        if arr is None or len(arr) == 0: continue

        pred_df = pd.DataFrame({
            "datetime": pd.to_datetime(arr[:, 0], format=dt_fmt, errors="coerce"),
            "value": pd.to_numeric(arr[:, 1], errors="coerce"),
        }).dropna(subset=["datetime"]).sort_values("datetime")

        pred_df["status"] = pred_df["datetime"].dt.strftime(dt_fmt).map(lambda ts: statuses.get((kks, ts), "OK"))
        
        # Условие запуска
        if start_df is not None:
            merged_start = pd.merge_asof(pred_df[['datetime']], start_df, on='datetime', direction='backward')
            start_values = merged_start['startup'].fillna(0).values
            pred_df["startup_cond"] = start_values
            startup_ok = start_values > 0.5
            pred_df.loc[~startup_ok, "status"] = "WARNING"
        else:
            pred_df["startup_cond"] = np.nan

        # ---------- Главный график ----------
        st.subheader(f"Выход модели: {kks}")
        fig = go.Figure()

        if y_kks and y_kks in st.session_state.loaded_signals:
            arr_y, _ = st.session_state.loaded_signals[y_kks]
            actuals_df = pd.DataFrame({
                "datetime": pd.to_datetime(arr_y[:, 0], errors="coerce", format=dt_fmt),
                "value": pd.to_numeric(arr_y[:, 1], errors="coerce"),
            }).dropna(subset=["datetime"])
            
            fig.add_trace(go.Scatter(
                x=actuals_df["datetime"], y=actuals_df["value"],
                mode="lines", name="Факт (Y)", line=dict(color="#4a90d9", width=1.5),
                connectgaps=False, hovertemplate="Факт: %{y:.4g}<extra></extra>",
            ))
        else:
            actuals_df = pd.DataFrame()

        ok_mask = pred_df["status"] == "OK"
        warn_mask = ~ok_mask
        hover_tmpl = "Прогноз: %{y:.4g}"
        if startup_kks:
            hover_tmpl += "<br>Усл. запуска: %{customdata:.2f}"
        
        if warn_mask.any():
            fig.add_trace(go.Scatter(x=pred_df.loc[warn_mask, "datetime"], y=pred_df.loc[warn_mask, "value"], mode="markers", name="Прогноз (WARNING)", marker=dict(color="#ef4444", size=6), customdata=pred_df.loc[warn_mask, "startup_cond"], hovertemplate=hover_tmpl + "<extra>WARNING</extra>"))
        if ok_mask.any():
            fig.add_trace(go.Scatter(x=pred_df.loc[ok_mask, "datetime"], y=pred_df.loc[ok_mask, "value"], mode="markers", name="Прогноз (OK)", marker=dict(color="#10b981", size=5), customdata=pred_df.loc[ok_mask, "startup_cond"], hovertemplate=hover_tmpl + "<extra>OK</extra>"))
        # --- Y-диапазон по факту (ЗАЩИТА ОТ ВЫБРОСОВ МОДЕЛИ) ---
        y_range = None
        if not actuals_df.empty:
            y_valid = actuals_df["value"].dropna()
            if not y_valid.empty:
                y_min = float(y_valid.min())
                y_max = float(y_valid.max())
                span = y_max - y_min
                if span <= 0: span = max(abs(y_max), 1.0) * 0.1
                pad = span * 0.05
                y_range = [y_min - pad, y_max + pad]

        fig.update_layout(
            title=f"Выход {kks}",
            xaxis_title="Время", yaxis_title="Значение",
            height=850, hovermode="x unified", template="plotly_white",
            uirevision="nn-check",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=40, r=20, t=60, b=40),
        )

        if y_range is not None:
            fig.update_yaxes(range=y_range)

        st.plotly_chart(fig, use_container_width=True, key=f"main_chart_{kks}")
        # =====================================================================
        # --- ВИЗУАЛИЗАЦИЯ УСЛОВИЯ ЗАПУСКА ---
        # =====================================================================
        if start_df is not None and not start_df.empty:
            fig_cond = go.Figure()
            
            # 1. Исходный сигнал с сервера (показываем как ступеньки)
            fig_cond.add_trace(go.Scatter(
                x=start_df["datetime"], 
                y=start_df["startup"],
                mode="lines", 
                name="Исходный (из файла)",
                line=dict(color="#8b5cf6", width=2, shape="hv")
            ))
            
            # 2. Сигнал, который реально видит модель после merge_asof
            fig_cond.add_trace(go.Scatter(
                x=pred_df["datetime"], 
                y=pred_df["startup_cond"],
                mode="markers", 
                name="Синхронизировано с прогнозом",
                marker=dict(color="#f59e0b", size=4, symbol="x")
            ))

            fig_cond.update_layout(
                title=f"Сигнал условия запуска: {startup_kks}",
                xaxis_title="Время", 
                yaxis_title="Значение",
                height=300,
                margin=dict(l=40, r=20, t=40, b=40),
                yaxis=dict(range=[-0.1, 1.1], tickvals=[0, 1], ticktext=["0 (Стоп)", "1 (Работа)"]),
                hovermode="x unified",
                template="plotly_white",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_cond, use_container_width=True, key=f"chart_cond_{kks}")
        # =====================================================================

                # ---------- Метрики ----------
        if not actuals_df.empty:
            merged = align_pred_with_actuals(pred_df, actuals_df, tol_seconds=1)
            if merged.empty:
                st.warning("Не удалось сопоставить предсказания с фактами по времени — метрики не рассчитаны.")
            else:
                # 1. Общие метрики (по всем точкам)
                overall = compute_regression_metrics(merged["actual"].values, merged["pred"].values)
                df_overall = metrics_to_dataframe(overall)

                # 2. Разбивка по статусам
                df_by_status_rows = []
                for status_val in ("OK", "WARNING"):
                    sub = merged[merged["status"] == status_val]
                    if sub.empty: continue
                    m = compute_regression_metrics(sub["actual"].values, sub["pred"].values)
                    row = {"Статус": status_val, "N": m["N"]}
                    for key in ("MSE", "RMSE", "MAE", "R²", "SMAPE, %", "Max abs err", "Bias (mean err)"):
                        v = m.get(key, float("nan"))
                        if isinstance(v, float) and np.isnan(v): row[key] = "—"
                        elif key in ("R²",): row[key] = f"{v:.4f}"
                        elif key in ("SMAPE, %",): row[key] = f"{v:.2f}"
                        else: row[key] = f"{v:.6g}"
                    df_by_status_rows.append(row)
                df_by_status = pd.DataFrame(df_by_status_rows)

                st.markdown("##### 📊 Метрики")
                col_left, col_right = st.columns([1, 2])
                with col_left:
                    st.markdown("**Общие метрики**")
                    st.dataframe(df_overall, use_container_width=True, hide_index=True)
                with col_right:
                    st.markdown("**Разбивка по статусам**")
                    if df_by_status.empty:
                        st.info("Нет данных для разбивки по статусам.")
                    else:
                        st.dataframe(df_by_status, use_container_width=True, hide_index=True)

                # =====================================================================
                # --- СОХРАНЕНИЕ МЕТРИК В ПРОЕКТ ---
                # =====================================================================
                if st.button("💾 Сохранить метрики (для статуса OK) в проект", key=f"save_metrics_{kks}"):
                    merged_ok = merged[merged["status"] == "OK"]
                    if merged_ok.empty:
                        st.error("Нет точек со статусом OK для расчета метрик.")
                    else:
                        m_ok = compute_regression_metrics(merged_ok["actual"].values, merged_ok["pred"].values)
                        residuals_ok = merged_ok["pred"] - merged_ok["actual"]
                        abs_residuals_ok = residuals_ok.abs()
                        
                        metrics_payload = {
                            "mse": m_ok.get("MSE"),
                            "rmse": m_ok.get("RMSE"),
                            "mae": m_ok.get("MAE"),
                            "r2": m_ok.get("R²"),
                            "bias": float(residuals_ok.mean()) if not residuals_ok.empty else None,
                            "std": float(residuals_ok.std()) if not residuals_ok.empty else None,
                            "p95": float(abs_residuals_ok.quantile(0.95)) if not abs_residuals_ok.empty else None,
                            "p99": float(abs_residuals_ok.quantile(0.99)) if not abs_residuals_ok.empty else None,
                        }
                        
                        possible_filenames = [
                            f"{project_code}_neural.json",
                            f"{project_code}_neural_network.json",
                            f"{project_code}_neural_template.json"
                        ]
                        
                        update_url = f"{api_url}/api/nn/project/update-metrics"
                        saved = False
                        
                        for fname in possible_filenames:
                            try:
                                r = requests.post(
                                    update_url, 
                                    json={"filename": fname, "config": config_name, "metrics": metrics_payload},
                                    timeout=10
                                )
                                if r.status_code == 200:
                                    st.success(f"✅ Метрики для {kks} сохранены в файл `{fname}`!")
                                    st.info("Обновите страницу редактора проекта, чтобы увидеть изменения.")
                                    saved = True
                                    break
                                elif r.status_code != 404:
                                    st.error(f"Ошибка {r.status_code}: {r.text}")
                                    break
                            except Exception as e:
                                st.error(f"Ошибка сети: {e}")
                                break
                                
                        if not saved:
                            st.error(f"Не удалось найти файл проекта. Проверены имена: {', '.join(possible_filenames)}")

                # =====================================================================
                # --- АНАЛИЗ ОСТАТКОВ (ТОЛЬКО ДЛЯ СТАТУСА OK) ---
                # =====================================================================
                merged_ok = merged[merged["status"] == "OK"].copy()
                if not merged_ok.empty:
                    st.divider()
                    st.markdown("##### 📉 Анализ остатков (ошибок) для статуса OK")
                    
                    residuals = merged_ok["pred"] - merged_ok["actual"]
                    abs_residuals = residuals.abs()

                    mean_res = residuals.mean()
                    std_res = residuals.std() if len(residuals) > 1 else 0.0
                    p95_abs = abs_residuals.quantile(0.95)
                    p99_abs = abs_residuals.quantile(0.99)

                    col_hist, col_box = st.columns([2, 1])
                    
                    with col_hist:
                        fig_hist = go.Figure(data=[go.Histogram(x=residuals, nbinsx=50, marker_color='#4a90d9', opacity=0.75)])
                        fig_hist.update_layout(title="Гистограмма распределения остатков (Прогноз - Факт)", xaxis_title="Остаток", yaxis_title="Количество", height=400, template="plotly_white", margin=dict(l=20, r=20, t=40, b=20))
                        st.plotly_chart(fig_hist, use_container_width=True, key=f"hist_res_{kks}")

                    with col_box:
                        fig_box = go.Figure(data=[go.Box(y=residuals, name="Остатки", marker_color='#ef4444', boxpoints='outliers')])
                        fig_box.update_layout(title="Диаграмма с усами", yaxis_title="Остаток", height=400, template="plotly_white", margin=dict(l=20, r=20, t=40, b=20))
                        st.plotly_chart(fig_box, use_container_width=True, key=f"box_res_{kks}")

                    st.markdown("**Статистика остатков:**")
                    col_stats, col_custom = st.columns([2, 1])
                    
                    with col_stats:
                        stats_data = [
                            {"Метрика": "Среднее (Bias)", "Значение": f"{mean_res:.4g}"},
                            {"Метрика": "Стд. отклонение", "Значение": f"{std_res:.4g}"},
                            {"Метрика": "95-й перцентиль (по модулю)", "Значение": f"{p95_abs:.4g}"},
                            {"Метрика": "99-й перцентиль (по модулю)", "Значение": f"{p99_abs:.4g}"},
                        ]
                        st.dataframe(pd.DataFrame(stats_data), use_container_width=True, hide_index=True)

                    with col_custom:
                        st.markdown("**Пользовательский перцентиль:**")
                        custom_p = st.number_input("Введите перцентиль (0-100)", min_value=0.0, max_value=100.0, value=90.0, step=0.5, key=f"custom_p_input_{kks}")
                        
                        state_key = f"custom_p_res_{kks}"
                        if state_key not in st.session_state:
                            st.session_state[state_key] = None
                            
                        if st.button("Рассчитать", key=f"calc_custom_p_{kks}"):
                            st.session_state[state_key] = abs_residuals.quantile(custom_p / 100.0)
                        
                        if st.session_state[state_key] is not None:
                            st.success(f"{custom_p}-й перцентиль (по модулю): **{st.session_state[state_key]:.4g}**")
                # =====================================================================

                with st.expander("Экспорт метрик"):
                    csv_overall = df_overall.to_csv(index=False).encode("utf-8-sig")
                    st.download_button("Скачать метрики (CSV)", data=csv_overall, file_name=f"{kks}_metrics.csv", mime="text/csv", key=f"dl_{kks}")

        # =====================================================================
        # --- СОХРАНЕНИЕ СИГНАЛА В АРХИВ ---
        # =====================================================================
        if st.button(f"📥 Сохранить сигнал {kks} в архив", key=f"btn_save_archive_{kks}"):
            try:
                with st.spinner("Сохранение..."):
                    df_to_save = pd.DataFrame({
                        "DATE": pred_df["datetime"].dt.strftime("%d.%m.%Y"),
                        "TIME": pred_df["datetime"].dt.strftime("%H:%M:%S"),
                        kks: pred_df["value"]
                    })
                    csv_buffer = io.BytesIO()
                    df_to_save.to_csv(csv_buffer, sep=";", index=False, encoding="utf-8")
                    csv_buffer.seek(0)
                    
                    filename = f"{kks}.csv"
                    upload_url = f"{api_url}/api/upload-signal-csv?config={requests.utils.quote(config_name)}"
                    
                    r = requests.post(upload_url, files={"file": (filename, csv_buffer, "text/csv")}, timeout=120)
                    r.raise_for_status()
                st.success(f"✅ Сигнал **{kks}** успешно сохранён в архив!")
                st.info("Теперь вы можете использовать этот KKS в визуализаторе или как входной сигнал для новых правил.")
            except Exception as e:
                st.error(f"❌ Ошибка сохранения сигнала: {e}")
        # =====================================================================

        st.divider()

# ----------------------------------------------------------------------
# 7. КАСТОМНЫЕ ГРАФИКИ
# ----------------------------------------------------------------------
if st.session_state.custom_plots:
    st.divider()
    st.header("📈 Дополнительные графики")

for i, plot_id in enumerate(st.session_state.custom_plots):
    st.markdown(f"**График #{i+1}**")
    
    col_sel, col_btn = st.columns([5, 1])
    with col_sel:
        available = list((st.session_state.loaded_signals or {}).keys())
        if not available:
            st.info("Нет загруженных сигналов. Нажмите 'Запустить инференс' в левой панели.")
            selected = []
        else:
            # Теперь по умолчанию пусто (default=[])
            selected = st.multiselect(
                "Выберите сигналы", 
                available, 
                default=[],  # <--- ИСПРАВЛЕНИЕ: пустой список по умолчанию
                key=f"custom_sel_{plot_id}"
            )

    with col_btn:
        st.write("")
        if st.button("🗑️", key=f"del_plot_{plot_id}", help="Удалить этот график"):
            st.session_state.custom_plots.remove(plot_id)
            st.rerun()

    if selected:
        fig_custom = go.Figure()
        for sig_name in selected:
            arr, _ = st.session_state.loaded_signals[sig_name]
            # <--- ИСПРАВЛЕНИЕ: добавлен format=dt_fmt
            df_sig = pd.DataFrame({
                "datetime": pd.to_datetime(arr[:, 0], format=dt_fmt, errors="coerce"),
                "value": pd.to_numeric(arr[:, 1], errors="coerce")
            }).dropna(subset=["datetime"])
            
            fig_custom.add_trace(go.Scatter(
                x=df_sig["datetime"], 
                y=df_sig["value"], 
                mode="lines", 
                name=sig_name,
                connectgaps=False
            ))
        
        fig_custom.update_layout(
            height=400, 
            hovermode="x unified", 
            template="plotly_white", 
            margin=dict(l=40, r=20, t=20, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_custom, use_container_width=True, key=f"custom_chart_{plot_id}")
    st.divider()