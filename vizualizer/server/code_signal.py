import re
from typing import List, Tuple, Dict

import numpy as np
import pandas as pd


class CodeEvaluationError(Exception):
    """Ошибка во время вычисления выражения CODE."""


def sanitize_numeric_column(series: pd.Series) -> pd.Series:
    if series.dtype.kind in ("i", "u", "f"):
        return series
    text = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(text, errors="coerce")


def evaluate_code_expression(code_str: str, df_all: pd.DataFrame) -> Tuple[pd.Series, List[str]]:
    if df_all is None or df_all.empty:
        raise CodeEvaluationError("Нет данных для расчёта синтетического сигнала.")
    if not code_str or not code_str.strip():
        raise CodeEvaluationError("Строка CODE пуста.")

    index = df_all.index
    numeric_df = df_all.apply(sanitize_numeric_column)
    series_map = {col: numeric_df[col] for col in numeric_df.columns}
    warnings: List[str] = []

    # ---------- обработка «неправильных» имён сигналов ----------
    safe_name_map: Dict[str, str] = {}
    used_safe_names = set()

    def _make_safe_name(original: str, idx: int) -> str:
        base = re.sub(r"\W", "_", original)
        if not base or not re.match(r"[A-Za-z_]", base):
            base = f"SIG_{idx}"
        while base in used_safe_names:
            base += "_"
        used_safe_names.add(base)
        return base

    sorted_signals = sorted(series_map.keys(), key=len, reverse=True)
    for idx, sig_name in enumerate(sorted_signals):
        safe = _make_safe_name(sig_name, idx)
        safe_name_map[sig_name] = safe

    def _replace_signal_names(expr: str) -> str:
        result = []
        i = 0
        in_string = False
        string_char = ""

        while i < len(expr):
            ch = expr[i]
            if in_string:
                result.append(ch)
                if ch == string_char and expr[i - 1] != "\\":
                    in_string = False
                i += 1
                continue

            if ch in ("'", '"'):
                in_string = True
                string_char = ch
                result.append(ch)
                i += 1
                continue

            matched = None
            for name in sorted_signals:
                if expr.startswith(name, i):
                    matched = name
                    break
            if matched:
                result.append(safe_name_map[matched])
                i += len(matched)
            else:
                result.append(ch)
                i += 1

        return "".join(result)

    # ---------- вспомогательные функции ----------
    def _ensure_series(value) -> pd.Series:
        if isinstance(value, pd.Series):
            return value.reindex(index)
        if isinstance(value, pd.DataFrame):
            if value.shape[1] == 1:
                return value.iloc[:, 0].reindex(index)
            raise CodeEvaluationError("Невозможно привести DataFrame с несколькими колонками к Series.")
        if isinstance(value, (list, tuple, np.ndarray)):
            arr = np.asarray(value, dtype=float)
            if arr.size == 1:
                arr = np.full(len(index), arr.item())
            elif arr.shape[0] != len(index):
                return pd.Series(np.nan, index=index)
            return pd.Series(arr, index=index)
        if value is None or np.isscalar(value):
            return pd.Series(value, index=index)
        try:
            return pd.Series(value, index=index)
        except Exception as exc:
            raise CodeEvaluationError(f"Невозможно преобразовать значение '{value}' к Series.") from exc

    def _aggregate_nanfunc(func, args, empty_value=np.nan):
        if not args:
            return pd.Series(empty_value, index=index)
        stacked = np.vstack([_ensure_series(arg).values for arg in args])
        return pd.Series(func(stacked, axis=0), index=index)

    def GETPOINT(*_):
        if "GETPOINT" not in warnings:
            warnings.append("GETPOINT пока не поддержан — возвращается NaN.")
        return pd.Series(np.nan, index=index)

    def PREV(param_name):
        if param_name not in series_map:
            return pd.Series(np.nan, index=index)
        return series_map[param_name].shift(1)

    def _history_series(param_name):
        if param_name not in series_map:
            return None
        return series_map[param_name]

    def _history_window(period):
        try:
            minutes = int(period)
        except (TypeError, ValueError):
            return None
        if minutes <= 0:
            return None
        return f"{minutes}min"

    def _history_apply(param_name, period, fn):
        s = _history_series(param_name)
        window = _history_window(period)
        if s is None or window is None:
            return pd.Series(np.nan, index=index)
        return fn(s.rolling(window))

    HISTORYAVG = lambda n, p: _history_apply(n, p, lambda r: r.mean())
    HISTORYCOUNT = lambda n, p: _history_apply(n, p, lambda r: r.count())
    HISTORYSUM = lambda n, p: _history_apply(n, p, lambda r: r.sum())
    HISTORYMAX = lambda n, p: _history_apply(n, p, lambda r: r.max())
    HISTORYMIN = lambda n, p: _history_apply(n, p, lambda r: r.min())
    HISTORYDIFF = lambda n, p: _history_apply(n, p, lambda r: r.max() - r.min())

    def HISTORYGRADIENT(param_name, period):
        s = _history_series(param_name)
        window = _history_window(period)
        if s is None or window is None:
            return pd.Series(np.nan, index=index)

        def slope(window_series: pd.Series):
            valid = window_series.dropna()
            if len(valid) < 2:
                return np.nan
            x = valid.index.view(np.int64).astype(float) / 1e9
            y = valid.values.astype(float)
            x_mean = x.mean()
            y_mean = y.mean()
            denom = np.sum((x - x_mean) ** 2)
            if denom == 0:
                return np.nan
            return np.sum((x - x_mean) * (y - y_mean)) / denom

        return s.rolling(window).apply(slope, raw=False)

    def ROUND(a, b=0):
        a_values = _ensure_series(a).values
        b_values = _ensure_series(b).values
        decimals = [
            0 if np.isnan(dec) else int(round(dec))
            for dec in b_values
        ]
        rounded = np.array([
            np.round(val, dec) if not np.isnan(val) else np.nan
            for val, dec in zip(a_values, decimals)
        ])
        return pd.Series(rounded, index=index)

    # ---------- окружение eval ----------
    env = {
        "np": np,
        "ABS": lambda a: pd.Series(np.abs(_ensure_series(a).values), index=index),
        "POW": lambda a, b: pd.Series(np.power(_ensure_series(a).values, _ensure_series(b).values), index=index),
        "MIN": lambda *args: _aggregate_nanfunc(np.nanmin, args),
        "MAX": lambda *args: _aggregate_nanfunc(np.nanmax, args),
        "AVG": lambda *args: _aggregate_nanfunc(np.nanmean, args, empty_value=0.0),
        "MED": lambda *args: _aggregate_nanfunc(np.nanmedian, args),
        "ROUND": ROUND,
        "WHEN": lambda cond, t_val, f_val: pd.Series(
            np.where(_ensure_series(cond).astype(bool).values,
                     _ensure_series(t_val).values,
                     _ensure_series(f_val).values),
            index=index,
        ),
        "PREV": PREV,
        "HISTORYAVG": HISTORYAVG,
        "HISTORYCOUNT": HISTORYCOUNT,
        "HISTORYSUM": HISTORYSUM,
        "HISTORYMAX": HISTORYMAX,
        "HISTORYMIN": HISTORYMIN,
        "HISTORYDIFF": HISTORYDIFF,
        "HISTORYGRADIENT": HISTORYGRADIENT,
        "GETPOINT": GETPOINT,
    }

    for original_name, safe_name in safe_name_map.items():
        env[safe_name] = series_map[original_name]

    def _normalize_expression(expr: str) -> str:
        expr = re.sub(r"\bAND\b", "&", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bOR\b", "|", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bNOT\b", "~", expr, flags=re.IGNORECASE)
        expr = expr.replace("<>", "!=")
        expr = re.sub(r"(?<![<>=!])=(?![<>=])", "==", expr)
        return expr

    normalized_code = _normalize_expression(code_str)
    normalized_code = _replace_signal_names(normalized_code)

    try:
        raw_result = eval(normalized_code, {"__builtins__": {}}, env)
    except Exception as exc:
        raise CodeEvaluationError(str(exc)) from exc

    result_series = _ensure_series(raw_result)
    result_series.name = result_series.name or "CODE_RESULT"
    return result_series, warnings

def compute_code_signal(
    code_str: str,
    df_all: pd.DataFrame,
    warn_callback=lambda msg: None,
) -> pd.Series:
    """
    Совместимость с визуализатором: считает синтетический сигнал по CODE
    и прокидывает предупреждения через колбэк.
    """
    series, warnings = evaluate_code_expression(code_str, df_all)
    for message in warnings:
        warn_callback(message)
    return series