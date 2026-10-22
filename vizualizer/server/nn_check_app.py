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

    # R²
    if n > 1:
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    else:
        r2 = float("nan")

    # MAPE — исключаем |y_true| < eps, чтобы не делить на 0
    eps = 1e-9
    nz = np.abs(y_true) > eps
    mape = float(np.mean(abs_err[nz] / np.abs(y_true[nz])) * 100.0) if nz.any() else float("nan")

    # SMAPE — симметричная, безопаснее к нулям
    denom = np.abs(y_true) + np.abs(y_pred)
    nz2 = denom > eps
    smape = float(np.mean(2.0 * abs_err[nz2] / denom[nz2]) * 100.0) if nz2.any() else float("nan")

    # EV (Explained Variance)
    if n > 1:
        ev = 1.0 - float(np.var(residuals) / np.var(y_true)) if np.var(y_true) > 0 else float("nan")
    else:
        ev = float("nan")

    return {
        "N": int(n),
        "MSE": mse,
        "RMSE": rmse,
        "MAE": mae,
        "R²": r2,
        "EV": ev,
        "MAPE, %": mape,
        "SMAPE, %": smape,
        "Bias (mean err)": float(np.mean(residuals)),
        "Max abs err": float(np.max(abs_err)),
        "Median abs err": float(np.median(abs_err)),
        "Mean actual": float(np.mean(y_true)),
        "Mean pred": float(np.mean(y_pred)),
        "Std actual": float(np.std(y_true)),
        "Std pred": float(np.std(y_pred)),
    }


def restore_section_signs(name: str) -> str:
    """Обратная нормализация: '§§' -> '__' применялась при экспорте."""
    return name.replace("__", "§§")


