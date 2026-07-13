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

def load_input_data(element_id: str, project: dict, config: str,project_code: str,
                    loaded_cache: dict = None) -> Tuple[pd.DataFrame, Dict[str, str]]:
    elements = project.get('elements', {})
    connections = project.get('connections', [])
    elem = elements[element_id]
    inputs = []

    for conn in connections:
        if conn['toElement'] == element_id:
            inputs.append((conn['toPort'], conn['fromElement']))

    inputs.sort(key=lambda x: int(x[0].split('-')[1]))

    signals_data = {}
    hashes = {}
    ref_index = elem.get('props', {}).get('reference_signal_index', 0)
    interpolation = elem.get('props', {}).get('interpolation', 'linear')

    for port, src_id in inputs:
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

            # 1. Заменяем запятые на точки в значениях
            df['value'] = df['value'].astype(str).str.replace(',', '.', regex=False)

            # 2. Преобразуем в числовой тип (нечисловые строки станут NaN)
            df['value'] = pd.to_numeric(df['value'], errors='coerce')

            # 3. НЕ удаляем строки с NaN, чтобы сохранить исходный индекс времени.
            #    Интерполяция в build_dataset заполнит пропуски.
            signals_data[signal_name] = df
            hashes[src_id] = compute_hash({'name': signal_name, 'rows': len(df)})
        else:
            path = get_dataset_path(config, project_code, src_id)
            if not os.path.exists(path):
                raise ValueError(f"Для элемента {src_id} отсутствуют обработанные данные. Примените его сначала.")
            df = pd.read_excel(path)
            for col in df.columns:
                if col != 'datetime':
                    signals_data[col] = df[['datetime', col]].rename(columns={col: 'value'})
            file_hash = compute_hash({'file': path, 'mtime': os.path.getmtime(path)})
            hashes[src_id] = file_hash

    if not signals_data:
        raise ValueError("Нет входных данных для построения датасета")

    ref_names = list(signals_data.keys())
    if ref_index >= len(ref_names):
        ref_index = 0
    ref_name = ref_names[ref_index]

    df_combined = build_dataset(signals_data, ref_name, interpolation)

    # Дополнительно: после build_dataset все значения должны быть числовыми,
    # но на всякий случай ещё раз применим pd.to_numeric (уже есть в функции)
    return df_combined, hashes

def process_element(element_id: str, project: dict, config: str, project_code: str) -> str:
    """
    Основная функция обработки элемента. Рекурсивно проверяет входные элементы,
    при необходимости пересчитывает их, затем обрабатывает текущий.
    Возвращает хэш сохранённого результата.
    """
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
            # Если входной элемент – input-signal, данные загружаются динамически, файла нет.
            if src_elem['type'] != 'input-signal':
                # Это обработанный элемент – вызываем для него process_element
                process_element(src_id, project, config, project_code)
                # После обработки у него должен появиться meta_path
                src_meta_path = get_meta_path(config, project_code, src_id)
                if os.path.exists(src_meta_path):
                    with open(src_meta_path, 'r') as f:
                        src_meta = json.load(f)
                    input_hashes[src_id] = src_meta.get('hash', '')
                else:
                    input_hashes[src_id] = ''
            else:
                # Для input-signal хэш не хранится, будем считать, что данные не меняются
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
        if meta.get('hash') == current_hash and os.path.exists(data_path):
            # Данные актуальны
            return {
                "hash": current_hash,
                "file": os.path.relpath(data_path, get_datasets_dir(config)),
                "input_hashes": input_hashes
            }

    # 3. Выполнение обработки
    if elem_type == 'dataset':
        df, _ = load_input_data(element_id, project, config, project_code)
        df.to_excel(data_path, index=False)
    elif elem_type == 'filter':
        # Загружаем входной датасет (должен быть ровно один вход)
        inputs = []
        for conn in connections:
            if conn['toElement'] == element_id:
                inputs.append(conn['fromElement'])
        if len(inputs) != 1:
            raise ValueError("Элемент 'Фильтрация данных' должен иметь ровно один вход")
        src_id = inputs[0]
        src_elem = elements[src_id]
        if src_elem['type'] == 'input-signal':
            # Нелогично, но допустим: загрузим сигнал напрямую? Пока ошибка.
            raise ValueError("Фильтрация не может применяться напрямую к входному сигналу, используйте 'Собрать датасет'")
        src_data_path = get_dataset_path(config, project_code, src_id)
        if not os.path.exists(src_data_path):
            raise ValueError("Входной датасет не найден. Примените предшествующий элемент.")
        df = pd.read_excel(src_data_path)
        rules = elem.get('props', {}).get('rules', [])
        df = filter_dataset(df, rules)
        df.to_excel(data_path, index=False)
    else:
        raise ValueError(f"Неизвестный тип элемента для обработки: {elem_type}")

    # 4. Сохраняем метаданные с хэшем
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump({'hash': current_hash, 'timestamp': pd.Timestamp.now().isoformat()}, f)

    return {
        "hash": current_hash,
        "file": os.path.relpath(data_path, get_datasets_dir(config)),  # относительный путь
        "input_hashes": input_hashes
    }