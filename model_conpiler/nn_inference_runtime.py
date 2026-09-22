"""
nn_inference_runtime — единая среда инференса моделей.
"""
__version__ = "1.0"

import json
import os

import numpy as np
import pandas as pd
import tensorflow as tf
from datetime import datetime, timezone


# =============================================================================
# Методы обработки сигналов
# =============================================================================

def build_dataset(signals_data, ref_signal, interpolation='linear'):
    """Выровнять все сигналы по временной шкале опорного и интерполировать.

    signals_data : {имя: DataFrame[datetime, value]}
    ref_signal   : ключ в signals_data, чья шкала — опорная
    interpolation: 'linear' | 'nearest' | 'cubic'
    """
    if not signals_data:
        raise ValueError("Нет данных для построения датасета")

    df_ref = signals_data[ref_signal][['datetime']].drop_duplicates().sort_values('datetime')
    df_ref = df_ref.set_index('datetime')

    result = df_ref.copy()
    for name, df in signals_data.items():
        df = df.drop_duplicates(subset='datetime').set_index('datetime')
        aligned = df.reindex(result.index)
        aligned['value'] = pd.to_numeric(aligned['value'], errors='coerce')

        if interpolation == 'linear':
            aligned = aligned.interpolate(method='time', limit_direction='both')
        elif interpolation == 'nearest':
            aligned = aligned.interpolate(method='nearest', limit_direction='both')
        elif interpolation == 'cubic':
            aligned = aligned.interpolate(method='cubic', limit_direction='both')

        aligned = aligned.ffill().bfill()
        result[name] = pd.to_numeric(aligned['value'], errors='coerce')

    for col in result.columns:
        if col != 'datetime':
            result[col] = pd.to_numeric(result[col], errors='coerce')

    return result.reset_index()


def _collect_range_violations(df, ts, range_checks):
    """Для каждой строки df собрать список нарушений границ.

    Возвращает list длиной len(df), где элемент i — список dict'ов:
        {"column": str, "value": float, "min": float|None,
         "max": float|None, "timestamp": str}
    """
    violations = [[] for _ in range(len(df))]
    if not range_checks:
        return violations

    fmt = None

    for rule in range_checks:
        col = rule.get("column")
        if col not in df.columns:
            continue
        mn = rule.get("min")
        mx = rule.get("max")
        numeric = pd.to_numeric(df[col], errors="coerce")
        valid = numeric.notna() & np.isfinite(numeric)
        for i in np.where(valid)[0]:
            v = float(numeric.iloc[i])
            bad = False
            if mn is not None and v < mn:
                bad = True
            if mx is not None and v > mx:
                bad = True
            if not bad:
                continue
            if fmt is None:
                # Определяем формат по ts один раз
                fmt = "%d.%m.%Y %H:%M:%S"
            violations[i].append({
                "column": col,
                "value": v,
                "min": mn,
                "max": mx,
                "timestamp": pd.Timestamp(ts.iloc[i]).strftime(fmt),
            })
    return violations


 

def filter_dataset(df, rules):
    """Убрать строки вне [min, max]; опционально min-max нормализация.
    """
    df = df.copy()
    for rule in rules:
        col = rule.get('column')
        if col not in df.columns:
            continue

        numeric_col = pd.to_numeric(df[col], errors='coerce')
        mask = pd.Series(True, index=df.index)
        if rule.get('min') is not None:
            mask &= numeric_col.isna() | (numeric_col >= rule['min'])
        if rule.get('max') is not None:
            mask &= numeric_col.isna() | (numeric_col <= rule['max'])

        df = df[mask]

        if rule.get('normalize'):
            mn = rule.get('min')
            mx = rule.get('max')
            if mn is None or mx is None:
                valid = numeric_col.notna() & np.isfinite(numeric_col)
                if valid.any():
                    mn = float(numeric_col[valid].min())
                    mx = float(numeric_col[valid].max())
                else:
                    continue
            valid = numeric_col.notna() & np.isfinite(numeric_col)
            if mx > mn:
                df.loc[valid, col] = (numeric_col[valid] - mn) / (mx - mn)
            else:
                df.loc[valid, col] = 0.0
    return df


