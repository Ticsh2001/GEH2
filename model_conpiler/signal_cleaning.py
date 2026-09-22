"""
signal_cleaning.py — статистическая чистка входных сигналов.

"""
import numpy as np

__version__ = "1.0"


# Коэффициент перехода MAD -> sigma для нормального распределения:
# Hampel использует 1.4826 * MAD как робастную оценку стандартного
# отклонения; порог = k * 1.4826 * MAD.
_MAD_TO_SIGMA = 1.4826


def _detect_outliers_causal(values, window_size, k):
    """Causal Hampel: пометить точки, далёкие от локальной медианы.

    values      : np.ndarray (N,) float, может содержать NaN.
    window_size : сколько ПРЕДЫДУЩИХ точек использовать для оценки.
    k           : порог в единицах 1.4826 * MAD.

    Возвращает np.ndarray (N,) bool: True = выброс.

    Правила:
      - Для точки i окно = values[i-window_size : i] (строго в прошлом).
      - Если i < window_size — детекция не выполняется.
      - Из окна исключаются NaN.
      - Если в окне после фильтрации NaN осталось < 3 точек — пропускаем.
      - Если MAD окна = 0 — пропускаем точку (нет базы для суждения).
      - Сама точка i в вычислениях медианы/MAD не участвует.
    """
    n = len(values)
    mask = np.zeros(n, dtype=bool)

    for i in range(window_size, n):
        window = values[i - window_size:i]
        window = window[np.isfinite(window)]
        if window.size < 3:
            continue

        x = values[i]
        if not np.isfinite(x):
            # Уже NaN — нечего проверять, оставляем как есть.
            continue

        med = np.median(window)
        mad = np.median(np.abs(window - med))
        if mad == 0.0:
            continue

        threshold = k * _MAD_TO_SIGMA * mad
        if abs(x - med) > threshold:
            mask[i] = True

    return mask


def clean_signals(signals, *, hampel_window=7, hampel_k=3.0):
    """Почистить словарь сигналов причинным Hampel-фильтром.

    Параметры
    ---------
    signals : dict[str, np.ndarray]
        {KKS: массив (N, 2)}. Столбец 0 — datetime (в любом формате,
        который потом распарсит сессия), столбец 1 — значения.
    hampel_window : int
        Размер скользящего окна в точках (только прошлое). По умолчанию 7.
    hampel_k : float
        Порог чувствительности: k * 1.4826 * MAD. По умолчанию 3.

    Возвращает
    ----------
    cleaned : dict[str, np.ndarray]
        Те же массивы, но значения выбросов заменены на NaN. Timestamps
        не тронуты, длина сохранена.
    report : dict
        {KKS: {
            "total_points": int,
            "input_nan":    int,       # сколько NaN/нечисловых пришло
            "masked":       int,       # сколько помечено как выбросы
            "mask":         ndarray bool (N,),
        }}
    """
    if hampel_window < 1:
        raise ValueError("hampel_window должен быть >= 1")
    if hampel_k <= 0:
        raise ValueError("hampel_k должен быть > 0")

    cleaned = {}
    report = {}

    for kks, arr in signals.items():
        arr = np.asarray(arr)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError(
                "Сигнал %r должен иметь форму (N, 2); получено %r"
                % (kks, arr.shape))

        ts = arr[:, 0]
        raw_vals = arr[:, 1]

        # Приводим значения к float; всё, что не число или не конечно — NaN.
        vals = np.full(len(raw_vals), np.nan, dtype=float)
        input_nan = 0
        for j, v in enumerate(raw_vals):
            try:
                f = float(v)
            except (TypeError, ValueError):
                input_nan += 1
                continue
            if np.isfinite(f):
                vals[j] = f
            else:
                input_nan += 1

        # Причинный Hampel по столбцу значений.
        mask = _detect_outliers_causal(
            vals, int(hampel_window), float(hampel_k))

        # Заменяем помеченные значения на NaN; остальные не трогаем.
        out_vals = vals.copy()
        out_vals[mask] = np.nan

        # Собираем (N, 2) обратно с сохранением timestamps.
        cleaned_arr = np.empty((len(ts), 2), dtype=object)
        cleaned_arr[:, 0] = ts
        cleaned_arr[:, 1] = out_vals
        cleaned[kks] = cleaned_arr

        report[kks] = {
            "total_points": int(len(ts)),
            "input_nan": int(input_nan),
            "masked": int(mask.sum()),
            "mask": mask,
        }

    return cleaned, report


def report_to_jsonable(report):
    """Преобразовать отчёт к JSON-сериализуемому виду.

    bool-массивы `mask` заменяются на список индексов True-позиций
    (masked_indices), потому что numpy.bool_ не сериализуется напрямую.
    """
    out = {}
    for kks, r in report.items():
        out[kks] = {
            "total_points": int(r["total_points"]),
            "input_nan": int(r["input_nan"]),
            "masked": int(r["masked"]),
            "masked_indices": [int(i) for i in np.where(r["mask"])[0]],
        }
    return out