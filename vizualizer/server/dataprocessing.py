#dataprocessing.py
import os
import json
import hashlib
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple

# =============================================================================
# Константы обработки временных рядов
# =============================================================================
MAX_GAP = pd.Timedelta(hours=4)
DELTA_TIME = pd.Timedelta(minutes=30)


def _parse_shift_delta(shift_value: int, shift_unit: str):
    """shift_value + shift_unit -> Timedelta или DateOffset."""
    if shift_unit == 'rows':
        return DELTA_TIME * int(shift_value)
    if shift_unit in ('months', 'years'):
        return pd.DateOffset(**{shift_unit: int(shift_value)})
    return pd.Timedelta(**{shift_unit: int(shift_value)})


def _parse_window_delta(window_size: int, window_unit: str) -> pd.Timedelta:
    """window_size + window_unit -> длина окна (Timedelta)."""
    if window_unit == 'rows':
        return DELTA_TIME * max(0, int(window_size) - 1)
    if window_unit in ('months', 'years'):
        raise ValueError(
            f"window_unit '{window_unit}' не поддерживается — "
            "используйте секунды / минуты / часы / дни"
        )
    return pd.Timedelta(**{window_unit: int(window_size)})


def _window_num_points(window_size: int, window_unit: str) -> int:
    """Сколько точек в окне — по шагу DELTA_TIME."""
    if window_unit == 'rows':
        return max(1, int(window_size))
    delta = _parse_window_delta(window_size, window_unit)
    return max(1, int(round(delta / DELTA_TIME)) + 1)


def get_dataset_path(config: str, project_code: str, element_id: str) -> str:
    return os.path.join(get_datasets_dir(config), f"{project_code}_{element_id}.xlsx")

def get_meta_path(config: str, project_code: str, element_id: str) -> str:
    return os.path.join(get_datasets_dir(config), f"{project_code}_{element_id}_meta.json")

def get_datasets_dir(config: str) -> str:
    base = os.environ.get('DATASETS_DIR', os.path.join(os.path.dirname(__file__), 'datasets'))
    path = os.path.join(base, config)
    os.makedirs(path, exist_ok=True)
    return path

def compute_hash(obj) -> str:
    json_str = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

def build_dataset(signals_data: Dict[str, pd.DataFrame], ref_signal: str,
                  interpolation: str = 'linear') -> pd.DataFrame:
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
        aligned = aligned.fillna(method='ffill').fillna(method='bfill')
        result[name] = pd.to_numeric(aligned['value'], errors='coerce')

    for col in result.columns:
        if col != 'datetime':
            result[col] = pd.to_numeric(result[col], errors='coerce')

    return result.reset_index()

def apply_time_filter(df: pd.DataFrame, intervals: List[dict]) -> pd.DataFrame:
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
        numeric_col = pd.to_numeric(df[col], errors='coerce')
        mask = pd.Series(True, index=df.index)

        if 'min' in rule and rule['min'] is not None:
            mask &= (numeric_col.isna()) | (numeric_col >= rule['min'])
        if 'max' in rule and rule['max'] is not None:
            mask &= (numeric_col.isna()) | (numeric_col <= rule['max'])

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

def load_input_data(element_id: str, project: dict, config: str, project_code: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
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

def apply_time_shift(df: pd.DataFrame, shift_value: int, shift_unit: str,
                     max_gap: pd.Timedelta = MAX_GAP) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if df is None or df.empty:
        empty = df.copy() if df is not None else pd.DataFrame()
        return empty, empty

    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)

    delta = _parse_shift_delta(shift_value, shift_unit)
    times = df['datetime'].values
    n = len(times)
    other_cols = [c for c in df.columns if c != 'datetime']

    if n == 0:
        empty = df.iloc[:0].copy()
        return empty, empty

    kept_orig_idx: List[int] = []
    shifted_rows: List[dict] = []

    for i in range(n):
        t = pd.Timestamp(times[i])
        try:
            t_target = t + delta
        except Exception:
            continue

        pos = int(np.searchsorted(times, np.datetime64(t_target)))

        if pos < n and pd.Timestamp(times[pos]) == t_target:
            src = df.iloc[pos]
            shifted = {c: src[c] for c in other_cols}
        else:
            if pos == 0 or pos >= n:
                continue
            t_left = pd.Timestamp(times[pos - 1])
            t_right = pd.Timestamp(times[pos])
            if t_right - t_left > max_gap:
                continue

            row_left = df.iloc[pos - 1]
            row_right = df.iloc[pos]
            alpha = float((t_target - t_left) / (t_right - t_left))

            shifted = {}
            for c in other_cols:
                try:
                    lv = float(row_left[c])
                    rv = float(row_right[c])
                    shifted[c] = lv + alpha * (rv - lv)
                except (TypeError, ValueError):
                    shifted[c] = row_left[c] if alpha < 0.5 else row_right[c]

        kept_orig_idx.append(i)
        shifted_rows.append(shifted)

    if not kept_orig_idx:
        empty = df.iloc[:0].copy()
        return empty, empty

    df_orig = df.iloc[kept_orig_idx].reset_index(drop=True).copy()
    df_shift = pd.DataFrame(shifted_rows)
    df_shift['datetime'] = df_orig['datetime'] + delta
    cols_order = ['datetime'] + other_cols
    df_shift = df_shift[cols_order]

    return df_orig, df_shift