def normalize_dataset(df, rules):
    """Нормализовать столбцы по заранее сохранённым [min, max].

    Приводит значения к [0, 1]. Используется на инференсе, где дроп строк не нужен.
    Правила имеют вид {'column': <имя>, 'min': <float>, 'max': <float>}.
    NaN-строки не трогаются.
    """
    df = df.copy()
    for rule in rules:
        col = rule.get('column')
        if col not in df.columns:
            continue
        mn = rule.get('min')
        mx = rule.get('max')
        if mn is None or mx is None:
            continue

        numeric_col = pd.to_numeric(df[col], errors='coerce')
        valid = numeric_col.notna() & np.isfinite(numeric_col)
        if not valid.any():
            continue

        if mx > mn:
            df.loc[valid, col] = (numeric_col[valid] - mn) / (mx - mn)
        else:
            df.loc[valid, col] = 0.0
    return df


def apply_time_filter(df, intervals):
    """Оставить строки, чей datetime попадает хотя бы в один интервал."""
    if not intervals:
        return df
    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])

    masks = []
    for inv in intervals:
        from_dt = pd.to_datetime(inv['from']) if inv.get('from') else None
        to_dt = pd.to_datetime(inv['to']) if inv.get('to') else None
        m = pd.Series(True, index=df.index)
        if from_dt is not None:
            m &= df['datetime'] >= from_dt
        if to_dt is not None:
            m &= df['datetime'] <= to_dt
        masks.append(m)

    if masks:
        combined = masks[0]
        for m in masks[1:]:
            combined |= m
        df = df[combined]
    return df


def apply_time_shift(df, shift_value, shift_unit):
    """Вернуть (df_original, df_shifted).
    """
    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)

    if shift_unit in ('months', 'years'):
        if shift_unit == 'months':
            delta = pd.DateOffset(months=shift_value)
        else:
            delta = pd.DateOffset(years=shift_value)
    else:
        delta = pd.Timedelta(**{shift_unit: shift_value})

    shifted = df.copy()
    start = df['datetime'][0]
    shifted = shifted[shifted['datetime'] >= start + delta]

    original = df.iloc[:-shift_value] if shift_value < len(df) else df.iloc[:0]
    return original, shifted


def apply_labeler(df, x_columns, y_column, window_size=1, window_unit='rows'):
    """Собрать X (признаки с окном).
    """
    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)

    y = df[y_column] if y_column else None

    if window_size == 1 and window_unit == 'rows':
        X = df[x_columns] if x_columns else None
    else:
        X_rows = []
        y_rows = []
        for i in range(window_size - 1, len(df)):
            window = df.iloc[i - window_size + 1 : i + 1]
            x_vals = {}
            for col in x_columns:
                for step in range(window_size):
                    x_vals[f"{col}_t-{window_size - 1 - step}"] = window[col].iloc[step]
            X_rows.append(x_vals)
            if y is not None:
                y_rows.append(y.iloc[i])
        X = pd.DataFrame(X_rows) if X_rows else None
        y = pd.Series(y_rows) if y_rows else None

    return X, y


def resample_to_fixed_grid(df, freq):
    """Линейно интерполировать сигнал на регулярную сетку с шагом `freq`.
    """
    df = df.dropna(subset=['datetime']).sort_values('datetime')
    df = df.drop_duplicates(subset='datetime', keep='last')
    if df.empty:
        return df

    s = df.set_index('datetime')['value']
    grid = pd.date_range(start=s.index.min(), end=s.index.max(), freq=freq)
    if len(grid) == 0:
        return df.reset_index(drop=True)

    union = s.index.union(grid).sort_values()
    s = s.reindex(union)
    s = s.interpolate(method='time', limit_direction='both').ffill().bfill()
    out = s.loc[grid].reset_index()
    out.columns = ['datetime', 'value']
    return out


