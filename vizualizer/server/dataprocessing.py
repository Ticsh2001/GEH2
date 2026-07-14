import os
import json
import hashlib
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


def get_dataset_path(config: str, project_code: str, element_id: str) -> str:
    return os.path.join(get_datasets_dir(config), f"{project_code}_{element_id}.xlsx")

def get_meta_path(config: str, project_code: str, element_id: str) -> str:
    return os.path.join(get_datasets_dir(config), f"{project_code}_{element_id}_meta.json")

# Папка для хранения обработанных датасетов
def get_datasets_dir(config: str) -> str:
    base = os.environ.get('DATASETS_DIR', os.path.join(os.path.dirname(__file__), 'datasets'))
    path = os.path.join(base, config)
    os.makedirs(path, exist_ok=True)
    return path

def compute_hash(obj) -> str:
    """SHA256‑хэш от JSON‑представления объекта."""
    json_str = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

def build_dataset(signals_data: Dict[str, pd.DataFrame], ref_signal: str,
                  interpolation: str = 'linear') -> pd.DataFrame:
    """
    signals_data: словарь {имя_сигнала: DataFrame с колонками ['datetime', 'value']}
    ref_signal: ключ из signals_data, по времени которого синхронизируем.
    Возвращает объединённый DataFrame со столбцами 'datetime' и столбцами сигналов.
    """
    if not signals_data:
        raise ValueError("Нет данных для построения датасета")

    # Опорный DataFrame
    df_ref = signals_data[ref_signal][['datetime']].drop_duplicates().sort_values('datetime')
    df_ref = df_ref.set_index('datetime')

    result = df_ref.copy()
    for name, df in signals_data.items():
        df = df.drop_duplicates(subset='datetime').set_index('datetime')
        aligned = df.reindex(result.index)
        # Преобразуем в числовой тип, иначе interpolate упадёт
        aligned['value'] = pd.to_numeric(aligned['value'], errors='coerce')
        # Интерполяция
        if interpolation == 'linear':
            aligned = aligned.interpolate(method='time', limit_direction='both')
        elif interpolation == 'nearest':
            aligned = aligned.interpolate(method='nearest', limit_direction='both')
        elif interpolation == 'cubic':
            aligned = aligned.interpolate(method='cubic', limit_direction='both')
        aligned = aligned.fillna(method='ffill').fillna(method='bfill')
        # Ещё раз на всякий случай
        result[name] = pd.to_numeric(aligned['value'], errors='coerce')

    # На всякий случай пробежимся по всем колонкам ещё раз
    for col in result.columns:
        if col != 'datetime':
            result[col] = pd.to_numeric(result[col], errors='coerce')

    result = result.reset_index()
    return result

def apply_time_filter(df: pd.DataFrame, intervals: List[dict]) -> pd.DataFrame:
    """Оставляет строки, datetime которых попадает хотя бы в один интервал."""
    if not intervals:
        return df
    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    masks = []
    for inv in intervals:
        from_dt = pd.to_datetime(inv['from']) if inv['from'] else None
        to_dt = pd.to_datetime(inv['to']) if inv['to'] else None
        m = pd.Series(True, index=df.index)
        if from_dt is not None:
            m &= df['datetime'] >= from_dt
        if to_dt is not None:
            m &= df['datetime'] <= to_dt
        masks.append(m)
    if masks:
        combined_mask = masks[0]
        for m in masks[1:]:
            combined_mask |= m
        df = df[combined_mask]
    return df


def filter_dataset(df: pd.DataFrame, rules: List[dict]) -> pd.DataFrame:
    df = df.copy()
    for rule in rules:
        col = rule['column']
        if col not in df.columns:
            continue
        # Преобразуем столбец в числа, NaN останутся
        numeric_col = pd.to_numeric(df[col], errors='coerce')
        mask = pd.Series(True, index=df.index)

        if 'min' in rule and rule['min'] is not None:
            # Оставляем строки, где значение NaN ИЛИ >= min
            mask &= (numeric_col.isna()) | (numeric_col >= rule['min'])
        if 'max' in rule and rule['max'] is not None:
            mask &= (numeric_col.isna()) | (numeric_col <= rule['max'])

        df = df[mask]
        # Нормализация – только для не‑NaN значений
        if rule.get('normalize'):
            valid = numeric_col.notna() & np.isfinite(numeric_col)
            if valid.any():
                min_val = numeric_col[valid].min()
                max_val = numeric_col[valid].max()
                if max_val > min_val:
                    df.loc[valid, col] = (numeric_col[valid] - min_val) / (max_val - min_val)
                else:
                    df.loc[valid, col] = 0.0
    return df