def metrics_to_dataframe(metrics: dict) -> pd.DataFrame:
    """Превращает dict метрик в двухколоночную таблицу."""
    fmt = {
        "MSE": "{:.6g}", "RMSE": "{:.6g}", "MAE": "{:.6g}",
        "R²": "{:.4f}", "EV": "{:.4f}",
        "MAPE, %": "{:.2f}", "SMAPE, %": "{:.2f}",
        "Bias (mean err)": "{:.6g}",
        "Max abs err": "{:.6g}", "Median abs err": "{:.6g}",
        "Mean actual": "{:.6g}", "Mean pred": "{:.6g}",
        "Std actual": "{:.6g}", "Std pred": "{:.6g}",
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
    """Inner-merge по datetime с допуском tol_seconds."""
    if pred_df.empty or actuals_df.empty:
        return pd.DataFrame(columns=["datetime", "actual", "pred", "status"])

    a = actuals_df.rename(columns={"value": "actual"}).copy()
    p = pred_df.rename(columns={"value": "pred"}).copy()

    a = a.sort_values("datetime")
    p = p.sort_values("datetime")

    merged = pd.merge_asof(
        p, a[["datetime", "actual"]],
        on="datetime",
        direction="nearest",
        tolerance=pd.Timedelta(seconds=tol_seconds)
    )
    merged = merged.dropna(subset=["actual"])
    return merged[["datetime", "actual", "pred", "status"]]


def _model_signature(*paths: str) -> tuple:
    """Сигнатура файлов для инвалидации кэша."""
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
st.set_page_config(
    page_title="NN Check",
    layout="wide",
    initial_sidebar_state="collapsed",
)
qp = st.query_params
config_name = qp.get("config", "")
project_code = qp.get("code", "")
model_dir = qp.get("model_dir", "")
y_labeler_id = qp.get("y_labeler_id", "")
y_kks = qp.get("y_kks", "").strip()
api_url = qp.get("api_url", "http://localhost:8000")

if not (project_code and model_dir and y_labeler_id):
    st.error("Не переданы обязательные параметры (code / model_dir / y_labeler_id).")
    st.stop()

print(f"[nn_check_app] params: config={config_name!r} code={project_code!r} "
      f"model_dir={model_dir!r} y_labeler_id={y_labeler_id!r} api_url={api_url!r}", flush=True)

# ----------------------------------------------------------------------
# 2. Подтягиваем рантайм
# ----------------------------------------------------------------------
if model_dir not in sys.path:
    sys.path.insert(0, model_dir)

try:
    from nn_inference_runtime import InferenceSession
    print(f"[nn_check_app] nn_inference_runtime imported from {model_dir}", flush=True)
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
    return InferenceSession.from_config_file(
        config_path=cfg, model_path=mdl, metadata_path=mt
    )

t0 = time.time()
print("[nn_check_app] creating InferenceSession...", flush=True)
session = _make_session(config_path, model_path, meta_path, sig)
print(f"[nn_check_app] InferenceSession created in {time.time()-t0:.2f}s", flush=True)

if "model_signature" not in st.session_state:
    st.session_state.model_signature = None

if st.session_state.model_signature != sig:
    print(f"[nn_check_app] model signature changed -> clearing cached results", flush=True)
    st.session_state.model_signature = sig
    st.session_state.pop("inference_result", None)
    st.session_state.pop("inference_log", None)

meta = session.metadata()
st.title(f"🔍 Проверка модели: {project_code}")
st.caption(meta.get("project_description", [""])[0] if meta.get("project_description") else "")

dt_fmt = meta["input_contract"]["datetime_format"]

# ----------------------------------------------------------------------
# 4. Загрузка сырых входных сигналов
# ----------------------------------------------------------------------
def load_signal_from_api(name: str, config_name: str, api_url: str, dt_fmt: str) -> tuple[np.ndarray, str]:
    """
    Возвращает (ndarray, source_file).
    Пробует найти сигнал сначала по исходному имени, потом с §§.
    """
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
            if "datetime" not in df.columns or "value" not in df.columns:
                last_error = f"некорректный формат ответа для '{candidate}'"
                continue

            # === ДИАГНОСТИКА ===
            print(f"[DIAG] '{name}' (candidate '{candidate}'):", flush=True)
            print(f"[DIAG]   records from server: {len(records)}", flush=True)
            
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            print(f"[DIAG]   valid datetime: {df['datetime'].notna().sum()}", flush=True)
            
            df = df.dropna(subset=["datetime"]).sort_values("datetime")
            print(f"[DIAG]   after dropna(datetime): {len(df)}", flush=True)
            # ===================

            # Нормализация value (запятые)
            raw_val = df["value"]
            if raw_val.dtype == object:
                raw_val = (
                    raw_val.astype(str)
                           .str.replace(",", ".", regex=False)
                           .str.replace("\u00a0", "", regex=False)
                           .str.strip()
                )
            df["value"] = pd.to_numeric(raw_val, errors="coerce")
            df = df.reset_index(drop=True)

            #df = df.dropna(subset=["value"]).reset_index(drop=True)

            dt_str = df["datetime"].dt.strftime(dt_fmt)
            mask = dt_str.notna() 
            
            print(f"[nn_check_app]   '{name}' -> resolved as '{candidate}' "
                  f"(source: {source_file}, {mask.sum()} rows)", flush=True)
            print(f"[DIAG]   after mask: {mask.sum()} (len(df)={len(df)})", flush=True)
                  
            return np.column_stack([
                dt_str[mask].to_numpy(),
                df["value"][mask].to_numpy(dtype=float),
            ]), source_file
            
        except Exception as e:
            last_error = str(e)
            continue

    raise ValueError(f"Не удалось загрузить сигнал '{name}' (пробовали {candidates}): {last_error}")

with st.spinner("Загружаю сырые входные сигналы из архива..."):
    input_signals = {}
    missing = []
    for inp in meta["inputs"]:
        name = inp["name"]
        try:
            arr, _src = load_signal_from_api(name, config_name, api_url, dt_fmt)
            input_signals[name] = arr
        except Exception as e:
            missing.append(f"{name}: {e}")

if missing:
    st.error("Не удалось загрузить входные сигналы:\n" + "\n".join(missing))
    st.stop()

st.success(f"Загружено входных сигналов: {len(input_signals)}")

# ----------------------------------------------------------------------
# 5. Фактические значения Y (сырой сигнал)
# ----------------------------------------------------------------------
if not y_kks:
    st.warning("⚠️ Не передан KKS Y-сигнала (y_kks) — график будет без факта.")
    actuals_df = pd.DataFrame(columns=["datetime", "value"])
else:
    try:
        with st.spinner(f"Загружаю Y-сигнал «{y_kks}»..."):
            candidates = [y_kks, restore_section_signs(y_kks)]
            url = f"{api_url}/api/signal-data?config={requests.utils.quote(config_name)}&_t={time.time()}"
            r = requests.post(url, json={"signal_names": candidates, "format": "json"}, timeout=120)
            r.raise_for_status()
            payload = r.json()

        data_map = payload.get("data") or {}
        records = []
        for cand in candidates:
            if data_map.get(cand):
                records = data_map[cand]
                break

        if not records:
            raise ValueError(f"пусто (пробовали {candidates})")

        # Без dropna по value, как ты и просил
        actuals_df = pd.DataFrame(records)
        actuals_df["value"] = pd.to_numeric(actuals_df["value"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
        actuals_df["datetime"] = pd.to_datetime(actuals_df["datetime"], errors="coerce")
        actuals_df = actuals_df.sort_values("datetime").reset_index(drop=True)

        st.success(f"Y загружен: **{y_kks}** — {len(actuals_df)} точек (непустых: {int(actuals_df['value'].notna().sum())})")
    except Exception as e:
        st.warning(f"⚠️ Не удалось загрузить Y '{y_kks}': {e}")
        actuals_df = pd.DataFrame(columns=["datetime", "value"])

# ----------------------------------------------------------------------
# 6. Запуск инференса
# ----------------------------------------------------------------------
log_path = os.path.join(model_dir, f"{project_code}.inference_log.json")

if st.button("▶ Запустить инференс на всём диапазоне", type="primary"):
    with st.spinner("Модель считает предсказания..."):
        try:
            result = session.predict(input_signals, log_path=log_path)
        except TypeError:
            result = session.predict(input_signals)
        st.session_state["inference_result"] = result
        st.session_state["inference_log"] = log_path

if st.button("🔄 Перезагрузить модель", key="reload_model"):
    _make_session.clear()
    st.session_state.pop("inference_result", None)
    st.session_state.pop("inference_log", None)
    st.session_state.model_signature = None
    st.rerun()

result = st.session_state.get("inference_result")

# ----------------------------------------------------------------------
# 7. Отрисовка результатов и метрик
# ----------------------------------------------------------------------
if result:
    output_kks = list(result.keys())
    st.subheader("Результаты инференса")

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
        st.warning(f"Не удалось прочитать лог инференса: {e}")

    # Читаем конфиг один раз
    with open(config_path, "r", encoding="utf-8") as f:
        _cfg = json.load(f)

    startup_info = _cfg.get("startup_condition") or {}
    startup_kks = startup_info.get("kks")
    start_df = None

    if startup_kks:
        try:
            # Загружаем один раз для всех выходов
            arr_start, src_file = load_signal_from_api(startup_kks, config_name, api_url, dt_fmt)
            st.caption(f"ℹ️ Условие запуска **{startup_kks}** загружено из файла: `{src_file}` (точек: {len(arr_start)})")
            
            start_df = pd.DataFrame({
                "datetime": pd.to_datetime(arr_start[:, 0], format=dt_fmt, errors="coerce"), # <--- ВОТ ТУТ ДОБАВИЛИ format=dt_fmt
                "startup": pd.to_numeric(arr_start[:, 1], errors="coerce"),
            })
            
            # Удаляем только те строки, где дата не распарсилась (NaT)
            start_df = start_df.dropna(subset=["datetime"]).sort_values("datetime")
            
            # А пустые значения в самом сигнале заменяем на 0
            start_df["startup"] = start_df["startup"].fillna(0)

            # ====== ДИАГНОСТИКА ======
            with st.expander("🔍 Отладка: что загружено для условия запуска", expanded=False):
                st.write(f"**KKS:** `{startup_kks}`")
                st.write(f"**Файл-источник:** `{src_file}`")
                st.write(f"**Всего точек:** {len(start_df)}")
                st.write(f"**Диапазон времени:** {start_df['datetime'].min()} — {start_df['datetime'].max()}")
                st.write(f"**Уникальные значения:** {sorted(start_df['startup'].unique())}")
                st.write("**Первые 10 строк:**")
                st.dataframe(start_df.head(10))
                st.write("**Последние 10 строк:**")
                st.dataframe(start_df.tail(10))
            # =========================

        except Exception as e:
            st.warning(f"⚠️ Не удалось загрузить условие запуска '{startup_kks}': {e}")

    for kks in output_kks:
        arr = result[kks]
        if arr is None or len(arr) == 0:
            st.warning(f"{kks}: пустой результат")
            continue

        pred_df = pd.DataFrame({
            "datetime": pd.to_datetime(arr[:, 0], format=dt_fmt, errors="coerce"),
            "value": pd.to_numeric(arr[:, 1], errors="coerce"),
        }).dropna(subset=["datetime"]).sort_values("datetime")

        pred_df["status"] = pred_df["datetime"].dt.strftime(dt_fmt).map(
            lambda ts: statuses.get((kks, ts), "OK")
        )
        
        # --- ЛОГИКА УСЛОВИЯ ЗАПУСКА ---
        if start_df is not None:
            merged_start = pd.merge_asof(
                pred_df[['datetime']], start_df, on='datetime', direction='backward'
            )
            start_values = merged_start['startup'].fillna(0).values
            pred_df["startup_cond"] = start_values
            
            startup_ok = start_values > 0.5
            pred_df.loc[~startup_ok, "status"] = "WARNING"
            
            cond_failed = (~startup_ok).sum()
            if cond_failed > 0:
                st.caption(f"⚠️ {cond_failed} точек прогноза заблокированы условием запуска.")
        else:
            pred_df["startup_cond"] = np.nan

        # ---------- график ----------
        fig = go.Figure()

        if not actuals_df.empty:
            fig.add_trace(go.Scatter(
                x=actuals_df["datetime"], y=actuals_df["value"],
                mode="lines", name="Факт (Y)",
                line=dict(color="#4a90d9", width=1.5), connectgaps=False,
                hovertemplate="Факт: %{y:.4g}<extra></extra>",
            ))

        ok_mask = pred_df["status"] == "OK"
        warn_mask = ~ok_mask

        hover_tmpl = "Прогноз: %{y:.4g}"
        if startup_kks:
            hover_tmpl += "<br>Усл. запуска: %{customdata:.2f}"
        
        if warn_mask.any():
            fig.add_trace(go.Scatter(
                x=pred_df.loc[warn_mask, "datetime"], y=pred_df.loc[warn_mask, "value"],
                mode="markers", name="Прогноз (WARNING)",
                marker=dict(color="#ef4444", size=6),
                customdata=pred_df.loc[warn_mask, "startup_cond"],
                hovertemplate=hover_tmpl + "<extra>WARNING</extra>"
            ))
        if ok_mask.any():
            fig.add_trace(go.Scatter(
                x=pred_df.loc[ok_mask, "datetime"], y=pred_df.loc[ok_mask, "value"],
                mode="markers", name="Прогноз (OK)",
                marker=dict(color="#10b981", size=5),
                customdata=pred_df.loc[ok_mask, "startup_cond"],
                hovertemplate=hover_tmpl + "<extra>OK</extra>"
            ))

        # --- Y-диапазон по факту ---
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

        st.plotly_chart(fig, use_container_width=True, key=f"chart_main_{kks}")

        # Заодно покажем, насколько прогноз вылезает за пределы Y-факта —
        # полезно понять, где модель «фантазирует» вне зоны обучения
        if y_range is not None:
            out_of_range = pred_df[
                (pred_df["value"] < y_range[0]) | (pred_df["value"] > y_range[1])
            ]
            if len(out_of_range) > 0:
                st.caption(
                    f"⚠️ {len(out_of_range)} точек прогноза ({len(out_of_range)/len(pred_df)*100:.1f}%) "
                    f"выходят за диапазон факта [{y_range[0]:.4g}, {y_range[1]:.4g}] "
                    f"и обрезаны на графике."
                )

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
                # =====================================================================
        # --- СОХРАНЕНИЕ СИГНАЛА В АРХИВ ---
        # =====================================================================
        if st.button(f"📥 Сохранить сигнал {kks} в архив", key=f"btn_save_archive_{kks}"):
            try:
                with st.spinner("Сохранение..."):
                    # Формируем DataFrame в формате, который ждет visualizer (DATE;TIME;KKS)
                    df_to_save = pd.DataFrame({
                        "DATE": pred_df["datetime"].dt.strftime("%d.%m.%Y"),
                        "TIME": pred_df["datetime"].dt.strftime("%H:%M:%S"),
                        kks: pred_df["value"]
                    })
                    
                    # Сохраняем в буфер
                    csv_buffer = io.BytesIO()
                    df_to_save.to_csv(csv_buffer, sep=";", index=False, encoding="utf-8")
                    csv_buffer.seek(0)
                    
                    filename = f"{kks}.csv"
                    upload_url = f"{api_url}/api/upload-signal-csv?config={requests.utils.quote(config_name)}"
                    
                    # Отправляем на бэкенд
                    r = requests.post(
                        upload_url,
                        files={"file": (filename, csv_buffer, "text/csv")},
                        timeout=120
                    )
                    r.raise_for_status()
                    
                st.success(f"✅ Сигнал **{kks}** успешно сохранён в архив!")
                st.info("Теперь вы можете использовать этот KKS в визуализаторе или как входной сигнал для новых правил.")
            except Exception as e:
                st.error(f"❌ Ошибка сохранения сигнала: {e}")
        # =====================================================================

        # ---------- метрики ----------
        st.markdown("##### 📊 Метрики")

        if actuals_df.empty:
            st.info("Фактические значения Y-labeler'а недоступны — метрики рассчитать нельзя.")
        else:
            merged = align_pred_with_actuals(pred_df, actuals_df, tol_seconds=1)
            if merged.empty:
                st.warning("Не удалось сопоставить предсказания с фактами по времени — метрики не рассчитаны.")
            else:
                overall = compute_regression_metrics(merged["actual"].values, merged["pred"].values)
                st.caption(f"Сопоставлено точек: {overall['N']} из {len(pred_df)} (по timestamp'ам)")

                df_overall = metrics_to_dataframe(overall)

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
                        
                        # Пробуем разные варианты имен файлов
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
                                    # Если это не 404, значит ошибка серьезная (например, 500)
                                    st.error(f"Ошибка {r.status_code}: {r.text}")
                                    break
                            except Exception as e:
                                st.error(f"Ошибка сети: {e}")
                                break
                                
                        if not saved:
                            st.error(f"Не удалось найти файл проекта. Проверены имена: {', '.join(possible_filenames)}")

                # =====================================================================
                # --- АНАЛИЗ ОСТАТКОВ ДЛЯ СТАТУСА OK ---
                # =====================================================================
                merged_ok = merged[merged["status"] == "OK"].copy()
                if not merged_ok.empty:
                    st.divider()
                    st.markdown("##### 📉 Анализ остатков (ошибок) для статуса OK")
                    
                    # Считаем остатки (Предсказание минус Факт)
                    residuals = merged_ok["pred"] - merged_ok["actual"]
                    abs_residuals = residuals.abs()

                    # Базовая статистика
                    mean_res = residuals.mean()
                    std_res = residuals.std() if len(residuals) > 1 else 0.0
                    p95_abs = abs_residuals.quantile(0.95)
                    p99_abs = abs_residuals.quantile(0.99)

                    # 1. Графики
                    col_hist, col_box = st.columns([2, 1])
                    
                    with col_hist:
                        fig_hist = go.Figure(data=[go.Histogram(
                            x=residuals, 
                            nbinsx=50, 
                            marker_color='#4a90d9',
                            opacity=0.75
                        )])
                        fig_hist.update_layout(
                            title="Гистограмма распределения остатков (Прогноз - Факт)",
                            xaxis_title="Остаток",
                            yaxis_title="Количество",
                            height=400,
                            template="plotly_white",
                            margin=dict(l=20, r=20, t=40, b=20)
                        )
                        st.plotly_chart(fig_hist, use_container_width=True, key=f"hist_res_{kks}")

                    with col_box:
                        fig_box = go.Figure(data=[go.Box(
                            y=residuals, 
                            name="Остатки",
                            marker_color='#ef4444',
                            boxpoints='outliers'
                        )])
                        fig_box.update_layout(
                            title="Диаграмма с усами",
                            yaxis_title="Остаток",
                            height=400,
                            template="plotly_white",
                            margin=dict(l=20, r=20, t=40, b=20)
                        )
                        st.plotly_chart(fig_box, use_container_width=True, key=f"box_res_{kks}")

                    # 2. Таблица статистики
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
                        custom_p = st.number_input(
                            "Введите перцентиль (0-100)", 
                            min_value=0.0, max_value=100.0, value=90.0, step=0.5,
                            key=f"custom_p_input_{kks}"
                        )
                        
                        # Инициализируем стейт для сохранения результата
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
                    st.download_button(
                        "Скачать метрики (CSV)",
                        data=csv_overall,
                        file_name=f"{kks}_metrics.csv",
                        mime="text/csv",
                        key=f"dl_{kks}",
                    )

    with st.expander("Лог инференса (raw JSON)"):
        st.json(log_data)
else:
    st.info("Нажмите «Запустить инференс», чтобы построить график.")