# =============================================================================
# Методы прохода цепочки преобразований
# =============================================================================

def _raw_to_signal_frames(raw_signals, ordered_names, datetime_format):
    """{имя: ndarray (N, 2)} -> {имя: DataFrame[datetime, value]}.
    Столбец 0 парсится по формату `datetime_format`; строки, которые не
    удалось распарсить, отбрасываются. Столбец 1 приводится к числу.
    """
    out = {}
    for name in ordered_names:
        if name not in raw_signals:
            raise KeyError(
                "Отсутствует сигнал %r. Требуемые сигналы: %r"
                % (name, ordered_names))
        arr = np.asarray(raw_signals[name])
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError(
                "Сигнал %r должен иметь форму (N, 2); получено %r"
                % (name, arr.shape))
        df = pd.DataFrame({'datetime': arr[:, 0], 'value': arr[:, 1]})
        df['datetime'] = pd.to_datetime(
            df['datetime'], format=datetime_format, errors='coerce')
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        df = df.dropna(subset=['datetime'])
        out[name] = df
    return out


def _run_chain_input(chain_input, raw_signals, *, min_history_rows,
                     resample_freq, datetime_format, range_checks=None):
    """Применить цепочку X-преобразований.
   
    """
    if not chain_input:
        raise RuntimeError("Пустая цепочка — нечего выполнять")
    first = chain_input[0]
    if first['type'] != 'dataset':
        raise RuntimeError("chain_input[0] должен быть шагом 'dataset'")

    ordered_names = list(first['inputs'])
    signal_frames = _raw_to_signal_frames(raw_signals, ordered_names, datetime_format)
    signal_frames = {
        name: resample_to_fixed_grid(df, resample_freq)
        for name, df in signal_frames.items()
    }
    for name, df in signal_frames.items():
        if len(df) < min_history_rows:
            raise ValueError(
                "Сигнал %r содержит всего %d строк после пересэмплирования "
                "с шагом %s; минимум %d строк требуется для окна labeler'а."
                % (name, len(df), resample_freq, min_history_rows))

    ref_idx = int(first.get('ref_signal_index', 0) or 0)
    if ref_idx >= len(ordered_names):
        ref_idx = 0
    ref_name = ordered_names[ref_idx]

    df = build_dataset(signal_frames, ref_name, first.get('interpolation', 'linear'))
    ts = pd.to_datetime(df['datetime']).reset_index(drop=True)

    violations = _collect_range_violations(df, ts, range_checks or [])

    window_size = 1

    for step in chain_input[1:]:
        t = step['type']
        if t == 'normalize':
            df = normalize_dataset(df, step['rules'])
        elif t == 'filter':
            df = filter_dataset(df, step['rules'])
            ts = pd.to_datetime(df['datetime']).reset_index(drop=True)
        elif t == 'timefilter':
            df = apply_time_filter(df, step['intervals'])
            ts = pd.to_datetime(df['datetime']).reset_index(drop=True)
        elif t == 'timeshift':
            df, _ = apply_time_shift(df, step['shift_value'], step['shift_unit'])
            ts = ts.iloc[:len(df)].reset_index(drop=True)
        elif t == 'labeler':
            w = int(step.get('window_size', 1))
            wu = step.get('window_unit', 'rows')
            window_size = w
            X, _ = apply_labeler(df, step['x_columns'], step['y_column'], w, wu)
            if X is None:
                raise RuntimeError("labeler не собрал X — проверьте x_columns")
            n_X = X.shape[0]
            ts = ts.iloc[:n_X].reset_index(drop=True)
            df = X
        else:
            raise ValueError("Неизвестный тип шага цепочки: %r" % t)

    X_arr = df.to_numpy(dtype='float32') if isinstance(df, pd.DataFrame) \
        else np.asarray(df, dtype='float32')

    if len(ts) != X_arr.shape[0]:
        raise RuntimeError(
            "Внутренняя ошибка: количество меток времени %d не совпадает "
            "с числом строк X %d." % (len(ts), X_arr.shape[0]))

    return X_arr, ts.to_numpy(), violations, window_size