def get_output_file(elem: dict, port: str, config: str, project_code: str) -> str:
    meta_path = get_meta_path(config, project_code, elem['id'])
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"Метаданные для элемента {elem['id']} не найдены. Примените элемент.")
    with open(meta_path, 'r') as f:
        meta = json.load(f)
    outputs = meta.get('outputs', {})
    if port not in outputs or not outputs[port]:
        raise ValueError(f"Порт {port} не содержит данных")
    return os.path.join(get_datasets_dir(config), outputs[port])

def load_input_data(element_id: str, project: dict, config: str, project_code: str,
                    loaded_cache: dict = None) -> Tuple[pd.DataFrame, Dict[str, str]]:
    elements = project.get('elements', {})
    connections = project.get('connections', [])
    elem = elements[element_id]
    inputs = []

    for conn in connections:
        if conn['toElement'] == element_id:
            inputs.append({
                'toPort': conn['toPort'],
                'fromPort': conn['fromPort'],
                'fromElement': conn['fromElement']
            })

    inputs.sort(key=lambda x: int(x['toPort'].split('-')[1]))

    signals_data = {}
    hashes = {}
    ref_index = elem.get('props', {}).get('reference_signal_index', 0)
    interpolation = elem.get('props', {}).get('interpolation', 'linear')

    for inp in inputs:
        src_id = inp['fromElement']
        fromPort = inp['fromPort']
        src_elem = elements[src_id]

        if src_elem['type'] == 'input-signal':
            signal_name = src_elem['props'].get('name', '').strip()
            if not signal_name:
                raise ValueError(f"Входной сигнал {src_id} не имеет имени")

            from main import load_signal_data_optimized
            data_dict = load_signal_data_optimized([signal_name], config)
            if signal_name not in data_dict:
                raise ValueError(f"Данные для сигнала '{signal_name}' не найдены")

            df = data_dict[signal_name].copy()
            df['value'] = df['value'].astype(str).str.replace(',', '.', regex=False)
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            signals_data[signal_name] = df
            hashes[src_id] = compute_hash({'name': signal_name, 'rows': len(df)})
        else:
            # Все остальные элементы – универсальный доступ через метаданные
            path = get_output_file(src_elem, fromPort, config, project_code)
            if not os.path.exists(path):
                raise ValueError(f"Файл для элемента {src_id} (порт {fromPort}) не найден")
            df = pd.read_excel(path)
            for col in df.columns:
                if col != 'datetime':
                    signals_data[col] = df[['datetime', col]].rename(columns={col: 'value'})
            hashes[src_id] = compute_hash({'file': path, 'mtime': os.path.getmtime(path)})

    if not signals_data:
        raise ValueError("Нет входных данных для построения датасета")

    ref_names = list(signals_data.keys())
    if ref_index >= len(ref_names):
        ref_index = 0
    ref_name = ref_names[ref_index]

    df_combined = build_dataset(signals_data, ref_name, interpolation)
    return df_combined, hashes

