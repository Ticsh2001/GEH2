# visualizer_app.py — с поддержкой сохранения/загрузки состояния

import pandas as pd
import requests
import streamlit as st
import plotly.express as px
import numpy as np
import plotly.graph_objects as go
from typing import List
from datetime import datetime, time
from io import BytesIO
from code_signal import register_tables
import re

from code_signal import compute_code_signal, sanitize_numeric_column, evaluate_code_expression, CodeEvaluationError
from visualizer_state import (
    create_visualizer_state, 
    load_visualizer_state,
    STATE_VERSION
)

def debug_series(name: str, series: pd.Series, formula: str):
    if series is None:
        print(f"[DEBUG] {name}: series is None")
        return

    total = len(series)
    nan_count = series.isna().sum()
    valid_count = total - nan_count

    print(f"\n[DEBUG] ===== {name} =====")
    print(f"[DEBUG] Formula: {formula}")
    print(f"[DEBUG] Total points: {total}")
    print(f"[DEBUG] Valid points: {valid_count}")
    print(f"[DEBUG] NaN points: {nan_count}")

    if valid_count == 0:
        print(f"[DEBUG] ❌ ВСЁ NaN — сигнал невалиден")
    else:
        print(f"[DEBUG] ✅ Есть валидные значения")
        print(f"[DEBUG] Sample:")
        print(series.dropna().head(5))

