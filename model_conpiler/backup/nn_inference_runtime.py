"""
nn_inference_runtime — единая среда инференса для проектов.

"""
import argparse
import json
import sys

import numpy as np
import pandas as pd
import tensorflow as tf


# =============================================================================
# Чистые трансформации
# (портированы из dataprocessing.py; кэширование, файловый ввод-вывод и
#  логика путей конфигов удалены; семантика сохранена без изменений, чтобы
#  инференс совпадал с обучением.)
# =============================================================================

def build_dataset(signals_data, ref_signal, interpolation='linear'):
    """Выровнять все сигналы по временной шкале опорного и интерполировать.

    Параметры
    ---------
    signals_data : {имя: DataFrame[datetime, value]}
    ref_signal   : ключ в signals_data, чья временная шкала используется
                   как опорная
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


def filter_dataset(df, rules):
    """Убрать строки, где значение столбца выходит за пределы [min, max];
    опционально выполнить min-max нормализацию.
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
            valid = numeric_col.notna() & np.isfinite(numeric_col)
            if valid.any():
                mn = numeric_col[valid].min()
                mx = numeric_col[valid].max()
                if mx > mn:
                    df.loc[valid, col] = (numeric_col[valid] - mn) / (mx - mn)
                else:
                    df.loc[valid, col] = 0.0
    return df


def apply_time_filter(df, intervals):
    """Оставить только строки, чей datetime попадает хотя бы в один
    интервал {from, to}."""
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
    """
    Создать временное смещение
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
    """Собрать X (признаки с окном) 
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
# Прогон цепочки
# =============================================================================

def _raw_to_signal_frames(raw_signals, ordered_names, datetime_format):
    """Преобразовать {имя: ndarray (N, 2)} в {имя: DataFrame[datetime, value]}.
    Столбец 0 по формату `datetime_format`; Столбец 1 приводится к числу.
    """
    out = {}
    for name in ordered_names:
        if name not in raw_signals:
            raise KeyError(
                "Отсутствует сигнал %r. Требуемые сигналы: %r"
                % (name, ordered_names)
            )
        arr = np.asarray(raw_signals[name])
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError(
                "Сигнал %r должен иметь форму (N, 2); получено %r"
                % (name, arr.shape)
            )
        df = pd.DataFrame({'datetime': arr[:, 0], 'value': arr[:, 1]})
        df['datetime'] = pd.to_datetime(
            df['datetime'], format=datetime_format, errors='coerce'
        )
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        df = df.dropna(subset=['datetime'])
        out[name] = df
    return out


def _run_chain(chain, raw_signals, *, min_history_rows,
               resample_freq, datetime_format):
    """Применить цепочку; вернуть (X, timestamps).

    X           : np.ndarray формы (M, K) — признаки для модели.
    timestamps  : np.ndarray datetime64[ns] формы (M,) — метка времени,
                  соответствующая каждой строке X (а значит и каждой строке
                  предсказания).
    """
    if not chain:
        raise RuntimeError("Пустая цепочка — нечего выполнять")
    first = chain[0]
    if first['type'] != 'dataset':
        raise RuntimeError("CHAIN[0] должен быть шагом 'dataset'")

    ordered_names = list(first['inputs'])
    signal_frames = _raw_to_signal_frames(raw_signals, ordered_names, datetime_format)

    # Пересэмплировать каждый сигнал на регулярную сетку до начала цепочки.
    signal_frames = {
        name: resample_to_fixed_grid(df, resample_freq)
        for name, df in signal_frames.items()
    }

    for name, df in signal_frames.items():
        if len(df) < min_history_rows:
            raise ValueError(
                "Сигнал %r содержит всего %d строк после пересэмплирования "
                "с шагом %s; минимум %d строк требуется для окна labeler'а."
                % (name, len(df), resample_freq, min_history_rows)
            )

    ref_idx = int(first.get('ref_signal_index', 0) or 0)
    if ref_idx >= len(ordered_names):
        ref_idx = 0
    ref_name = ordered_names[ref_idx]

    df = build_dataset(signal_frames, ref_name, first.get('interpolation', 'linear'))

    # Текущие метки времени строк DataFrame. Обновляются на каждом шаге;
    # фактически меняет состав строк только labeler.
    ts = pd.to_datetime(df['datetime']).reset_index(drop=True)

    for step in chain[1:]:
        t = step['type']
        if t == 'filter':
            df = filter_dataset(df, step['rules'])
            ts = pd.to_datetime(df['datetime']).reset_index(drop=True)
        elif t == 'timefilter':
            df = apply_time_filter(df, step['intervals'])
            ts = pd.to_datetime(df['datetime']).reset_index(drop=True)
        elif t == 'timeshift':
            # Только out-0: последние shift_value строк отбрасываются,
            # datetime не меняется.
            df, _ = apply_time_shift(df, step['shift_value'], step['shift_unit'])
            ts = ts.iloc[: len(df)].reset_index(drop=True)
        elif t == 'labeler':
            w = int(step.get('window_size', 1))
            wu = step.get('window_unit', 'rows')
            X, _ = apply_labeler(
                df, step['x_columns'], step['y_column'], w, wu,
            )
            if X is None:
                raise RuntimeError(
                    "labeler не собрал X — проверьте x_columns"
                )
            # Согласовать метки времени с X: X-строка k соответствует
            # ПОСЛЕДНЕЙ строке окна, то есть строке df с индексом (k + w - 1).
            if w == 1 and wu == 'rows':
                ts = ts.reset_index(drop=True)
            else:
                ts = ts.iloc[w - 1:].reset_index(drop=True)
            df = X
        else:
            raise ValueError("Неизвестный тип шага цепочки: %r" % (t,))

    X = df.to_numpy(dtype='float32') if isinstance(df, pd.DataFrame) \
        else np.asarray(df, dtype='float32')

    if len(ts) != X.shape[0]:
        raise RuntimeError(
            "Внутренняя ошибка: количество меток времени %d не совпадает "
            "с числом строк X %d. Вероятно, какой-то шаг цепочки меняет "
            % (len(ts), X.shape[0])
        )
    return X, ts.to_numpy()