def apply_time_shift(df: pd.DataFrame, shift_value: int, shift_unit: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Возвращает (df_original, df_shifted).
    df_original — строки, для которых есть сдвинутая пара (т.е. без последних shift_value единиц).
    df_shifted — те же строки, но со сдвигом времени.
    """
    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)

    # Преобразуем единицу в Timedelta
    unit_map = {
        'seconds': 'seconds', 'minutes': 'minutes', 'hours': 'hours',
        'days': 'days', 'weeks': 'W', 'months': 'M', 'years': 'Y'
    }
    if shift_unit in ['months', 'years']:
        # Для месяцев/лет Timedelta не работает, используем DateOffset
        if shift_unit == 'months':
            delta = pd.DateOffset(months=shift_value)
        else:
            delta = pd.DateOffset(years=shift_value)
    else:
        delta = pd.Timedelta(**{shift_unit: shift_value})

    # Исходные данные (обрезаем последние shift_value строк, потому что для них нет сдвига)
    original = df.iloc[:-shift_value] if shift_value < len(df) else df.iloc[:0]
    # Сдвинутые данные: берём те же строки, но добавляем delta к datetime
    shifted = original.copy()
    shifted['datetime'] = shifted['datetime'] + delta

    return original, shifted

def apply_labeler(df: pd.DataFrame, x_columns: list, y_column: str,
                  window_size: int = 1, window_unit: str = 'rows') -> (pd.DataFrame, pd.Series):
    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)

    # Y
    y = df[y_column] if y_column else None

    if window_size == 1 and window_unit == 'rows':
        X = df[x_columns] if x_columns else None
    else:
        # Формируем окна
        X_rows = []
        y_rows = []
        for i in range(window_size-1, len(df)):
            window = df.iloc[i-window_size+1 : i+1]
            x_vals = {}
            for col in x_columns:
                for step in range(window_size):
                    x_vals[f"{col}_t-{window_size-1-step}"] = window[col].iloc[step]
            X_rows.append(x_vals)
            if y is not None:
                y_rows.append(y.iloc[i])
        X = pd.DataFrame(X_rows) if X_rows else None
        y = pd.Series(y_rows) if y_rows else None

    return X, y



def process_element(element_id: str, project: dict, config: str, project_code: str) -> dict:
    elements = project.get('elements', {})
    elem = elements[element_id]
    elem_type = elem.get('nnType') or elem.get('type')

    meta_path = get_meta_path(config, project_code, element_id)
    data_path = get_dataset_path(config, project_code, element_id)

    # 1. Рекурсивная обработка входов
    input_hashes = {}
    connections = project.get('connections', [])
    for conn in connections:
        if conn['toElement'] == element_id:
            src_id = conn['fromElement']
            src_elem = elements[src_id]
            if src_elem['type'] != 'input-signal':
                process_element(src_id, project, config, project_code)
                src_meta_path = get_meta_path(config, project_code, src_id)
                if os.path.exists(src_meta_path):
                    with open(src_meta_path, 'r') as f:
                        src_meta = json.load(f)
                    input_hashes[src_id] = src_meta.get('hash', '')
                else:
                    input_hashes[src_id] = ''
            else:
                input_hashes[src_id] = 'signal_fixed'

    # 2. Проверка необходимости пересчёта
    current_config = {
        'props': elem.get('props', {}),
        'input_hashes': input_hashes,
        'type': elem_type
    }
    current_hash = compute_hash(current_config)

    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            meta = json.load(f)
        # Проверяем актуальность: хэш совпадает и существует хотя бы один выходной файл
        outputs_exist = all(
            os.path.exists(os.path.join(get_datasets_dir(config), rel))
            for rel in meta.get('outputs', {}).values() if rel
        )
        if meta.get('hash') == current_hash and outputs_exist:
            return {
                "hash": current_hash,
                "file": os.path.relpath(data_path, get_datasets_dir(config)),
                "input_hashes": input_hashes
            }

    # 3. Выполнение обработки — теперь для всех типов используется load_input_data
    if elem_type == 'dataset':
        df, _ = load_input_data(element_id, project, config, project_code)
        df.to_excel(data_path, index=False)
        outputs = {'out-0': os.path.relpath(data_path, get_datasets_dir(config))}

    elif elem_type in ('filter', 'timefilter', 'timeshift', 'labeler'):
        # Получаем входной DataFrame через универсальную функцию
        df, _ = load_input_data(element_id, project, config, project_code)

        if elem_type == 'filter':
            rules = elem.get('props', {}).get('rules', [])
            df = filter_dataset(df, rules)
            df.to_excel(data_path, index=False)
            outputs = {'out-0': os.path.relpath(data_path, get_datasets_dir(config))}

        elif elem_type == 'timefilter':
            intervals = elem.get('props', {}).get('intervals', [])
            df = apply_time_filter(df, intervals)
            df.to_excel(data_path, index=False)
            outputs = {'out-0': os.path.relpath(data_path, get_datasets_dir(config))}

        elif elem_type == 'timeshift':
            shift_value = elem.get('props', {}).get('shift_value', 1)
            shift_unit = elem.get('props', {}).get('shift_unit', 'days')
            df_orig, df_shifted = apply_time_shift(df, shift_value, shift_unit)
            out0_path = get_dataset_path(config, project_code, f"{element_id}_out0")
            out1_path = get_dataset_path(config, project_code, f"{element_id}_out1")
            df_orig.to_excel(out0_path, index=False)
            df_shifted.to_excel(out1_path, index=False)
            outputs = {
                'out-0': os.path.relpath(out0_path, get_datasets_dir(config)),
                'out-1': os.path.relpath(out1_path, get_datasets_dir(config))
            }
            data_path = out0_path  # для совместимости

        elif elem_type == 'labeler':
            props = elem.get('props', {})
            x_cols = props.get('x_columns', [])
            y_col = props.get('y_column')
            w_size = props.get('window_size', 1)
            w_unit = props.get('window_unit', 'rows')
            X, y = apply_labeler(df, x_cols, y_col, w_size, w_unit)
            outX_path = get_dataset_path(config, project_code, f"{element_id}_X")
            outy_path = get_dataset_path(config, project_code, f"{element_id}_y")
            if X is not None:
                X.to_excel(outX_path, index=False)
            if y is not None:
                y.to_frame().to_excel(outy_path, index=False)
            outputs = {
                'out-0': os.path.relpath(outX_path, get_datasets_dir(config)) if X is not None else None,
                'out-1': os.path.relpath(outy_path, get_datasets_dir(config)) if y is not None else None
            }
            data_path = outX_path if X is not None else outy_path
    else:
        raise ValueError(f"Неизвестный тип элемента: {elem_type}")

    # 4. Сохраняем метаданные с outputs
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump({
            'hash': current_hash,
            'timestamp': pd.Timestamp.now().isoformat(),
            'outputs': outputs
        }, f)

    return {
        "hash": current_hash,
        "file": os.path.relpath(data_path, get_datasets_dir(config)),
        "input_hashes": input_hashes
    }