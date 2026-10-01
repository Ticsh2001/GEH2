# nn_check_app.py — визуальная проверка обученной модели на всём диапазоне данных
import os
import sys
import json
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
import logging
import time


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

    # EV (Explained Variance) — иногда полезнее R² при смещённом среднем
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


def align_pred_with_actuals(pred_df: pd.DataFrame,
                            actuals_df: pd.DataFrame,
                            tol_seconds: int = 1) -> pd.DataFrame:
    """
    Inner-merge по datetime с допуском tol_seconds (защита от секундного дрожания).
    Возвращает DataFrame: datetime, actual, pred, status.
    """
    if pred_df.empty or actuals_df.empty:
        return pd.DataFrame(columns=["datetime", "actual", "pred", "status"])

    a = actuals_df.rename(columns={"value": "actual"}).copy()
    p = pred_df.rename(columns={"value": "pred"}).copy()

    a = a.sort_values("datetime")
    p = p.sort_values("datetime")

    # merge_asof по ближайшему времени, затем фильтр по допуску
    merged = pd.merge_asof(
        p, a[["datetime", "actual"]],
        on="datetime",
        direction="nearest",
        tolerance=pd.Timedelta(seconds=tol_seconds)
    )
    merged = merged.dropna(subset=["actual"])
    return merged[["datetime", "actual", "pred", "status"]]

# ----------------------------------------------------------------------
# 1. Разбор query params
# ----------------------------------------------------------------------
qp = st.query_params
config_name = qp.get("config", "")
project_code = qp.get("code", "")
model_dir = qp.get("model_dir", "")
y_labeler_id = qp.get("y_labeler_id", "")
api_url = qp.get("api_url", "http://localhost:8000")

if not (project_code and model_dir and y_labeler_id):
    st.error("Не переданы обязательные параметры (code / model_dir / y_labeler_id).")
    st.stop()

print(f"[nn_check_app] params: config={config_name!r} code={project_code!r} "
      f"model_dir={model_dir!r} y_labeler_id={y_labeler_id!r} api_url={api_url!r}",
      flush=True)
st.write(f"**Отладка:** config={config_name}, code={project_code}, "
         f"model_dir={model_dir}, y_labeler_id={y_labeler_id}, api_url={api_url}")

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

config_path = os.path.join(model_dir, f"{project_code}.config.json")
model_path = os.path.join(model_dir, f"{project_code}.keras")
meta_path = os.path.join(model_dir, f"{project_code}_meta.json")

for p in (config_path, model_path, meta_path):
    if not os.path.isfile(p):
        st.error(f"Не найден файл: {p}")
        st.stop()

# ----------------------------------------------------------------------
# 3. Сессия и метаданные
# ----------------------------------------------------------------------
@st.cache_resource
def _make_session(cfg, mdl, mt):
    return InferenceSession.from_config_file(
        config_path=cfg, model_path=mdl, metadata_path=mt
    )

t0 = time.time()
print("[nn_check_app] creating InferenceSession...", flush=True)
session = _make_session(config_path, model_path, meta_path)
print(f"[nn_check_app] InferenceSession created in {time.time()-t0:.2f}s", flush=True)


meta = session.metadata()
print(f"[nn_check_app] meta loaded, inputs={len(meta.get('inputs', []))}", flush=True)


st.title(f"🔍 Проверка модели: {project_code}")
st.caption(meta.get("project_description", [""])[0] if meta.get("project_description") else "")

dt_fmt = meta["input_contract"]["datetime_format"]

# ----------------------------------------------------------------------
# 4. Загрузка сырых входных сигналов через API
# ----------------------------------------------------------------------
def load_signal_from_api(name: str, config_name: str, api_url: str, dt_fmt: str) -> np.ndarray:
    """Возвращает ndarray (N, 2): datetime-строка в формате контракта, значение."""
    url = f"{api_url}/api/signal-data?config={requests.utils.quote(config_name)}"
    r = requests.post(url, json={"signal_names": [name], "format": "json"}, timeout=120)
    r.raise_for_status()
    payload = r.json()
    records = (payload.get("data") or {}).get(name) or []
    if not records:
        raise ValueError(f"Нет данных для сигнала '{name}' в архиве")
    df = pd.DataFrame(records)
    if "datetime" not in df.columns or "value" not in df.columns:
        raise ValueError(f"Некорректный формат ответа для '{name}'")

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    dt_str = df["datetime"].dt.strftime(dt_fmt)
    mask = dt_str.notna() & df["value"].notna()
    return np.column_stack([
        dt_str[mask].to_numpy(),
        df["value"][mask].to_numpy(dtype=float),
    ])

with st.spinner("Загружаю сырые входные сигналы из архива..."):
    input_signals = {}
    missing = []
    for inp in meta["inputs"]:
        name = inp["name"]
        t1 = time.time()
        print(f"[nn_check_app] fetching signal {name!r} from {api_url}...", flush=True)
        try:
            input_signals[name] = load_signal_from_api(name, config_name, api_url, dt_fmt)
            print(f"[nn_check_app]   {name}: got {len(input_signals[name])} rows "
                  f"in {time.time()-t1:.2f}s", flush=True)
        except Exception as e:
            print(f"[nn_check_app]   {name}: FAILED — {e}", flush=True)
            missing.append(f"{name}: {e}")