def _expected_feature_shape(model):
    """Ожидаемая форма входа модели (на один образец), как tuple."""
    shape = model.input_shape
    if isinstance(shape, list):
        # Многovходовая модель: берём первый вход как опорный.
        return tuple(shape[0][1:])
    return tuple(shape[1:])


# =============================================================================
# InferenceSession
# =============================================================================

class InferenceSession:
    """Единая сессия инференса, управляемая конфигом проекта.
    """

    def __init__(self, config, model_path=None, metadata_path=None):
        # --- проверка версии ---------------------------------------------
        rt_req = config.get('runtime_version')
        # --- проверка базовой структуры ----------------------------------
        for key in ('chain', 'input_contract'):
            if key not in config:
                raise ValueError(
                    "В конфиге отсутствует обязательный ключ %r" % key
                )

        contract = config['input_contract']
        for key in ('resample_freq', 'datetime_format', 'min_history_rows'):
            if key not in contract:
                raise ValueError(
                    "В input_contract отсутствует обязательный ключ %r"
                    % key
                )

        self._config = config
        self._model_path = model_path
        self._metadata_path = metadata_path
        self._chain = config['chain']
        self._contract = contract
        self._inputs = config.get('inputs', [])
        self._project = config.get('project', {})
        self._model = None

    # --- создание --------------------------------------------------------

    @classmethod
    def from_config_file(cls, config_path, model_path=None, metadata_path=None):
        """Загрузить конфиг из файла и создать сессию."""
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cls(cfg, model_path=model_path, metadata_path=metadata_path)

    # --- 1. Метаданные ---------------------------------------------------

    def metadata(self):
        """Вернуть метаданные модели и входных сигналов в виде словаря."""
        md = {
            'project_code': self._project.get('code', ''),
            'project_description': self._project.get('description', ''),
            'config_version': self._config.get('config_version'),
            'runtime_version': self._config.get('runtime_version'),
            'input_contract': dict(self._contract),
            'inputs': [
                {
                    'name': s.get('name', ''),
                    'dimension': s.get('dimension', ''),
                    'description': s.get('description', ''),
                    'comment': s.get('comment', ''),
                    'array_shape': '(N, 2): столбец 0 — datetime, столбец 1 — значение',
                    'min_rows': self._contract['min_history_rows'],
                }
                for s in self._inputs
            ],
            'n_features_expected': None,
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

    # --- 2. Описание цепочки ---------------------------------------------

    def describe_chain(self):
        """Вернуть цепочку предобработки в виде списка структурных словарей."""
        out = []
        for i, step in enumerate(self._chain):
            t = step.get('type')
            if t == 'dataset':
                inputs = list(step.get('inputs', []))
                ref_idx = int(step.get('ref_signal_index', 0) or 0)
                ref_name = (
                    inputs[ref_idx] if 0 <= ref_idx < len(inputs)
                    else (inputs[0] if inputs else None)
                )
                label = (
                    "Выравнивание всех входных сигналов по временной шкале "
                    "опорного сигнала с интерполяцией пропусков."
                )
                params = {
                    'reference_signal': ref_name,
                    'interpolation': step.get('interpolation', 'linear'),
                    'inputs': inputs,
                }
            elif t == 'filter':
                rules = step.get('rules', [])
                label = (
                    "Отбрасывание строк, где значение столбца выходит за "
                    "[min, max]; опционально min-max нормализация."
                )
                params = {'n_rules': len(rules), 'rules': rules}
            elif t == 'timefilter':
                intervals = step.get('intervals', [])
                label = (
                    "Оставить только строки, чей datetime попадает хотя бы "
                    "в один заданный интервал."
                )
                params = {'n_intervals': len(intervals), 'intervals': intervals}
            elif t == 'timeshift':
                label = (
                    "Отбрасывание последних N строк (out-0 элемента "
                    "timeshift; ветка со сдвигом в будущее исключена)."
                )
                params = {
                    'shift_value': step.get('shift_value'),
                    'shift_unit': step.get('shift_unit'),
                }
            elif t == 'labeler':
                label = (
                    "Формирование X с окном по перечисленным x-столбцам; "
                    "каждая строка содержит последние window_size значений "
                    "каждого признака."
                )
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

    def chain_summary(self):
        """Вернуть цепочку как читаемую многострочную строку."""
        lines = [
            "Цепочка предобработки для проекта %s:"
            % self._project.get('code', '<неизвестно>')
        ]
        for entry in self.describe_chain():
            lines.append("  [%d] %s: %s" % (
                entry['step'], entry['type'], entry['label']
            ))
            for k, v in entry['params'].items():
                lines.append("      %s = %r" % (k, v))
        return "\n".join(lines)

    # --- 3. Инференс -----------------------------------------------------

    def _load_model(self):
        if self._model is None:
            if not self._model_path:
                raise RuntimeError(
                    "В эту сессию инференса не передан путь к модели "
                    "(model_path)."
                )
            self._model = tf.keras.models.load_model(self._model_path)
        return self._model

    def expected_input_shape(self):
        """Ожидаемая форма входа модели (на один образец)."""
        return _expected_feature_shape(self._load_model())

    def validate_signals(self, signals):
        """Проверить, что набор `signals` пригоден для цепочки.

        Бросает информативное исключение, если чего-то не хватает или
        что-то лишнее.
        """
        required = list(self._chain[0]['inputs'])
        provided = set(signals.keys())
        missing = [n for n in required if n not in provided]
        extra = sorted(provided - set(required))
        if missing or extra:
            raise ValueError(
                "Несовпадение набора сигналов: отсутствуют=%r, лишние=%r, "
                "требуются=%r" % (missing, extra, required)
            )
        for name in required:
            arr = np.asarray(signals[name])
            if arr.ndim != 2 or arr.shape[1] != 2:
                raise ValueError(
                    "Сигнал %r должен иметь форму (N, 2); получено %r"
                    % (name, arr.shape)
                )
            if arr.shape[0] < self._contract['min_history_rows']:
                raise ValueError(
                    "Сигнал %r содержит %d строк; требуется минимум %d."
                    % (name, arr.shape[0], self._contract['min_history_rows'])
                )
        return True

    def predict(self, signals):
        """Выполнить полный конвейер; вернуть ndarray (M, 2).

        Столбец 0      : метка времени предсказания (строка в формате
                         DATETIME_FORMAT).
        Столбец 1     : выход модели

        """
        if self._metadata_path:
            try:
                with open(self._metadata_path, 'r', encoding='utf-8') as f:
                    _ = json.load(f)
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                pass

        X, timestamps = _run_chain(
            self._chain,
            signals,
            min_history_rows=self._contract['min_history_rows'],
            resample_freq=self._contract['resample_freq'],
            datetime_format=self._contract['datetime_format'],
        )

        shift = self._config.get('prediction_shift')
        if shift and shift.get('value'):
            val = int(shift['value'])
            unit = shift['unit']
            if unit in ('months', 'years'):
                delta = pd.DateOffset(**{unit: val})
            else:
                delta = pd.Timedelta(**{unit: val})
            timestamps = pd.to_datetime(timestamps) + delta

        model = self._load_model()
        expected = _expected_feature_shape(model)
        actual = tuple(X.shape[1:])
        if actual != expected:
            raise ValueError(
                "Подготовленный вход имеет форму на образец %r, а модель "
                "ожидает %r. Цепочка в конфиге не совпадает с тем, на чём "
                "обучалась модель"
                % (actual, expected)
            )

        preds = model.predict(X, verbose=0)
        if preds.ndim == 1:
            preds = preds.reshape(-1, 1)

        if preds.shape[0] != len(timestamps):
            raise RuntimeError(
                "Модель вернула %d строк предсказаний, а на входе было %d "
                "строк." % (preds.shape[0], len(timestamps))
            )

        ts_str = pd.to_datetime(timestamps).strftime(
            self._contract['datetime_format']
        ).to_numpy()

        out = np.empty((len(ts_str), 1 + preds.shape[1]), dtype=object)
        out[:, 0] = ts_str
        out[:, 1:] = preds
        return out