def apply_labeler(df: pd.DataFrame, x_columns: List[str], y_column: Optional[str],
                  window_size: int = 1, window_unit: str = 'rows',
                  max_gap: pd.Timedelta = MAX_GAP) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Возвращает (X, y, timestamps).
    timestamps — pd.Series с временными метками для каждой строки (нужна для синхронизации).
    """
    if df is None or df.empty:
        return None, None, None

    df = df.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)

    if window_size == 1 and window_unit == 'rows':
        X = df[x_columns].copy() if x_columns else None
        y = df[y_column].copy() if y_column else None
        return X, y, df['datetime']

    window_delta = _parse_window_delta(window_size, window_unit)
    num_points = _window_num_points(window_size, window_unit)

    times = df['datetime'].values
    n = len(times)

    X_rows: List[dict] = []
    y_rows: List = []
    timestamps: List[pd.Timestamp] = []

    for i in range(n):
        t_i = pd.Timestamp(times[i])
        t_start = t_i - window_delta

        mask = (df['datetime'] >= t_start) & (df['datetime'] <= t_i)
        window = df[mask]
        if len(window) < 2:
            continue

        window_ts = window['datetime'].values.astype('datetime64[ns]').astype('int64')

        has_gap = False
        for j in range(1, len(window_ts)):
            gap = pd.Timestamp(window_ts[j]) - pd.Timestamp(window_ts[j - 1])
            if gap > max_gap:
                has_gap = True
                break
        if has_gap:
            continue

        if num_points == 1:
            grid_ts = np.array([int(t_i.value)])
        else:
            grid = pd.date_range(start=t_start, end=t_i, periods=num_points)
            grid_ts = grid.values.astype('datetime64[ns]').astype('int64')

        x_vals: Dict[str, float] = {}
        bad = False
        for col in x_columns:
            col_vals = pd.to_numeric(window[col], errors='coerce').values
            valid = ~np.isnan(col_vals)
            if valid.sum() < 2:
                bad = True
                break
            interp = np.interp(grid_ts, window_ts[valid], col_vals[valid])
            for step in range(num_points):
                x_vals[f"{col}_t-{num_points - 1 - step}"] = float(interp[step])
        if bad:
            continue

        X_rows.append(x_vals)
        if y_column:
            y_rows.append(df[y_column].iloc[i])
        timestamps.append(t_i)

    if not X_rows:
        return None, None, None

    X = pd.DataFrame(X_rows)
    y = pd.Series(y_rows) if y_column else None
    ts = pd.Series(timestamps)
    return X, y, ts


def get_element_status(element_id: str, project: dict, config: str, project_code: str) -> dict:
    elements = project.get('elements', {})
    elem = elements[element_id]
    elem_type = elem.get('nnType') or elem.get('type')
    connections = project.get('connections', [])

    input_hashes = {}
    inputs_up_to_date = True
    for conn in connections:
        if conn['toElement'] == element_id:
            src_id = conn['fromElement']
            src_elem = elements[src_id]
            nn_type = src_elem.get('nnType') or src_elem.get('type')
            
            if nn_type == 'input-signal':
                input_hashes[src_id] = 'signal_fixed'
            elif nn_type == 'nn-settings':
                input_hashes[src_id] = compute_hash(src_elem.get('props', {}))
            else:
                src_status = get_element_status(src_id, project, config, project_code)
                input_hashes[src_id] = src_status['hash']
                if not src_status['up_to_date']:
                    inputs_up_to_date = False

    meta_path = get_meta_path(config, project_code, element_id)
    outputs = {}
    out_mtimes = []
    saved_hash = None
    
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            meta = json.load(f)
        outputs = meta.get('outputs', {})
        saved_hash = meta.get('hash')
        
        for rel in outputs.values():
            if rel:
                full_path = os.path.join(get_datasets_dir(config), rel)
                if os.path.exists(full_path):
                    st = os.stat(full_path)
                    out_mtimes.append(f"{st.st_mtime_ns}_{st.st_size}")
                else:
                    out_mtimes.append("missing")

    # Строгий хэш с учетом mtime
    current_hash = compute_hash({
        'props': elem.get('props', {}),
        'input_hashes': input_hashes,
        'type': elem_type,
        'out_mtimes': out_mtimes
    })

    outputs_exist = all(
        os.path.exists(os.path.join(get_datasets_dir(config), rel))
        for rel in outputs.values() if rel
    )
    
    up_to_date = inputs_up_to_date and outputs_exist and (saved_hash == current_hash)

    return {'hash': current_hash, 'up_to_date': up_to_date, 'outputs': outputs}

def _calculate_shift_between_labelers(project: dict, x_labeler_id: str, y_labeler_id: str) -> pd.Timedelta:
    """Ищет в графе timeshift, который разделяет X и Y, и вычисляет временной сдвиг Y относительно X."""
    connections = project.get('connections', [])
    elements = project.get('elements', {})
    
    def get_path(eid):
        path = []
        visited = set()
        curr = eid
        while curr:
            visited.add(curr)
            conn = next((c for c in connections if c['toElement'] == curr), None)
            if not conn: break
            path.append((conn['fromElement'], conn['fromPort']))
            curr = conn['fromElement']
        return path

    x_path = get_path(x_labeler_id)
    y_path = get_path(y_labeler_id)
    
    total_shift = pd.Timedelta(0)
    # Ищем элементы в пути Y, которые берутся из out-1, а в пути X — из out-0
    for elem_id, port in y_path:
        if port == 'out-1':
            elem = elements.get(elem_id, {})
            if elem.get('nnType') == 'timeshift':
                # Проверяем, есть ли в пути X этот же элемент, но с out-0
                if (elem_id, 'out-0') in x_path:
                    val = elem.get('props', {}).get('shift_value', 1)
                    unit = elem.get('props', {}).get('shift_unit', 'days')
                    total_shift += _parse_shift_delta(val, unit)
    return total_shift


def clear_sync_data(element_id: str, config: str, project_code: str) -> bool:
    """Удаляет сгенерированные _sync файлы и очищает выходы у nn-template."""
    meta_path = get_meta_path(config, project_code, element_id)
    if not os.path.exists(meta_path):
        return False
        
    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
        
    outputs = meta.get('outputs', {})
    deleted = False
    
    for key in ['out-X', 'out-Y']:
        if key in outputs and outputs[key]:
            filepath = os.path.join(get_datasets_dir(config), outputs[key])
            if os.path.exists(filepath):
                os.remove(filepath)
                deleted = True
            outputs.pop(key, None)
            
    # Обнуляем хэш, чтобы статус стал неактуальным
    meta['hash'] = ""
    meta['outputs'] = outputs
    
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f)
        
    return deleted


def process_element(element_id: str, project: dict, config: str, project_code: str, force: bool = False) -> dict:
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
            nn_type = src_elem.get('nnType') or src_elem.get('type')

            if nn_type == 'input-signal':
                input_hashes[src_id] = 'signal_fixed'
            elif nn_type == 'nn-settings':
                input_hashes[src_id] = compute_hash(src_elem.get('props', {}))
            else:
                process_element(src_id, project, config, project_code)
                src_meta_path = get_meta_path(config, project_code, src_id)
                if os.path.exists(src_meta_path):
                    with open(src_meta_path, 'r') as f:
                        src_meta = json.load(f)
                    input_hashes[src_id] = src_meta.get('hash', '')
                else:
                    input_hashes[src_id] = ''

    # 2. Проверка необходимости пересчёта
    out_mtimes_before = []
    saved_hash = None
    if os.path.exists(meta_path):
        with open(meta_path, 'r') as f:
            meta = json.load(f)
        saved_hash = meta.get('hash')
        for rel in meta.get('outputs', {}).values():
            if rel:
                full_path = os.path.join(get_datasets_dir(config), rel)
                if os.path.exists(full_path):
                    st = os.stat(full_path)
                    out_mtimes_before.append(f"{st.st_mtime_ns}_{st.st_size}")
                else:
                    out_mtimes_before.append("missing")

    current_hash = compute_hash({
        'props': elem.get('props', {}),
        'input_hashes': input_hashes,
        'type': elem_type,
        'out_mtimes': out_mtimes_before
    })

    if not force and os.path.exists(meta_path):
        outputs_exist = all(
            os.path.exists(os.path.join(get_datasets_dir(config), rel))
            for rel in meta.get('outputs', {}).values() if rel
        )
        # Если хэши совпали и файлы на месте — отдаем кэш
        if saved_hash == current_hash and outputs_exist:
            return {
                "hash": current_hash,
                "file": os.path.relpath(data_path, get_datasets_dir(config)),
                "input_hashes": input_hashes
            }

    # 3. Выполнение обработки
    meta_timestamps = []
    
    if elem_type == 'dataset':
        df, _ = load_input_data(element_id, project, config, project_code)
        df.to_excel(data_path, index=False)
        outputs = {'out-0': os.path.relpath(data_path, get_datasets_dir(config))}

    elif elem_type in ('filter', 'timefilter', 'timeshift', 'labeler'):
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
            data_path = out0_path

        elif elem_type == 'labeler':
            props = elem.get('props', {})
            x_cols = props.get('x_columns', [])
            y_col = props.get('y_column')
            w_size = props.get('window_size', 1)
            w_unit = props.get('window_unit', 'rows')
            
            X, y, timestamps = apply_labeler(df, x_cols, y_col, w_size, w_unit)
            
            outX_path = get_dataset_path(config, project_code, f"{element_id}_X")
            outy_path = get_dataset_path(config, project_code, f"{element_id}_y")
            
            if X is not None:
                X.to_excel(outX_path, index=False)
            if y is not None:
                y.to_frame(name=y_col if y_col else 'y').to_excel(outy_path, index=False)
                
            outputs = {
                'out-0': os.path.relpath(outX_path, get_datasets_dir(config)) if X is not None else None,
                'out-1': os.path.relpath(outy_path, get_datasets_dir(config)) if y is not None else None
            }
            data_path = outX_path if X is not None else outy_path
            
            if timestamps is not None and not timestamps.empty:
                meta_timestamps = [t.isoformat() for t in timestamps]

    elif elem_type == 'nn-template':
        def get_conn(port):
            return next((c for c in connections if c['toElement'] == element_id and c['toPort'] == port), None)
        
        x_tr, y_tr = get_conn('in-1'), get_conn('in-2')
        if not x_tr or not y_tr:
            raise ValueError("nn-template: требуются подключения X_train и Y_train")

        x_labeler_id = x_tr['fromElement']
        y_labeler_id = y_tr['fromElement']

        x_meta = get_meta_path(config, project_code, x_labeler_id)
        y_meta = get_meta_path(config, project_code, y_labeler_id)
        
        if not os.path.exists(x_meta) or not os.path.exists(y_meta):
            raise ValueError("Метаданные лейблеров не найдены. Примените лейблеры заново.")

        with open(x_meta) as f: x_times = pd.to_datetime(json.load(f).get('timestamps', []))
        with open(y_meta) as f: y_times = pd.to_datetime(json.load(f).get('timestamps', []))

        if len(x_times) == 0 or len(y_times) == 0:
            raise ValueError("Один из лейблеров не сформировал временных меток.")

        time_shift = _calculate_shift_between_labelers(project, x_labeler_id, y_labeler_id)
        if time_shift > pd.Timedelta(0):
            y_times = y_times - time_shift

        x_df_time = pd.DataFrame({'datetime': x_times, 'idx_x': range(len(x_times))})
        y_df_time = pd.DataFrame({'datetime': y_times, 'idx_y': range(len(y_times))})
        
        merged_times = pd.merge(x_df_time, y_df_time, on='datetime', how='inner')
        if merged_times.empty:
            raise ValueError("Нет общих временных меток между X и Y.")

        x_path = get_output_file(elements[x_labeler_id], x_tr['fromPort'], config, project_code)
        y_path = get_output_file(elements[y_labeler_id], y_tr['fromPort'], config, project_code)
        
        X_df = pd.read_excel(x_path).iloc[merged_times['idx_x'].values].reset_index(drop=True)
        Y_df = pd.read_excel(y_path).iloc[merged_times['idx_y'].values].reset_index(drop=True)

        outX = get_dataset_path(config, project_code, f"{element_id}_X_sync")
        outY = get_dataset_path(config, project_code, f"{element_id}_Y_sync")
        X_df.to_excel(outX, index=False)
        Y_df.to_excel(outY, index=False)
        
        outputs = {
            'out-X': os.path.relpath(outX, get_datasets_dir(config)),
            'out-Y': os.path.relpath(outY, get_datasets_dir(config))
        }
        data_path = outX

    else:
        raise ValueError(f"Неизвестный тип элемента: {elem_type}")

    # 4. Сохраняем метаданные
    out_mtimes_after = []
    for rel in outputs.values():
        if rel:
            full_path = os.path.join(get_datasets_dir(config), rel)
            if os.path.exists(full_path):
                st = os.stat(full_path)
                out_mtimes_after.append(f"{st.st_mtime_ns}_{st.st_size}")

    final_hash = compute_hash({
        'props': elem.get('props', {}),
        'input_hashes': input_hashes,
        'type': elem_type,
        'out_mtimes': out_mtimes_after
    })

    meta_data = {
        'hash': final_hash,
        'timestamp': pd.Timestamp.now().isoformat(),
        'outputs': outputs
    }
    if elem_type == 'labeler':
        meta_data['timestamps'] = meta_timestamps

    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta_data, f)

    return {
        "hash": final_hash,
        "file": os.path.relpath(data_path, get_datasets_dir(config)),
        "input_hashes": input_hashes
    }