if missing:
    st.error("Не удалось загрузить входные сигналы:\n" + "\n".join(missing))
    st.stop()

st.success(f"Загружено входных сигналов: {len(input_signals)}")

# ----------------------------------------------------------------------
# 5. Фактические значения Y-labeler'а (для сравнения)
# ----------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _load_actuals(_y_id, _config, _code, _api_url):
    url = (f"{_api_url}/api/nn/data/{_y_id}/full"
           f"?config={requests.utils.quote(_config)}"
           f"&code={requests.utils.quote(_code)}"
           f"&port=out-1")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.json()
print("[nn_check_app] loading actuals from Y-labeler...", flush=True)

try:
    actuals_records = _load_actuals(y_labeler_id, config_name, project_code, api_url)
    actuals_df = pd.DataFrame(actuals_records)
    actuals_df["datetime"] = pd.to_datetime(actuals_df["datetime"], errors="coerce")
    actuals_df = actuals_df.dropna(subset=["datetime"]).sort_values("datetime")
    print(f"[nn_check_app] actuals: {len(actuals_df)} rows, "
          f"columns={list(actuals_df.columns)}", flush=True)
except Exception as e:
    st.warning(f"Не удалось загрузить фактические значения Y-labeler'а: {e}")
    actuals_df = pd.DataFrame(columns=["datetime", "value"])

# ----------------------------------------------------------------------
# 6. Запуск инференса
# ----------------------------------------------------------------------
log_path = os.path.join(model_dir, f"{project_code}.inference_log.json")

if st.button("▶ Запустить инференс на всём диапазоне", type="primary"):
    print("[nn_check_app] button PREDICT clicked", flush=True)
    with st.spinner("Модель считает предсказания..."):
        try:
            result = session.predict(input_signals, log_path=log_path)
        except TypeError:
            # на случай, если predict не принимает log_path
            result = session.predict(input_signals)
        st.session_state["inference_result"] = result
        st.session_state["inference_log"] = log_path

result = st.session_state.get("inference_result")

if result:
    output_kks = list(result.keys())
    st.subheader("Результаты инференса")

    # Загружаем лог (статусы)
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

        # ---------- график ----------
        fig = go.Figure()

        if not actuals_df.empty:
            fig.add_trace(go.Scatter(
                x=actuals_df["datetime"], y=actuals_df["value"],
                mode="lines", name="Факт (Y)",
                line=dict(color="#4a90d9", width=2)
            ))

        ok_mask = pred_df["status"] == "OK"
        warn_mask = ~ok_mask

        if warn_mask.any():
            fig.add_trace(go.Scatter(
                x=pred_df.loc[warn_mask, "datetime"],
                y=pred_df.loc[warn_mask, "value"],
                mode="markers", name="Предсказание (WARNING)",
                marker=dict(color="#ef4444", size=6)
            ))
        if ok_mask.any():
            fig.add_trace(go.Scatter(
                x=pred_df.loc[ok_mask, "datetime"],
                y=pred_df.loc[ok_mask, "value"],
                mode="markers", name="Предсказание (OK)",
                marker=dict(color="#10b981", size=5)
            ))

        fig.update_layout(
            title=f"Выход {kks}",
            xaxis_title="Время", yaxis_title="Значение",
            height=500, uirevision="nn-check"
        )
        st.plotly_chart(fig, use_container_width=True)

        # ---------- метрики ----------
        st.markdown("##### 📊 Метрики")

        if actuals_df.empty:
            st.info("Фактические значения Y-labeler'а недоступны — метрики рассчитать нельзя.")
        else:
            merged = align_pred_with_actuals(pred_df, actuals_df, tol_seconds=1)
            if merged.empty:
                st.warning("Не удалось сопоставить предсказания с фактами по времени — "
                           "метрики не рассчитаны.")
            else:
                # Общие метрики
                overall = compute_regression_metrics(
                    merged["actual"].values, merged["pred"].values
                )
                st.caption(f"Сопоставлено точек: {overall['N']} из {len(pred_df)} "
                           f"(по timestamp'ам)")

                df_overall = metrics_to_dataframe(overall)

                # Разбивка по статусам
                df_by_status_rows = []
                for status_val in ("OK", "WARNING"):
                    sub = merged[merged["status"] == status_val]
                    if sub.empty:
                        continue
                    m = compute_regression_metrics(sub["actual"].values, sub["pred"].values)
                    row = {"Статус": status_val, "N": m["N"]}
                    for key in ("MSE", "RMSE", "MAE", "R²", "SMAPE, %", "Max abs err",
                                "Bias (mean err)"):
                        v = m.get(key, float("nan"))
                        if isinstance(v, float) and np.isnan(v):
                            row[key] = "—"
                        elif key in ("R²",):
                            row[key] = f"{v:.4f}"
                        elif key in ("SMAPE, %",):
                            row[key] = f"{v:.2f}"
                        else:
                            row[key] = f"{v:.6g}"
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

                # Экспорт в CSV по кнопке (опционально)
                with st.expander("Экспорт метрик"):
                    csv_overall = df_overall.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "Скачать метрики (CSV)",
                        data=csv_overall,
                        file_name=f"{kks}_metrics.csv",
                        mime="text/csv",
                    )

    with st.expander("Лог инференса (raw JSON)"):
        st.json(log_data)
else:
    st.info("Нажмите «Запустить инференс», чтобы построить график.")