#code_signal.py

import re
from typing import List, Tuple, Dict

import numpy as np
import pandas as pd


def _knn_interpolate(features: np.ndarray, targets: np.ndarray, query_points: np.ndarray, k: int = 5) -> np.ndarray:
    """
    KNN-интерполяция для многомерных данных.
    
    Args:
        features: массив признаков размера (n_samples, n_features)
        targets: массив целевых значений размера (n_samples,)
        query_points: точки для интерполяции размера (n_queries, n_features)  
        k: количество ближайших соседей
        
    Returns:
        интерполированные значения размера (n_queries,)
    """
    n_samples = features.shape[0]
    n_queries = query_points.shape[0]
    k = min(k, n_samples)  # k не может быть больше количества доступных точек
    
    results = np.zeros(n_queries)
    
    for i, query in enumerate(query_points):
        # Вычисляем евклидовы расстояния до всех точек
        distances = np.sqrt(np.sum((features - query) ** 2, axis=1))
        
        # Находим k ближайших соседей
        nearest_indices = np.argpartition(distances, k)[:k]
        nearest_distances = distances[nearest_indices]
        nearest_targets = targets[nearest_indices]
        
        # Обрабатываем случай нулевых расстояний (точное совпадение)
        zero_dist_mask = nearest_distances == 0
        if np.any(zero_dist_mask):
            # Если есть точные совпадения, используем их среднее
            results[i] = np.mean(nearest_targets[zero_dist_mask])
        else:
            # Взвешенное среднее с весами обратно пропорциональными расстояниям
            weights = 1.0 / (nearest_distances + 1e-10)  # добавляем малое число для численной стабильности
            weights = weights / np.sum(weights)  # нормализуем веса
            results[i] = np.sum(weights * nearest_targets)
    
    return results


TABLE_REGISTRY: Dict[str, pd.DataFrame] = {}

def register_tables(tables: Dict[str, pd.DataFrame]):
    TABLE_REGISTRY.clear()
    TABLE_REGISTRY.update(tables or {})