def _expected_feature_shape(model):
    """Ожидаемая форма входа модели (на образец)."""
    shape = model.input_shape
    if isinstance(shape, list):
        return tuple(shape[0][1:])
    return tuple(shape[1:])


# =============================================================================
# InferenceSession
# =============================================================================

class InferenceSession:
    """Единая сессия инференса, управляемая конфигом проекта."""

    def __init__(self, config, model_path=None, metadata_path=None):
        rt_req = config.get('runtime_version')
        if rt_req and rt_req != __version__:
            raise RuntimeError(
                "Конфиг сгенерирован под nn_inference_runtime %r, "
                "а текущий модуль сообщает версию %r. Используйте "
                "соответствующую версию среды или перегенерируйте конфиг."
                % (rt_req, __version__))

        # --- проверка структуры ---
        for key in ('chain_input', 'input_contract', 'project'):
            if key not in config:
                raise ValueError(
                    "В конфиге отсутствует обязательный ключ %r" % key)

        contract = config['input_contract']
        for key in ('resample_freq', 'datetime_format', 'min_history_rows'):
            if key not in contract:
                raise ValueError(
                    "В input_contract отсутствует обязательный ключ %r" % key)

        self._config = config
        self._model_path = model_path
        self._metadata_path = metadata_path
        self._chain_input = config['chain_input']
        self._chain_output = config.get('chain_output') or {}
        self._prediction_shift = config.get('prediction_shift') or {}
        self._contract = contract
        self._inputs = config.get('inputs', [])
        self._project = config.get('project', {})
        self._model = None
        self.last_log = {}

    @classmethod
    def from_config_file(cls, config_path, model_path=None, metadata_path=None):
        """Загрузить конфиг из файла и создать сессию."""
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cls(cfg, model_path=model_path, metadata_path=metadata_path)


    def metadata(self):
        """Вернуть метаданные модели и входных сигналов в виде словаря."""
        md = {
            'project_code': list(self._project.get('code') or []),
            'project_description': list(self._project.get('description') or []),
            'config_version': self._config.get('config_version'),
            'input_contract': dict(self._contract),
            'prediction_shift': dict(self._prediction_shift),
            'chain_output': dict(self._chain_output),
            'inputs': [
                {
                    'name': s.get('name', ''),
                    'dimension': s.get('dimension', ''),
                    'description': s.get('description', ''),
                    'comment': s.get('comment', ''),
                    'array_shape': '(N, 2): столбец 0 — datetime, '
                                   'столбец 1 — значение',
                    'min_rows': self._contract['min_history_rows'],
                }
                for s in self._inputs
            ],
        }
        if self._metadata_path:
            try:
                with open(self._metadata_path, 'r', encoding='utf-8') as f:
                    mj = json.load(f)
                settings = mj.get('settings')
                if settings:
                    md['training_settings'] = settings
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                pass

        return md

    def describe_chain(self):
        """Структурное описание chain_input."""
        out = []
        for i, step in enumerate(self._chain_input):
            t = step.get('type')
            if t == 'dataset':
                inputs = list(step.get('inputs', []))
                ref_idx = int(step.get('ref_signal_index', 0) or 0)
                ref_name = (inputs[ref_idx] if 0 <= ref_idx < len(inputs)
                            else (inputs[0] if inputs else None))
                label = ("Выравнивание сигналов по временной шкале опорного "
                         "с интерполяцией пропусков.")
                params = {
                    'reference_signal': ref_name,
                    'interpolation': step.get('interpolation', 'linear'),
                    'inputs': inputs,
                }
            elif t == 'normalize':
                rules = step.get('rules', [])
                label = ("Нормализация столбцов по сохранённым [min, max]; "
                         "без отбрасывания строк.")
                params = {'n_rules': len(rules), 'rules': rules}
            elif t == 'filter':
                label = ("Фильтрация строк")
                params = {'n_rules': len(step.get('rules', [])),
                          'rules': step.get('rules', [])}
            elif t == 'timefilter':
                label = "Фильтр по временным интервалам."
                params = {'intervals': step.get('intervals', [])}
            elif t == 'timeshift':
                label = "Временное смещение."
                params = {'shift_value': step.get('shift_value'),
                          'shift_unit': step.get('shift_unit')}
            elif t == 'labeler':
                label = ("Формирование X с окном по x-столбцам; каждая "
                         "строка содержит последние window_size значений.")
                params = {
                    'x_columns': step.get('x_columns', []),
                    'window_size': step.get('window_size', 1),
                    'window_unit': step.get('window_unit', 'rows'),
                }
            else:
                label = "Неизвестный тип шага"
                params = {}
            out.append({'step': i, 'type': t, 'label': label, 'params': params})
        return out

    def describe_chain_output(self):
        """Структурное описание выходных преобразований по KKS."""
        out = {}
        for code, steps in self._chain_output.items():
            entries = []
            for s in steps:
                if s.get('type') == 'denormalize':
                    entries.append({
                        'type': 'denormalize',
                        'label': ("Разворот нормализации выхода в реальные "
                                  "единицы: y = y_model * (max - min) + min."),
                        'params': {'min': s.get('min'), 'max': s.get('max')},
                    })
                else:
                    entries.append({
                        'type': s.get('type'),
                        'label': "Неизвестный тип шага",
                        'params': {k: v for k, v in s.items() if k != 'type'},
                    })
            out[code] = entries
        return out

    
    def _build_log_summary(self):
        """Сводка по last_log: сколько точек и нарушений."""
        total = ok = warning = violations = 0
        for entries in self.last_log.values():
            for e in entries:
                total += 1
                if e.get("status") == "OK":
                    ok += 1
                else:
                    warning += 1
                violations += len(e.get("violations") or [])
        return {
            "total_points": total,
            "ok": ok,
            "warning": warning,
            "total_violations": violations,
        }

    def save_log(self, path):
        """Сохранить last_log в JSON-файл.

        Формат файла:
            {
                "project_code": [...],
                "project_description": [...],
                "logged_at": "ISO-8601",
                "model_path": "model.keras",
                "runtime_version": "1.0",
                "config_version": "1.0",
                "input_contract": {...},
                "summary": {
                "total_points": N,
                "ok": N,
                "warning": N,
                "total_violations": N
                },
                "outputs": {
                "<KKS>": [
                    {"output_index", "output_timestamp", "status", "violations": [...]},
                    ...
                ]
                }
            }
        """
        summary = self._build_log_summary()
        payload = {
            "project_code": list(self._project.get("code") or []),
            "project_description": list(self._project.get("description") or []),
            "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model_path": self._model_path,
            "runtime_version": __version__,
            "config_version": self._config.get("config_version"),
            "input_contract": dict(self._contract),
            "summary": summary,
            "outputs": self.last_log,
        }

        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return path
    

    def _load_model(self):
        if self._model is None:
            if not self._model_path:
                raise RuntimeError(
                    "Отсуствует путь к модели ")
            self._model = tf.keras.models.load_model(self._model_path)
        return self._model

    def expected_input_shape(self):
        """Ожидаемая форма входа модели."""
        return _expected_feature_shape(self._load_model())

    def validate_signals(self, signals):
        """Проверить, что набор `signals` пригоден для цепочки.
        """
        required = list(self._chain_input[0]['inputs'])
        provided = set(signals.keys())
        missing = [n for n in required if n not in provided]
        extra = sorted(provided - set(required))
        if missing or extra:
            raise ValueError(
                "Несовпадение набора сигналов: отсутствуют=%r, лишние=%r, требуются=%r" % (missing, extra, required))
        for name in required:
            arr = np.asarray(signals[name])
            if arr.ndim != 2 or arr.shape[1] != 2:
                raise ValueError("Сигнал %r должен иметь форму (N, 2); получено %r"%(name, arr.shape))
            if arr.shape[0] < self._contract['min_history_rows']:
                raise ValueError("Сигнал %r содержит %d строк; требуется минимум %d." % (name, arr.shape[0], self._contract['min_history_rows']))
        return True

    def predict(self, signals, *, log_path=None):
        """Выполнить полный конвейер инференса.
        """
        if self._metadata_path:
            try:
                with open(self._metadata_path, 'r', encoding='utf-8') as f:
                    _ = json.load(f)
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                pass

        range_checks = self._config.get('range_checks') or []

        X, ts_window_start, violations, window_size = _run_chain_input(
            self._chain_input,
            signals,
            min_history_rows=self._contract['min_history_rows'],
            resample_freq=self._contract['resample_freq'],
            datetime_format=self._contract['datetime_format'],
            range_checks=range_checks,
        )

        model = self._load_model()
        expected = _expected_feature_shape(model)
        actual = tuple(X.shape[1:])
        if actual != expected:
            raise ValueError(
                "Подготовленный вход имеет форму на образец %r, а модель "
                "ожидает %r. Цепочка в конфиге не совпадает с тем, на чём "
                "обучалась модель."
                % (actual, expected))

        preds = model.predict(X, verbose=0)
        if preds.ndim == 1:
            preds = preds.reshape(-1, 1)

        if preds.shape[0] != len(ts_window_start):
            raise RuntimeError(
                "Модель вернула %d строк предсказаний, а на входе было %d "
                "строк." % (preds.shape[0], len(ts_window_start)))

        output_codes = list(self._project.get('code') or [])
        if not output_codes:
            raise RuntimeError(
                "В конфиге project.code пуст.")
        if len(output_codes) != preds.shape[1]:
            raise ValueError("project.code содержит %d KKS-код(ов), а модель выдаёт %d значений." % (len(output_codes), preds.shape[1]))

        fmt = self._contract['datetime_format']
        result = {}
        self.last_log = {}

        for k, code in enumerate(output_codes):
            shift = self._prediction_shift.get(code)
            ts_out = pd.to_datetime(ts_window_start)
            if shift and shift.get('value'):
                val = int(shift['value'])
                unit = shift['unit']
                if unit in ('months', 'years'):
                    delta = pd.DateOffset(**{unit: val})
                else:
                    delta = pd.Timedelta(**{unit: val})
                ts_out = ts_out + delta

            values = preds[:, k].astype(float)
            for step in self._chain_output.get(code, []):
                if step['type'] == 'denormalize':
                    mn = float(step['min'])
                    mx = float(step['max'])
                    values = values * (mx - mn) + mn
                else:
                    raise ValueError("Неизвестный тип шага chain_output: %r" % step.get('type'))

            ts_str = ts_out.strftime(fmt).to_numpy()
            arr = np.empty((len(ts_str), 2), dtype=object)
            arr[:, 0] = ts_str
            arr[:, 1] = values
            result[code] = arr

            log_entries = self._build_log_for_output(
                k, ts_str, violations, window_size)
            self.last_log[code] = log_entries



        if log_path:
            self.save_log(log_path)

        return result

    def _build_log_for_output(self, output_index, ts_str,
                              violations, window_size):
        """Собрать диагностику по одному выходному ряду.

        Для X-строки k окно покрывает df[k .. k+window_size-1].
        Собираем нарушения из этого диапазона.
        """
        n = len(ts_str)
        entries = []
        for k in range(n):
            lo = k
            hi = min(k + window_size, len(violations))
            window_violations = []
            for i in range(lo, hi):
                window_violations.extend(violations[i])
            status = "WARNING" if window_violations else "OK"
            entries.append({
                "output_index": k,
                "output_timestamp": ts_str[k],
                "status": status,
                "violations": window_violations,
            })
        return entries