def _compute_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Коэффициент детерминации R^2."""
    mask = ~np.isnan(y_true) & ~np.isnan(y_pred)
    if mask.sum() < 2:
        return np.nan
    yt = y_true[mask]
    yp = y_pred[mask]
    ss_res = np.sum((yt - yp) ** 2)
    ss_tot = np.sum((yt - yt.mean()) ** 2)
    if ss_tot == 0:
        return np.nan
    return 1.0 - ss_res / ss_tot


def fit_linear(x: np.ndarray, y: np.ndarray) -> dict:
    """Линейная аппроксимация y = a + b*x."""
    mask = ~np.isnan(x) & ~np.isnan(y)
    if mask.sum() < 2:
        return {"a": np.nan, "b": np.nan, "r2": np.nan, "y_pred": np.full_like(y, np.nan)}
    coeffs = np.polyfit(x[mask], y[mask], 1)  # [b, a]
    b, a = coeffs[0], coeffs[1]
    y_pred = a + b * x
    r2 = _compute_r2(y, y_pred)
    return {"a": a, "b": b, "r2": r2, "y_pred": y_pred}


def fit_polynomial(x: np.ndarray, y: np.ndarray, degree: int) -> dict:
    """Полиномиальная аппроксимация степени N: y = Σ c_k * x^k (k=0..N)."""
    degree = max(1, int(degree))
    mask = ~np.isnan(x) & ~np.isnan(y)
    if mask.sum() <= degree:
        return {"coeffs": None, "r2": np.nan, "y_pred": np.full_like(y, np.nan)}
    coeffs = np.polyfit(x[mask], y[mask], degree)  # старший → младший
    y_pred = np.polyval(coeffs, x)
    r2 = _compute_r2(y, y_pred)
    return {"coeffs": coeffs, "r2": r2, "y_pred": y_pred}


def fit_power_law(x: np.ndarray, y: np.ndarray) -> dict:
    """
    Степенная зависимость: y = a * x^p.
    Фит делается в логарифмическом пространстве: ln(y) = ln(a) + p*ln(x).
    Требует x > 0 и y > 0.
    """
    mask = (~np.isnan(x)) & (~np.isnan(y)) & (x > 0) & (y > 0)
    if mask.sum() < 2:
        return {"a": np.nan, "p": np.nan, "r2": np.nan, "y_pred": np.full_like(y, np.nan), "used_points": 0}
    lx = np.log(x[mask])
    ly = np.log(y[mask])
    coeffs = np.polyfit(lx, ly, 1)  # [p, ln(a)]
    p, ln_a = coeffs[0], coeffs[1]
    a = float(np.exp(ln_a))
    y_pred = a * np.power(x, p)
    r2 = _compute_r2(y, y_pred)
    return {"a": a, "p": p, "r2": r2, "y_pred": y_pred, "used_points": int(mask.sum())}


def _format_poly_equation(coeffs: np.ndarray, x_name: str, y_name: str) -> str:
    """
    Форматирует полином: y = c0 + c1*x + c2*x^2 + ...
    coeffs: [cN, ..., c1, c0] — порядок Polyfit (старшие → младшие)
    """
    if coeffs is None or len(coeffs) == 0:
        return f"{y_name} = NaN"
    terms = []
    deg = len(coeffs) - 1
    for i, c in enumerate(coeffs):
        k = deg - i  # степень
        coef = f"{c:.6g}"
        if k == 0:
            terms.append(f"{coef}")
        elif k == 1:
            terms.append(f"{coef}·{x_name}")
        else:
            terms.append(f"{coef}·{x_name}^{k}")
    return f"{y_name} = " + " + ".join(terms)


def extract_table_names_from_code(code_str: str) -> set[str]:
    """
    Ищет имена таблиц, переданные первым аргументом в INTERPOLATE(...) и GETPOINT(...).
    Поддерживает:
      - "NAME" / 'NAME'
      - NAME (без кавычек, идентификатор: буквы/цифры/подчёркивание, начиная с буквы/подчёркивания)
    """
    if not code_str:
        return set()
    names = set()

    # Интерполяция: первый аргумент — имя таблицы
    rx_q_interp = re.compile(r'INTERPOLATE\s*\(\s*([\'"])(?P<n>[^\'"]+)\1', re.IGNORECASE)
    rx_id_interp = re.compile(r'INTERPOLATE\s*\(\s*(?P<n>[A-Za-z_][A-Za-z0-9_]*)\s*,', re.IGNORECASE)

    # GETPOINT: первый аргумент — имя таблицы
    rx_q_get = re.compile(r'GETPOINT\s*\(\s*([\'"])(?P<n>[^\'"]+)\1', re.IGNORECASE)
    rx_id_get = re.compile(r'GETPOINT\s*\(\s*(?P<n>[A-Za-z_][A-Za-z0-9_]*)\s*,', re.IGNORECASE)

    for rx in (rx_q_interp, rx_id_interp, rx_q_get, rx_id_get):
        for m in rx.finditer(code_str):
            names.add(m.group('n').strip())

    return names

def normalize_code_tables(code_str: str) -> str:
    """
    Оборачивает в кавычки первый аргумент INTERPOLATE/GETPOINT, если он был без кавычек.
    Пример: INTERPOLATE(h_TABLE, ...) → INTERPOLATE("h_TABLE", ...)
    """
    if not code_str:
        return code_str
    s = code_str

    # Только случаи без кавычек: (FUNC( IDENT , ...)) → (FUNC("IDENT", ...))
    s = re.sub(
        r'(INTERPOLATE\s*\(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*,)',
        r'\1"\2"\3',
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r'(GETPOINT\s*\(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*,)',
        r'\1"\2"\3',
        s,
        flags=re.IGNORECASE,
    )
    return s

def ensure_tables_for_code(code_str: str):
    """
    1) Находит имена таблиц в CODE (INTERPOLATE/GETPOINT, с кавычками и без).
    2) Подгружает отсутствующие таблицы через /api/table/file/{name}.
    3) Регистрирует актуальный кэш таблиц в вычислителе.
    """
    needed = extract_table_names_from_code(code_str)
    if not needed:
        return
    loaded = set(st.session_state.tables_cache.keys())
    to_load = sorted(needed - loaded)
    if to_load:
        st.info(f"📚 Загружаем таблицы из CODE: {', '.join(to_load)}")
        for t in to_load:
            try:
                load_table_df(t)  # кладёт DataFrame в st.session_state.tables_cache[t]
            except Exception as e:
                st.warning(f"⚠️ Таблица '{t}' не загружена: {e}")

    # В любом случае регистрируем актуальный кэш — операция идемпотентная
    register_tables(st.session_state.tables_cache)


def compute_streaming_signal_streaming_forward(
    formula: str,
    df_base: pd.DataFrame,
    signal_name: str,
) -> pd.Series:
    """
    Потоковый (однопроходный) расчёт самоссылающегося сигнала.
    Идём по индексу слева направо, на каждом шаге подаём уже посчитанную
    часть сигнала (для PREV/HISTORY от самого себя).
    """
    # Базовые данные и индекс
    df_work = df_base.copy()
    idx = df_work.index
    n = len(idx)

    # Буфер для результата
    result = pd.Series(np.nan, index=idx, name=signal_name)

    # Идём по времени слева направо
    for i in range(n):
        # На каждом шаге подставляем уже вычисленные значения сигнала (до текущего момента)
        df_work_current = df_work.iloc[: i + 1].copy()
        df_work_current[signal_name] = result.iloc[: i].reindex(df_work_current.index)
        # В текущем шаге ещё нет значения -> пусть будет NaN на конце
        # evaluate_code_expression посчитает весь кусок до текущего индекса
        try:
            series_step, _ = evaluate_code_expression(formula, df_work_current)
        except Exception as e:
            raise CodeEvaluationError(f"Ошибка на шаге {i} ({idx[i]}): {e}") from e

        # Берём значение в текущей точке времени
        val_i = series_step.iloc[-1]
        result.iat[i] = val_i

    return result

def compute_streaming_signal(
    formula: str,
    df_base: pd.DataFrame,
    signal_name: str,
) -> pd.Series:
    """
    Потоковый расчёт самоссылающегося сигнала.
    Все зависимости уже в df_base (посчитаны пакетно).
    Один проход по строкам, O(n).
    """
    import re
    from code_signal import sanitize_numeric_column
    from code_signal import TABLE_REGISTRY
    from code_signal import _get_xy_from_table, _interp_1d

    df_work = df_base.copy()
    df_work[signal_name] = np.nan

    index = df_work.index
    n = len(index)

    # Нормализуем числовые колонки один раз
    for col in df_work.columns:
        df_work[col] = sanitize_numeric_column(df_work[col])

    result = np.full(n, np.nan, dtype=np.float64)

    # numpy-массивы для быстрого доступа
    col_arrays = {}
    for col in df_work.columns:
        col_arrays[col] = df_work[col].values
    col_arrays[signal_name] = result

    # Безопасные имена сигналов
    safe_name_map = {}
    used_safe = set()
    sorted_signals = sorted(col_arrays.keys(), key=len, reverse=True)

    for idx_s, sig in enumerate(sorted_signals):
        base = re.sub(r"\W", "_", sig)
        if not base or not re.match(r"[A-Za-z_]", base):
            base = f"SIG_{idx_s}"
        while base in used_safe:
            base += "_"
        used_safe.add(base)
        safe_name_map[sig] = base

    # Замена имён сигналов в формуле
    def replace_signal_names(expr):
        parts = []
        pos = 0
        in_str = False
        str_ch = ""
        while pos < len(expr):
            ch = expr[pos]
            if in_str:
                parts.append(ch)
                if ch == str_ch and (pos == 0 or expr[pos - 1] != "\\"):
                    in_str = False
                pos += 1
                continue
            if ch in ("'", '"'):
                in_str = True
                str_ch = ch
                parts.append(ch)
                pos += 1
                continue
            matched = None
            for name in sorted_signals:
                if expr.startswith(name, pos):
                    matched = name
                    break
            if matched:
                parts.append(safe_name_map[matched])
                pos += len(matched)
            else:
                parts.append(ch)
                pos += 1
        return "".join(parts)

    def normalize_expr(expr):
        expr = re.sub(r"\bAND\b", "&", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bOR\b", "|", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bNOT\b", "~", expr, flags=re.IGNORECASE)
        expr = expr.replace("<>", "!=")
        expr = re.sub(r"(?<![<>=!])=(?![<>=])", "==", expr)
        return expr

    normalized = normalize_expr(formula)
    safe_formula = replace_signal_names(normalized)

    safe_self = safe_name_map[signal_name]

    # =========================================================================
    # Подмена PREV / HISTORY*(self, period) → специальные токены
    # =========================================================================

    # PREV(self) → __prev_self__
    safe_formula = re.sub(
        r"PREV\s*\(\s*" + re.escape(safe_self) + r"\s*\)",
        "__prev_self__",
        safe_formula,
        flags=re.IGNORECASE,
    )

    # PREV(other_signal) → __prev_OTHER__
    # Собираем все PREV(safe_name) кроме self
    prev_other_map = {}
    for orig, safe in safe_name_map.items():
        if orig == signal_name:
            continue
        pat = re.compile(
            r"PREV\s*\(\s*" + re.escape(safe) + r"\s*\)", re.IGNORECASE
        )
        token = f"__prev_{safe}__"
        if pat.search(safe_formula):
            prev_other_map[orig] = token
            safe_formula = pat.sub(token, safe_formula)

    # HISTORY*(self, period) → __history{func}_self_{period}__
    history_self_specs = []  # (func_name, period, token)
    for func_name in [
        "HISTORYAVG", "HISTORYSUM", "HISTORYCOUNT",
        "HISTORYMAX", "HISTORYMIN", "HISTORYDIFF", "HISTORYGRADIENT",
    ]:
        pat = re.compile(
            func_name + r"\s*\(\s*" + re.escape(safe_self) + r"\s*,\s*(\d+)\s*\)",
            re.IGNORECASE,
        )
        for m in pat.finditer(safe_formula):
            period = int(m.group(1))
            token = f"__hist_{func_name}_{period}__"
            history_self_specs.append((func_name, period, token))
        safe_formula = pat.sub(
            lambda m: f"__hist_{func_name}_{int(m.group(1))}__",
            safe_formula,
        )

    # HISTORY*(other, period) → __history{func}_other_{period}__
    history_other_specs = []  # (func_name, orig_signal, period, token)
    for orig, safe in safe_name_map.items():
        if orig == signal_name:
            continue
        for func_name in [
            "HISTORYAVG", "HISTORYSUM", "HISTORYCOUNT",
            "HISTORYMAX", "HISTORYMIN", "HISTORYDIFF", "HISTORYGRADIENT",
        ]:
            pat = re.compile(
                func_name + r"\s*\(\s*" + re.escape(safe) + r"\s*,\s*(\d+)\s*\)",
                re.IGNORECASE,
            )
            for m in pat.finditer(safe_formula):
                period = int(m.group(1))
                token = f"__hist_{func_name}_{safe}_{period}__"
                history_other_specs.append((func_name, orig, period, token))
            safe_formula = pat.sub(
                lambda m, fn=func_name, s=safe: f"__hist_{fn}_{s}_{int(m.group(1))}__",
                safe_formula,
            )

    # GETPOINT → NaN
    #safe_formula = re.sub(
    #    r"GETPOINT\s*\([^)]*\)", "np.nan", safe_formula, flags=re.IGNORECASE
    #)

    # Компилируем один раз
    compiled = compile(safe_formula, "<streaming_formula>", "eval")

    # =========================================================================
    # Предвычисление HISTORY для НЕ-self сигналов (они полностью известны)
    # =========================================================================
    precomputed_history_other = {}
    for func_name, orig, period, token in history_other_specs:
        arr = col_arrays[orig]
        series = pd.Series(arr, index=index)
        if func_name == "HISTORYAVG":
            rolled = series.rolling(period, min_periods=1).mean()
        elif func_name == "HISTORYSUM":
            rolled = series.rolling(period, min_periods=1).sum()
        elif func_name == "HISTORYCOUNT":
            rolled = series.rolling(period, min_periods=1).count()
        elif func_name == "HISTORYMAX":
            rolled = series.rolling(period, min_periods=1).max()
        elif func_name == "HISTORYMIN":
            rolled = series.rolling(period, min_periods=1).min()
        elif func_name == "HISTORYDIFF":
            r_max = series.rolling(period, min_periods=1).max()
            r_min = series.rolling(period, min_periods=1).min()
            rolled = r_max - r_min
        elif func_name == "HISTORYGRADIENT":
            rolled = _precompute_gradient(series, period)
        else:
            rolled = pd.Series(np.nan, index=index)
        precomputed_history_other[token] = rolled.values

    # Предвычисление PREV для НЕ-self сигналов
    precomputed_prev_other = {}
    for orig, token in prev_other_map.items():
        arr = col_arrays[orig]
        shifted = np.empty(n, dtype=np.float64)
        shifted[0] = np.nan
        shifted[1:] = arr[:-1]
        precomputed_prev_other[token] = shifted

    # =========================================================================
    # Кольцевые буферы для HISTORY*(self)
    # =========================================================================
    ring_buffers = {}
    for func_name, period, token in history_self_specs:
        ring_buffers[token] = {
            "func": func_name,
            "period": period,
            "buffer": np.full(period, np.nan, dtype=np.float64),
            "pos": 0,
            "count": 0,
        }

    def ring_push(rb, value):
        rb["buffer"][rb["pos"]] = value
        rb["pos"] = (rb["pos"] + 1) % rb["period"]
        if rb["count"] < rb["period"]:
            rb["count"] += 1

    def ring_compute(rb):
        buf = rb["buffer"]
        cnt = rb["count"]
        if cnt == 0:
            return np.nan
        window = buf[:cnt] if cnt < rb["period"] else buf
        valid = window[~np.isnan(window)]
        if len(valid) == 0:
            return np.nan

        func = rb["func"]
        if func == "HISTORYAVG":
            return np.mean(valid)
        elif func == "HISTORYSUM":
            return np.sum(valid)
        elif func == "HISTORYCOUNT":
            return float(len(valid))
        elif func == "HISTORYMAX":
            return np.max(valid)
        elif func == "HISTORYMIN":
            return np.min(valid)
        elif func == "HISTORYDIFF":
            return np.max(valid) - np.min(valid)
        elif func == "HISTORYGRADIENT":
            return _scalar_gradient(valid)
        return np.nan

    # =========================================================================
    # Скалярные версии всех функций
    # =========================================================================

    def _safe_float(v):
        if v is None:
            return np.nan
        try:
            f = float(v)
            return f
        except (TypeError, ValueError):
            return np.nan

    def _is_nan(v):
        try:
            return np.isnan(v)
        except (TypeError, ValueError):
            return True

    def WHEN(cond, t_val, f_val):
        try:
            return t_val if bool(cond) else f_val
        except (ValueError, TypeError):
            return np.nan

    def ABS(a):
        a = _safe_float(a)
        return np.abs(a) if not _is_nan(a) else np.nan

    def EXP(a):
        a = _safe_float(a)
        return np.exp(a) if not _is_nan(a) else np.nan

    def POW(a, b):
        a, b = _safe_float(a), _safe_float(b)
        if _is_nan(a) or _is_nan(b):
            return np.nan
        return np.power(a, b)

    def LOG(a):
        a = _safe_float(a)
        return np.log(a) if (not _is_nan(a) and a > 0) else np.nan

    def LOG10(a):
        a = _safe_float(a)
        return np.log10(a) if (not _is_nan(a) and a > 0) else np.nan

    def MIN(*args):
        vals = [_safe_float(a) for a in args]
        vals = [v for v in vals if not _is_nan(v)]
        return min(vals) if vals else np.nan

    def MAX(*args):
        vals = [_safe_float(a) for a in args]
        vals = [v for v in vals if not _is_nan(v)]
        return max(vals) if vals else np.nan

    def AVG(*args):
        vals = [_safe_float(a) for a in args]
        vals = [v for v in vals if not _is_nan(v)]
        return sum(vals) / len(vals) if vals else np.nan

    def MED(*args):
        vals = [_safe_float(a) for a in args]
        vals = [v for v in vals if not _is_nan(v)]
        return float(np.median(vals)) if vals else np.nan

    def ROUND(a, b=0):
        a = _safe_float(a)
        if _is_nan(a):
            return np.nan
        return round(a, int(b))

    def GETPOINT(curveName, pointX, pointY, axisToFind):
        curve_name = str(curveName)
        axis = str(axisToFind).strip().upper()

        df_tbl = TABLE_REGISTRY.get(curve_name)
        if df_tbl is None:
            return np.nan
        try:
            x, y = _get_xy_from_table(df_tbl)
        except Exception:
            return np.nan

        if axis == "Y":
            xq = _safe_float(pointX)
            if _is_nan(xq):
                return np.nan
            return float(_interp_1d(x, y, np.array([xq], dtype=np.float64))[0])

        if axis == "X":
            yq = _safe_float(pointY)
            if _is_nan(yq):
                return np.nan
            return float(_interp_1d(y, x, np.array([yq], dtype=np.float64))[0])

        return np.nan

    # =========================================================================
    # Datetime-массив для HISTORYGRADIENT (нужны временные метки)
    # =========================================================================
    if isinstance(index, pd.DatetimeIndex):
        timestamps_minutes = index.view(np.int64).astype(np.float64) / 1e9 / 60.0
    else:
        timestamps_minutes = np.arange(n, dtype=np.float64)

    # =========================================================================
    # ГЛАВНЫЙ ЦИКЛ — один проход O(n)
    # =========================================================================
    for i in range(n):
        # Базовое окружение
        env = {
            "__builtins__": {},
            "np": np,
            "WHEN": WHEN,
            "ABS": ABS,
            "EXP": EXP,
            "POW": POW,
            "LOG": LOG,
            "LOG10": LOG10,
            "MIN": MIN,
            "MAX": MAX,
            "AVG": AVG,
            "MED": MED,
            "ROUND": ROUND,
            "GETPOINT": GETPOINT,
            # PREV(self)
            "__prev_self__": result[i - 1] if i > 0 else np.nan,
        }
                # --- НОВОЕ: X/Y без кавычек ---
        env["X"] = "X"
        env["Y"] = "Y"

        # --- НОВОЕ: имена таблиц без кавычек ---
        for tbl_name in TABLE_REGISTRY.keys():
            if tbl_name not in env:
                env[tbl_name] = tbl_name

        # Значения всех сигналов на текущем шаге
        for orig_name, safe in safe_name_map.items():
            env[safe] = col_arrays[orig_name][i]

        # Предвычисленные PREV(other)
        for token, arr in precomputed_prev_other.items():
            env[token] = arr[i]

        # Предвычисленные HISTORY*(other)
        for token, arr in precomputed_history_other.items():
            env[token] = arr[i]

        # HISTORY*(self) из кольцевых буферов
        for token, rb in ring_buffers.items():
            env[token] = ring_compute(rb)

        # Вычисляем формулу
        try:
            val = eval(compiled, env)
            result[i] = float(val) if val is not None else np.nan
        except Exception:
            result[i] = np.nan

        # Обновляем кольцевые буферы HISTORY*(self) после вычисления
        for token, rb in ring_buffers.items():
            ring_push(rb, result[i])

    return pd.Series(result, index=index, name=signal_name)


def _scalar_gradient(values: np.ndarray) -> float:
    """Наклон линейной регрессии для окна значений (скалярная версия)."""
    n = len(values)
    if n < 2:
        return np.nan
    x = np.arange(n, dtype=np.float64)
    y = values.astype(np.float64)
    x_mean = x.mean()
    y_mean = y.mean()
    denom = np.sum((x - x_mean) ** 2)
    if denom == 0:
        return np.nan
    return np.sum((x - x_mean) * (y - y_mean)) / denom

def load_table_df(curve_name: str) -> pd.DataFrame:
    cache = st.session_state.tables_cache
    if curve_name in cache:
        return cache[curve_name]

    r = requests.get(f"{api_url}/api/table/file/{curve_name}")
    r.raise_for_status()
    print(BytesIO(r.content))

    df = pd.read_excel(BytesIO(r.content), engine="openpyxl")  # header=0 по умолчанию
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(axis=1, how="all")

    cache[curve_name] = df
    return df


def _precompute_gradient(series: pd.Series, period: int) -> pd.Series:
    """Предвычисляет градиент для полностью известного сигнала (пакетно)."""
    def slope(window):
        valid = window.dropna()
        if len(valid) < 2:
            return np.nan
        x = np.arange(len(valid), dtype=np.float64)
        y = valid.values.astype(np.float64)
        x_m, y_m = x.mean(), y.mean()
        d = np.sum((x - x_m) ** 2)
        if d == 0:
            return np.nan
        return np.sum((x - x_m) * (y - y_m)) / d
    return series.rolling(window=period, min_periods=2).apply(slope, raw=False)




st.set_page_config(page_title="Signal Visualizer", layout="wide")
st.title("📊 Визуализация сигналов")

query_params = st.query_params
session_token = query_params.get("session", None)
api_url = query_params.get("api_url", "http://localhost:8000")

signal_codes = query_params.get("signals", [])
if isinstance(signal_codes, str):
    signal_codes = [signal_codes]

CODE = ""
INITIAL_VISUALIZER_STATE = None  # Состояние из проекта
TABLES = []

if session_token:
    try:
        resp = requests.get(f"{api_url}/api/visualize/session/{session_token}")
        resp.raise_for_status()
        payload = resp.json()
        signal_codes = payload.get("signals", signal_codes)
        CODE = payload.get("code", CODE)
        TABLES = payload.get("tables", [])
        INITIAL_VISUALIZER_STATE = payload.get("visualizer_state")  # НОВОЕ
    except Exception as e:
        st.error(f"Не удалось получить данные сессии: {e}")

# === ИНИЦИАЛИЗАЦИЯ SESSION STATE ===
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
# НОВОЕ: флаг что состояние уже загружено (чтобы не перезаписывать при rerun)
if "state_loaded" not in st.session_state:
    st.session_state.state_loaded = False
# НОВОЕ: флаг что есть несохранённые изменения
if "has_unsaved_changes" not in st.session_state:
    st.session_state.has_unsaved_changes = False
if "tables_cache" not in st.session_state:
    st.session_state.tables_cache = {}


def mark_unsaved():
    """Помечает что есть несохранённые изменения"""
    st.session_state.has_unsaved_changes = True


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
        
                # === ДЕТЕКЦИЯ САМОССЫЛАЮЩИХСЯ СИГНАЛОВ ===
        self_referential_signals = set()
        for name, data in synthetic_signals.items():
            if name in data.get("dependencies", []):  # Прямая самоссылка
                self_referential_signals.add(name)
        
        batch_order = [s for s in computation_order if s not in self_referential_signals]
        streaming_order = [s for s in computation_order if s in self_referential_signals]

        # === ВЫЧИСЛЕНИЕ ПАКЕТНЫХ СИГНАЛОВ (без самоссылок) ===
        if batch_order:
            with st.spinner(f"⚙️ Вычисляем {len(batch_order)} пакетных сигналов..."):
                progress_bar = st.progress(0)
                for idx, syn_name in enumerate(batch_order):
                    syn_data = synthetic_signals[syn_name]
                    formula = syn_data.get("formula", "")
                    # НОВОЕ: автоподгрузка таблиц для вложенного сигнала
                    ensure_tables_for_code(formula)
                    formula = normalize_code_tables(formula)
                    if not formula or df_all.empty:
                        continue
                    try:
                        syn_series = compute_code_signal(
                            formula, df_all,
                            warn_callback=lambda msg, name=syn_name: st.warning(f"[{name}] {msg}", icon="⚠️")
                        )
                        debug_series(syn_name, syn_series, formula)
                        syn_series.name = syn_name
                        df_all[syn_name] = syn_series
                        found_signals.append(syn_name)
                        st.session_state.synthetic_computed[syn_name] = formula
                    except Exception as e:
                        st.error(f"❌ Ошибка '{syn_name}': {e}")
                        not_found_signals.append(syn_name)
                    progress_bar.progress((idx + 1) / len(batch_order))
                progress_bar.empty()

        # === ВЫЧИСЛЕНИЕ ПОТОКОВЫХ СИГНАЛОВ (с самоссылкой) ===
        if streaming_order:
            with st.spinner(f"🌀 Вычисляем {len(streaming_order)} потоковых сигналов..."):
                progress_bar = st.progress(0)
                for idx, syn_name in enumerate(streaming_order):
                    syn_data = synthetic_signals[syn_name]
                    formula = syn_data.get("formula", "")
                    ensure_tables_for_code(formula)
                    formula = normalize_code_tables(formula)
                    if not formula or df_all.empty:
                        not_found_signals.append(syn_name)
                        progress_bar.progress((idx + 1) / len(streaming_order))
                        continue
                    try:
                        #streaming_series = compute_streaming_signal_streaming_forward(
                        #    formula=formula,
                        #    df_base=df_all,
                        #    signal_name=syn_name,
                        #    )
                        streaming_series = compute_streaming_signal(
                            formula=formula,
                            df_base=df_all,
                            signal_name=syn_name,
                        )
                        debug_series(syn_name, streaming_series, formula)
                        df_all[syn_name] = streaming_series
                        found_signals.append(syn_name)
                        st.session_state.synthetic_computed[syn_name] = formula
                        st.info(f"✅ Потоковый сигнал '{syn_name}' вычислен")
                    except Exception as e:
                        st.error(f"❌ Ошибка итеративного расчёта '{syn_name}': {e}")
                        not_found_signals.append(syn_name)
                    progress_bar.progress((idx + 1) / len(streaming_order))
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

if TABLES:
    for t in TABLES:
        try:
            load_table_df(t)
        except Exception as e:
            st.warning(f"⚠️ Таблица '{t}' не загружена: {e}")

    # Передаём таблицы в вычислитель (code_signal.py)
    register_tables(st.session_state.tables_cache)
else:
    print('NO tables')
    register_tables({})


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


# --- синтетический сигнал из CODE ---
code_signal_name = st.session_state.code_signal_name
df_for_code = get_all_signals_df(exclude={code_signal_name} if code_signal_name else None)
code_key = (session_token, CODE)
if CODE:
    ensure_tables_for_code(CODE)

already_have_series = (
    st.session_state.code_signal_name is not None
    and st.session_state.code_signal_name in st.session_state.derived_signals
)

if CODE and df_for_code is not None:
    need_recalc = (st.session_state.get("code_key") != code_key) or (not already_have_series)

    if need_recalc:
        try:
            CODE_FOR_EVAL = normalize_code_tables(CODE)
            synthetic_series = compute_code_signal(
                CODE_FOR_EVAL,
                df_for_code,
                warn_callback=lambda msg: st.warning(msg, icon="⚠️"),
            )
            debug_series("CODE_RESULT", synthetic_series, CODE)
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


# === ЗАГРУЗКА СОХРАНЁННОГО СОСТОЯНИЯ (один раз) ===
df_all_signals = get_all_signals_df()

if not st.session_state.state_loaded and INITIAL_VISUALIZER_STATE and df_all_signals is not None:
    available_signals = set(df_all_signals.columns.tolist())
    
    loaded_selected, loaded_areas, load_warnings = load_visualizer_state(
        INITIAL_VISUALIZER_STATE,
        available_signals
    )
    
    # Применяем загруженное состояние
    if loaded_selected:
        st.session_state.selected_signals = loaded_selected
    if loaded_areas:
        st.session_state.plot_areas = loaded_areas
    
    # Показываем предупреждения
    for warn in load_warnings:
        st.warning(f"⚠️ {warn}")
    
    if loaded_selected or loaded_areas:
        st.info("📂 Загружено сохранённое состояние визуализатора")
    
    st.session_state.state_loaded = True
    st.session_state.has_unsaved_changes = False


# === ФУНКЦИЯ СОХРАНЕНИЯ СОСТОЯНИЯ ===
def save_current_state():
    """Сохраняет текущее состояние на сервер"""
    if not session_token:
        st.error("Нет токена сессии для сохранения")
        return False
    
    state = create_visualizer_state(
        st.session_state.selected_signals,
        st.session_state.plot_areas
    )
    
    try:
        resp = requests.post(
            f"{api_url}/api/visualize/save-state",
            json={
                "session_token": session_token,
                "state": state
            }
        )
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("success"):
            st.session_state.has_unsaved_changes = False
            return True
        else:
            st.error(f"Ошибка сохранения: {result.get('message')}")
            return False
    except Exception as e:
        st.error(f"Ошибка сохранения состояния: {e}")
        return False


# === SIDEBAR ===
with st.sidebar:
    st.header("Выбор сигналов")
    
    # НОВОЕ: Кнопка сохранения состояния
    if session_token:
        save_col1, save_col2 = st.columns([2, 1])
        with save_col1:
            if st.button("💾 Сохранить состояние", use_container_width=True):
                if save_current_state():
                    st.success("✅ Состояние сохранено!")
                    st.info("💡 Теперь сохраните проект в редакторе")
        with save_col2:
            if st.session_state.has_unsaved_changes:
                st.markdown("🔴 *Изменения*")
            else:
                st.markdown("🟢 *Сохранено*")
        st.divider()

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
                if checked and signal not in st.session_state.selected_signals:
                    st.session_state.selected_signals.add(signal)
                    mark_unsaved()
                elif not checked and signal in st.session_state.selected_signals:
                    st.session_state.selected_signals.discard(signal)
                    mark_unsaved()
        
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
                    if checked and signal not in st.session_state.selected_signals:
                        st.session_state.selected_signals.add(signal)
                        mark_unsaved()
                    elif not checked and signal in st.session_state.selected_signals:
                        st.session_state.selected_signals.discard(signal)
                        mark_unsaved()
        
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Все проекта"):
                st.session_state.selected_signals.update(project_signals)
                mark_unsaved()
                st.rerun()
        with col2:
            if st.button("❌ Снять все"):
                st.session_state.selected_signals.clear()
                mark_unsaved()
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
                "cursor_time": None,
                "x_range": None,
                "y_range": None
            })
            mark_unsaved()
            st.rerun()
        if col_b.button("❌ Очистить все"):
            st.session_state.plot_areas = []
            st.session_state.selected_signals = set()
            st.session_state.global_cursor_time = None
            mark_unsaved()
            st.rerun()
    else:
        st.info("📥 Данные сигналов еще не загружены.")


def find_nearest_index_in_range(valid_index, target_time, x_start, x_end):
    """Находит ближайший индекс в заданном диапазоне"""
    mask = (valid_index >= x_start) & (valid_index <= x_end)
    filtered_index = valid_index[mask]
    
    if len(filtered_index) == 0:
        return 0, valid_index[0] if len(valid_index) > 0 else None
    
    if target_time is None:
        return 0, filtered_index[0]
    
    diffs = abs((filtered_index - pd.to_datetime(target_time)).total_seconds())
    min_pos = diffs.argmin()
    return min_pos, filtered_index[min_pos]


# === ОСНОВНАЯ ОБЛАСТЬ ГРАФИКОВ ===
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
                    mark_unsaved()
                    st.rerun()

            selected = st.multiselect(
                "Выберите сигнал(ы):",
                list(st.session_state.selected_signals),
                default=plot_area.get("signals", []),
                key=f"signals_sel_{i}",
            )
            
            # Проверяем изменились ли сигналы
            if set(selected) != set(plot_area.get("signals", [])):
                mark_unsaved()
            st.session_state.plot_areas[i]["signals"] = selected

            if selected:
                df_plot = df_all_signals[selected].copy()
                df_plot_num = df_plot.apply(sanitize_numeric_column)

                valid_index = df_plot_num.dropna(how="all").index
                if len(valid_index) == 0:
                    st.warning("Нет числовых данных для выбранных сигналов.")
                else:
                    full_x_min = valid_index.min()
                    full_x_max = valid_index.max()
                    
                    y_data = df_plot_num.values.flatten()
                    y_data = y_data[~np.isnan(y_data)]
                    full_y_min = float(y_data.min()) if len(y_data) > 0 else 0.0
                    full_y_max = float(y_data.max()) if len(y_data) > 0 else 1.0
                    
                    y_padding = (full_y_max - full_y_min) * 0.05
                    full_y_min -= y_padding
                    full_y_max += y_padding

                    if plot_area.get('x_range') is None:
                        plot_area['x_range'] = [full_x_min, full_x_max]
                    
                    if plot_area.get('y_range') is None:
                        plot_area['y_range'] = [full_y_min, full_y_max]

                    x_start_ts, x_end_ts = plot_area['x_range']
                    mask_visible = (valid_index >= x_start_ts) & (valid_index <= x_end_ts)
                    visible_index = valid_index[mask_visible]
                    
                    if len(visible_index) == 0:
                        st.warning("В выбранном диапазоне X нет данных.")
                    else:
                        if plot_area.get('cursor_time') is None:
                            plot_area['cursor_time'] = visible_index[len(visible_index) // 2]
                        
                        cursor_time = plot_area['cursor_time']
                        if cursor_time < x_start_ts or cursor_time > x_end_ts:
                            cursor_time = visible_index[len(visible_index) // 2]
                            plot_area['cursor_time'] = cursor_time
                        
                        cursor_pos, _ = find_nearest_index_in_range(
                            visible_index, cursor_time, x_start_ts, x_end_ts
                        )
                        
                        if st.session_state.global_cursor_time is not None:
                            global_cursor = st.session_state.global_cursor_time
                            if x_start_ts <= global_cursor <= x_end_ts:
                                cursor_pos, cursor_time = find_nearest_index_in_range(
                                    visible_index, global_cursor, x_start_ts, x_end_ts
                                )
                                plot_area['cursor_time'] = cursor_time
                        
                        ts_idx = st.slider(
                            "📍 Вертикальная линия (в видимом диапазоне)",
                            min_value=0,
                            max_value=len(visible_index) - 1,
                            value=min(cursor_pos, len(visible_index) - 1),
                            key=f"vline_slider_{i}",
                            help="Слайдер работает только в рамках текущего видимого диапазона X"
                        )
                        
                        ts = visible_index[ts_idx]
                        plot_area['cursor_time'] = ts
                        
                        col_pos, col_sync = st.columns([3, 1])
                        with col_pos:
                            st.markdown(f"**📅 Позиция линии:** `{ts.strftime('%Y-%m-%d %H:%M:%S')}`")
                        with col_sync:
                            if st.button("🔄 Синхронизировать все", key=f"sync_{i}"):
                                st.session_state.global_cursor_time = ts
                                for pa in st.session_state.plot_areas:
                                    pa['cursor_time'] = ts
                                st.rerun()

                        fig = px.line(
                            df_plot_num,
                            x=df_plot_num.index,
                            y=selected,
                            title=f"График #{plot_area['id']}",
                            render_mode="webgl"
                        )
                        
                        fig.add_vline(x=ts, line_width=2, line_dash="dash", line_color="red")
                        
                        shapes = plot_area.get('shapes', [])
                        for shape in shapes:
                            if shape['type'] == 'vline':
                                fig.add_vline(x=shape['x'], line_dash=shape['dash'], line_color=shape['color'], line_width=1)
                            elif shape['type'] == 'hline':
                                fig.add_hline(y=shape['y'], line_dash=shape['dash'], line_color=shape['color'], line_width=1)
                        
                        fig.update_layout(
                            uirevision=f"plot_area_{plot_area['id']}",
                            height=600,
                            legend_title_text="Сигналы",
                            xaxis_title="Время",
                            yaxis_title="Значение",
                            margin=dict(l=20, r=20, t=40, b=20),
                            xaxis=dict(
                                range=[x_start_ts, x_end_ts],
                                rangeslider=dict(
                                    visible=True,
                                    thickness=0.08,
                                    bgcolor='#e0e0e0',
                                    range=[full_x_min, full_x_max]
                                )
                            ),
                            yaxis=dict(
                                range=plot_area['y_range'],
                                fixedrange=False
                            )
                        )
                        
                        st.plotly_chart(fig, use_container_width=True)

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
                                    mark_unsaved()
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
                                    mark_unsaved()
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
                                    mark_unsaved()
                                    st.rerun()

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
        # === ОБЛАКО ТОЧЕК X–Y + АППРОКСИМАЦИЯ (всегда внизу) ===
        # === ОБЛАКО ТОЧЕК X–Y + АППРОКСИМАЦИЯ (всегда внизу) ===
        if df_all_signals is not None:
            # Доступны только отмеченные галочками сигналы + CODE_RESULT (если есть)
            available_cols = df_all_signals.columns.tolist()
            selected_set = st.session_state.get("selected_signals", set())
            available_signals = [c for c in available_cols if c in selected_set]

            code_sig = st.session_state.get("code_signal_name")
            if code_sig and code_sig in available_cols and code_sig not in available_signals:
                available_signals.append(code_sig)

            st.divider()
            st.subheader("🔷 Облако точек X–Y + аппроксимация")

            # Нужно минимум два сигнала для выбора осей
            if len(available_signals) < 2:
                st.info("Отметьте минимум два сигнала слева (включая при желании CODE_RESULT), чтобы выбрать X и Y.")
            else:
                # Значения по умолчанию: X — первый доступный; Y — CODE_RESULT, если есть, иначе второй
                default_x_idx = 0
                default_y_idx = 1
                if code_sig and code_sig in available_signals:
                    default_y_idx = available_signals.index(code_sig)

                col_sel = st.columns(3)
                with col_sel[0]:
                    x_sig = st.selectbox(
                        "Ось X",
                        available_signals,
                        index=min(default_x_idx, len(available_signals) - 1),
                        key="xy_x_sig"
                    )
                with col_sel[1]:
                    y_sig = st.selectbox(
                        "Ось Y",
                        available_signals,
                        index=min(default_y_idx, len(available_signals) - 1),
                        key="xy_y_sig"
                    )
                with col_sel[2]:
                    max_points = st.number_input(
                        "Макс. точек на графике",
                        min_value=100,
                        max_value=500_000,
                        value=50_000,
                        step=100,
                        help="Для ускорения визуализации при больших наборах",
                        key="xy_max_points"
                    )

                # Базовые серии и предварительные минимумы/максимумы
                x_series = sanitize_numeric_column(df_all_signals[x_sig])
                y_series = sanitize_numeric_column(df_all_signals[y_sig])

                x_min_default = float(x_series.dropna().min()) if x_series.dropna().size > 0 else 0.0
                x_max_default = float(x_series.dropna().max()) if x_series.dropna().size > 0 else 1.0
                y_min_default = float(y_series.dropna().min()) if y_series.dropna().size > 0 else 0.0
                y_max_default = float(y_series.dropna().max()) if y_series.dropna().size > 0 else 1.0

                # UI фильтров диапазона (каждая граница опциональна)
                st.markdown("**Фильтр диапазонов значений (опционально):**")
                col_f = st.columns(2)

                with col_f[0]:
                    st.markdown(f"**Фильтр по X: {x_sig}**")
                    x_min_en = st.checkbox("Задать минимум X", value=False, key="xy_x_min_en")
                    x_min_val = st.number_input("Минимум X", value=x_min_default, key="xy_x_min", disabled=not x_min_en)
                    x_max_en = st.checkbox("Задать максимум X", value=False, key="xy_x_max_en")
                    x_max_val = st.number_input("Максимум X", value=x_max_default, key="xy_x_max", disabled=not x_max_en)

                with col_f[1]:
                    st.markdown(f"**Фильтр по Y: {y_sig}**")
                    y_min_en = st.checkbox("Задать минимум Y", value=False, key="xy_y_min_en")
                    y_min_val = st.number_input("Минимум Y", value=y_min_default, key="xy_y_min", disabled=not y_min_en)
                    y_max_en = st.checkbox("Задать максимум Y", value=False, key="xy_y_max_en")
                    y_max_val = st.number_input("Максимум Y", value=y_max_default, key="xy_y_max", disabled=not y_max_en)

                # Формируем пары (X(t), Y(t)) по совпадающим меткам времени, сразу сбрасываем NaN
                xy_df = pd.DataFrame({x_sig: x_series, y_sig: y_series}).dropna()

                original_count = len(xy_df)

                # Применяем фильтры: каждая граница учитывается только если включена
                if x_min_en:
                    xy_df = xy_df[xy_df[x_sig] >= x_min_val]
                if x_max_en:
                    xy_df = xy_df[xy_df[x_sig] <= x_max_val]
                if y_min_en:
                    xy_df = xy_df[xy_df[y_sig] >= y_min_val]
                if y_max_en:
                    xy_df = xy_df[xy_df[y_sig] <= y_max_val]

                filtered_count = len(xy_df)

                # Подвыборка после фильтрации — для ускорения рендера
                if filtered_count > max_points:
                    step = max(1, filtered_count // max_points)
                    xy_df = xy_df.iloc[::step, :]
                    # после подвыборки не меняем filtered_count, он отражает объём данных для аппроксимации

                if xy_df.empty:
                    st.warning("После применения фильтров не осталось валидных точек для отображения.")
                else:
                    x_vals = xy_df[x_sig].to_numpy(dtype=np.float64)
                    y_vals = xy_df[y_sig].to_numpy(dtype=np.float64)

                    st.caption(f"Точек всего: {original_count} | после фильтра: {filtered_count} | отрисовано: {len(xy_df)}")

                    # Настройки аппроксимации
                    fit_type = st.selectbox(
                        "Тип аппроксимации",
                        ["Без аппроксимации", "Линейная (y = a + b·X)", "Полиномиальная степень N", "Степенная (y = a·X^p)"],
                        key="xy_fit_type"
                    )

                    poly_degree = None
                    if "Полиномиальная" in fit_type:
                        poly_degree = st.slider(
                            "Степень полинома (N)",
                            min_value=2, max_value=8, value=2, step=1,
                            key="xy_poly_deg"
                        )

                    # Облако точек
                    fig_scatter = px.scatter(
                        xy_df, x=x_sig, y=y_sig,
                        title=f"Облако точек: {x_sig} → {y_sig}",
                        render_mode="webgl",
                        opacity=0.75
                    )

                    # Диапазон X для кривой
                    x_min = float(np.nanmin(x_vals))
                    x_max = float(np.nanmax(x_vals))
                    x_grid = np.linspace(x_min, x_max, 500)

                    info_lines = []
                    if fit_type.startswith("Линейная"):
                        fit = fit_linear(x_vals, y_vals)
                        a, b, r2 = fit["a"], fit["b"], fit["r2"]
                        y_line = a + b * x_grid
                        fig_scatter.add_trace(go.Scatter(x=x_grid, y=y_line, mode="lines", name="Линейная аппр.", line=dict(color="red", width=2)))
                        info_lines.append(f"Модель: {y_sig} = {a:.6g} + {b:.6g}·{x_sig}")
                        info_lines.append(f"R² = {r2:.4f}" if not np.isnan(r2) else "R² недоступен")

                    elif fit_type.startswith("Полиномиальная"):
                        fit = fit_polynomial(x_vals, y_vals, degree=poly_degree or 2)
                        coeffs, r2 = fit["coeffs"], fit["r2"]
                        if coeffs is None:
                            info_lines.append("Недостаточно точек для выбранной степени полинома.")
                        else:
                            y_line = np.polyval(coeffs, x_grid)
                            fig_scatter.add_trace(go.Scatter(x=x_grid, y=y_line, mode="lines", name=f"Полином N={poly_degree}", line=dict(color="orange", width=2)))
                            info_lines.append(_format_poly_equation(coeffs, x_sig, y_sig))
                            info_lines.append(f"R² = {r2:.4f}" if not np.isnan(r2) else "R² недоступен")

                    elif fit_type.startswith("Степенная"):
                        fit = fit_power_law(x_vals, y_vals)  # учитывает только X>0 и Y>0
                        a, p, r2 = fit["a"], fit["p"], fit["r2"]
                        used = fit.get("used_points", 0)
                        if np.isnan(a) or np.isnan(p):
                            info_lines.append("Для степенной аппроксимации нужны пары с X>0 и Y>0.")
                        else:
                            x_pos = x_grid[x_grid > 0]
                            y_line = a * np.power(x_pos, p)
                            fig_scatter.add_trace(go.Scatter(x=x_pos, y=y_line, mode="lines", name="Степенная аппр.", line=dict(color="green", width=2)))
                            info_lines.append(f"Модель: {y_sig} = {a:.6g}·{x_sig}^{p:.6g}")
                            info_lines.append(f"Использовано точек: {used}")
                            info_lines.append(f"R² = {r2:.4f}" if not np.isnan(r2) else "R² недоступен")

                    fig_scatter.update_layout(
                        uirevision="xy_scatter_fixed_bottom",
                        height=600,
                        xaxis_title=x_sig,
                        yaxis_title=y_sig,
                        margin=dict(l=20, r=20, t=40, b=20),
                        legend_title_text="",
                    )

                    st.plotly_chart(fig_scatter, use_container_width=True)

                    if info_lines:
                        st.markdown("**Аппроксимация:**")
                        for line in info_lines:
                            st.markdown(f"- {line}")
        



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