def _get_xy_from_table(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    cols = list(df.columns)
    if len(cols) < 2:
        raise CodeEvaluationError("GETPOINT: таблица должна иметь минимум 2 колонки (X и Y).")

    # если есть колонки X/Y (без учёта регистра) — используем их
    lower = {str(c).strip().lower(): c for c in cols}
    if "x" in lower and "y" in lower:
        cx, cy = lower["x"], lower["y"]
    else:
        cx, cy = cols[0], cols[1]

    x = sanitize_numeric_column(df[cx]).to_numpy(dtype=np.float64)
    y = sanitize_numeric_column(df[cy]).to_numpy(dtype=np.float64)

    mask = ~np.isnan(x) & ~np.isnan(y)
    x, y = x[mask], y[mask]
    if x.size < 2:
        raise CodeEvaluationError("GETPOINT: недостаточно точек для интерполяции (нужно >= 2).")
    return x, y

def _interp_1d(x: np.ndarray, y: np.ndarray, xq: np.ndarray) -> np.ndarray:
    order = np.argsort(x)
    xs = x[order]
    ys = y[order]
    return np.interp(xq, xs, ys)  # линейная интерполяция, clamp по краям


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

    def GETPOINT(curveName, pointX, pointY, axisToFind):
        curve_name = str(curveName)
        axis = str(axisToFind).strip().upper()

        df_tbl = TABLE_REGISTRY.get(curve_name)
        if df_tbl is None:
            if "GETPOINT" not in warnings:
                warnings.append(f"GETPOINT: таблица '{curve_name}' не загружена — NaN.")
            return pd.Series(np.nan, index=index)

        try:
            x, y = _get_xy_from_table(df_tbl)
        except Exception as e:
            if "GETPOINT" not in warnings:
                warnings.append(f"GETPOINT: ошибка таблицы '{curve_name}': {e}")
            return pd.Series(np.nan, index=index)

        if axis == "Y":
            xq = _ensure_series(pointX).values.astype(np.float64)
            out = _interp_1d(x, y, xq)
            return pd.Series(out, index=index)

        if axis == "X":
            yq = _ensure_series(pointY).values.astype(np.float64)
            out = _interp_1d(y, x, yq)
            return pd.Series(out, index=index)

        if "GETPOINT" not in warnings:
            warnings.append("GETPOINT: axisToFind должен быть 'X' или 'Y' — NaN.")
        return pd.Series(np.nan, index=index)
    
    def INTERPOLATE(interpolationTableName, targetColumnName, *values):
        """
        Функция многомерной интерполяции с использованием KNN-регрессии.
        
        Args:
            interpolationTableName: имя интерполяционной таблицы
            targetColumnName: имя столбца для интерполяции  
            *values: значения параметров для интерполяции (порядок как в таблице, кроме targetColumnName)
            
        Returns:
            pd.Series с интерполированными значениями
        """
        table_name = str(interpolationTableName)
        target_col = str(targetColumnName)
        
        # Получаем таблицу из реестра
        df_table = TABLE_REGISTRY.get(table_name)
        if df_table is None:
            if "INTERPOLATE" not in warnings:
                warnings.append(f"INTERPOLATE: таблица '{table_name}' не загружена — возвращается NaN.")
            return pd.Series(np.nan, index=index)
        
        try:
            # Проверяем наличие целевого столбца
            if target_col not in df_table.columns:
                available_cols = list(df_table.columns)
                if "INTERPOLATE" not in warnings:
                    warnings.append(f"INTERPOLATE: столбец '{target_col}' не найден в таблице '{table_name}'. Доступные: {available_cols}")
                return pd.Series(np.nan, index=index)
            
            # Получаем все столбцы кроме целевого (это будут признаки для интерполяции)
            feature_columns = [col for col in df_table.columns if col != target_col]
            
            if len(feature_columns) == 0:
                if "INTERPOLATE" not in warnings:
                    warnings.append(f"INTERPOLATE: в таблице '{table_name}' нет столбцов-признаков (все столбцы кроме '{target_col}').")
                return pd.Series(np.nan, index=index)
            
            # Проверяем соответствие количества параметров
            if len(values) != len(feature_columns):
                if "INTERPOLATE" not in warnings:
                    warnings.append(f"INTERPOLATE: количество параметров ({len(values)}) не совпадает с количеством столбцов-признаков ({len(feature_columns)}) в таблице '{table_name}'. Ожидаемый порядок: {feature_columns}")
                return pd.Series(np.nan, index=index)
            
            # Подготавливаем данные таблицы
            df_clean = df_table.copy()
            
            # Санитизируем все столбцы
            for col in df_clean.columns:
                df_clean[col] = sanitize_numeric_column(df_clean[col])
            
            # Удаляем строки с NaN значениями
            df_clean = df_clean.dropna()
            
            if len(df_clean) < 2:
                if "INTERPOLATE" not in warnings:
                    warnings.append(f"INTERPOLATE: недостаточно валидных строк в таблице '{table_name}' (нужно >= 2).")
                return pd.Series(np.nan, index=index)
            
            # Извлекаем признаки и цели из таблицы
            features_table = df_clean[feature_columns].values  # shape: (n_samples, n_features)
            targets_table = df_clean[target_col].values       # shape: (n_samples,)
            
            # Преобразуем входные параметры в массивы Series
            input_series = []
            for i, val in enumerate(values):
                series_val = _ensure_series(val)
                input_series.append(series_val.values)
            
            # Создаем матрицу запросов: каждая строка - одна временная точка
            n_points = len(index)
            n_features = len(feature_columns)
            query_matrix = np.zeros((n_points, n_features))
            
            for i in range(n_features):
                query_matrix[:, i] = input_series[i]
            
            # Проверяем на NaN в запросах
            valid_mask = ~np.any(np.isnan(query_matrix), axis=1)
            
            # Выполняем интерполяцию
            results = np.full(n_points, np.nan)
            
            if np.any(valid_mask):
                valid_queries = query_matrix[valid_mask]
                
                # Определяем оптимальное k (но не больше 10 для производительности)
                k = min(5, len(df_clean), max(2, len(df_clean) // 3))
                
                try:
                    interpolated_values = _knn_interpolate(
                        features_table, 
                        targets_table, 
                        valid_queries, 
                        k=k
                    )
                    results[valid_mask] = interpolated_values
                except Exception as e:
                    if "INTERPOLATE" not in warnings:
                        warnings.append(f"INTERPOLATE: ошибка во время интерполяции для таблицы '{table_name}': {e}")
                    return pd.Series(np.nan, index=index)
            
            return pd.Series(results, index=index)
            
        except Exception as e:
            if "INTERPOLATE" not in warnings:
                warnings.append(f"INTERPOLATE: общая ошибка для таблицы '{table_name}': {e}")
            return pd.Series(np.nan, index=index)

    def PREV(param):
        s = _history_series(param)
        if s is None:
            return pd.Series(np.nan, index=index)
        return s.shift(1)

    def _history_series(param):
        # 1) Если уже Series — используем её
        if isinstance(param, pd.Series):
            return sanitize_numeric_column(param).reindex(index)

        # 2) Если пришло "безопасное имя" (SIG_...) — оно уже есть в env как Series.
        # Но сюда оно попадёт только если пользователь передал строку "SIG_0".
        if isinstance(param, str):
            # сначала пробуем как исходное имя сигнала
            if param in series_map:
                return series_map[param]

            # потом пробуем как safe-name
            for orig, safe in safe_name_map.items():
                if param == safe:
                    return series_map.get(orig)

        return None

    def _history_window(period):
        # ИСПРАВЛЕНИЕ: если period - это pandas Series, берём первое значение
        if isinstance(period, pd.Series):
            first_val = period.iloc[0] if len(period) > 0 else np.nan
            if pd.isna(first_val):
                return None
            period = first_val
        
        try:
            minutes = int(period)
        except (TypeError, ValueError):
            return None
        if minutes <= 0:
            return None
        print(f"[DEBUG] {minutes}min")
        return f"{minutes}min"

    def _history_apply(param, period, fn):
        s = _history_series(param)
        window = _history_window(period)
        if s is None or window is None:
            return pd.Series(np.nan, index=index)

        # 1) Если datetime-индекс — используем time-based rolling
        if isinstance(s.index, (pd.DatetimeIndex, pd.TimedeltaIndex, pd.PeriodIndex)):
            return fn(s.rolling(window, min_periods=1))

        # 2) Иначе пробуем интерпретировать period как "кол-во точек"
        try:
            n = int(period)
            if n <= 0:
                return pd.Series(np.nan, index=index)
            return fn(s.rolling(window=n, min_periods=1))
        except Exception:
            return pd.Series(np.nan, index=index)

    HISTORYAVG = lambda n, p: _history_apply(n, p, lambda r: r.mean())
    HISTORYCOUNT = lambda n, p: _history_apply(n, p, lambda r: r.count())
    HISTORYSUM = lambda n, p: _history_apply(n, p, lambda r: r.sum())
    HISTORYMAX = lambda n, p: _history_apply(n, p, lambda r: r.max())
    HISTORYMIN = lambda n, p: _history_apply(n, p, lambda r: r.min())
    HISTORYDIFF = lambda n, p: _history_apply(n, p, lambda r: r.max() - r.min())

# code_signal.py

    def HISTORYAVG_DEBUG(param, period):
        print(f"\n[HISTORYAVG] Вызов с param={type(param)}, period={type(period)}")
        
        # Показываем что пришло в аргументах
        if isinstance(param, pd.Series):
            print(f"[HISTORYAVG] param Series: размер={len(param)}, первые значения={param.head(3).tolist()}")
        else:
            print(f"[HISTORYAVG] param не Series: {param}")
            
        if isinstance(period, pd.Series):
            print(f"[HISTORYAVG] period Series: размер={len(period)}, значения={period.unique()}")
        else:
            print(f"[HISTORYAVG] period не Series: {period}")
        
        # Вызываем исходную логику
        result = _history_apply(param, period, lambda r: r.mean())
        
        if isinstance(result, pd.Series):
            print(f"[HISTORYAVG] Результат: размер={len(result)}")
            print(f"[HISTORYAVG] Первые 5 значений: {result.head(5).tolist()}")
            print(f"[HISTORYAVG] Уникальные значения: {result.dropna().unique()[:10]}")  # первые 10 уникальных
            print(f"[HISTORYAVG] Есть ли NaN: {result.isna().any()}, количество NaN: {result.isna().sum()}")
        else:
            print(f"[HISTORYAVG] Результат не Series: {result}")
        
        return result

    def HISTORYGRADIENT(param_name, period):
        """
        Возвращает коэффициент наклона (a) линейной регрессии y = a*x + b
        по значениям param_name за предшествующие `period` минут.

        Поддерживает:
        - datetime-индекс: period интерпретируется как минуты (time-based rolling)
            и наклон возвращается в единицах "значение за минуту".
        - non-datetime индекс: period интерпретируется как количество точек (integer window),
            и наклон возвращается в "значение за индексный шаг".
        """
        s = _history_series(param_name)
        if s is None:
            return pd.Series(np.nan, index=index)
        
        period_val = period
        if isinstance(period, pd.Series):
            period_val = period.iloc[0] if len(period) > 0 else np.nan
            if pd.isna(period_val):
                return pd.Series(np.nan, index=index)

        # проверяем period
        try:
            minutes = int(period_val)
        except Exception:
            return pd.Series(np.nan, index=index)

        if minutes <= 0:
            return pd.Series(np.nan, index=index)

        # функция, вычисляющая наклон по подсерии (сработает для любого окна)
        def slope(window_series: pd.Series):
            valid = window_series.dropna()
            if len(valid) < 2:
                return np.nan

            # x: времена в минутах (если datetime), иначе последовательные индексы
            if isinstance(valid.index, (pd.DatetimeIndex, pd.TimedeltaIndex, pd.PeriodIndex)):
                # индекс в nanoseconds -> в минуты: /1e9 (сек) / 60
                x = valid.index.view(np.int64).astype(float) / 1e9 / 60.0
            else:
                # используем относительные индексы 0..n-1 (шаги)
                x = np.arange(len(valid), dtype=float)

            y = valid.values.astype(float)

            x_mean = x.mean()
            y_mean = y.mean()
            denom = np.sum((x - x_mean) ** 2)
            if denom == 0:
                return np.nan

            num = np.sum((x - x_mean) * (y - y_mean))
            return num / denom

        # Выбираем rolling: если datetime-индекс — time-based, иначе window по числу точек
        if isinstance(s.index, (pd.DatetimeIndex, pd.TimedeltaIndex, pd.PeriodIndex)):
            window = f"{minutes}min"
            rolling = s.rolling(window=window, min_periods=2)
        else:
            rolling = s.rolling(window=minutes, min_periods=2)

        # Возвращаем Series с применённой функцией
        return rolling.apply(slope, raw=False)


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
        "EXP": lambda a: pd.Series(np.exp(_ensure_series(a).values), index=index),
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
        "LOG": lambda x: pd.Series(np.log(_ensure_series(x).values), index=index),
        # Логарифм по основанию 10 (если нужен)
        "LOG10": lambda x: pd.Series(np.log10(_ensure_series(x).values), index=index),
        "PREV": PREV,
        "HISTORYAVG": HISTORYAVG,
        "HISTORYCOUNT": HISTORYCOUNT,
        "HISTORYSUM": HISTORYSUM,
        "HISTORYMAX": HISTORYMAX,
        "HISTORYMIN": HISTORYMIN,
        "HISTORYDIFF": HISTORYDIFF,
        "HISTORYGRADIENT": HISTORYGRADIENT,
        "GETPOINT": GETPOINT,
        "INTERPOLATE": INTERPOLATE,
    }
    env["X"] = "X"
    env["Y"] = "Y"

    for original_name, safe_name in safe_name_map.items():
        env[safe_name] = series_map[original_name]

    for tbl_name in TABLE_REGISTRY.keys():
        # если вдруг совпало с safe-именем сигнала — не перетираем сигнал
        if tbl_name not in env:
            env[tbl_name] = tbl_name

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