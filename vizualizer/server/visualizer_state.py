# visualizer_state.py — модуль для сериализации/десериализации состояния визуализатора

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
import pandas as pd


# Версия формата состояния (для обратной совместимости в будущем)
STATE_VERSION = 1


def serialize_timestamp(ts) -> Optional[str]:
    """Конвертирует Timestamp/datetime в ISO строку"""
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    if isinstance(ts, (datetime, pd.Timestamp)):
        return ts.isoformat()
    return str(ts)


def deserialize_timestamp(ts_str: Optional[str]) -> Optional[pd.Timestamp]:
    """Конвертирует ISO строку в Timestamp"""
    if ts_str is None:
        return None
    try:
        return pd.Timestamp(ts_str)
    except Exception:
        return None


def serialize_shape(shape: Dict[str, Any]) -> Dict[str, Any]:
    """Сериализует один маркер (shape) для JSON"""
    result = {
        'type': shape.get('type'),
        'dash': shape.get('dash', 'solid'),
        'color': shape.get('color', 'gray')
    }
    
    if shape.get('type') == 'vline':
        result['x'] = serialize_timestamp(shape.get('x'))
    elif shape.get('type') == 'hline':
        result['y'] = shape.get('y')
    
    return result


def deserialize_shape(shape_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Десериализует маркер из JSON"""
    shape_type = shape_data.get('type')
    
    if shape_type not in ('vline', 'hline'):
        return None
    
    result = {
        'type': shape_type,
        'dash': shape_data.get('dash', 'solid'),
        'color': shape_data.get('color', 'gray')
    }
    
    if shape_type == 'vline':
        ts = deserialize_timestamp(shape_data.get('x'))
        if ts is None:
            return None
        result['x'] = ts
    elif shape_type == 'hline':
        y_val = shape_data.get('y')
        if y_val is None:
            return None
        result['y'] = float(y_val)
    
    return result


def serialize_plot_area(plot_area: Dict[str, Any]) -> Dict[str, Any]:
    """Сериализует одну область графика"""
    return {
        'id': plot_area.get('id', 1),
        'signals': list(plot_area.get('signals', [])),
        'shapes': [serialize_shape(s) for s in plot_area.get('shapes', [])],
        # cursor_time и диапазоны НЕ сохраняем — они пересчитываются
    }


def deserialize_plot_area(
    area_data: Dict[str, Any], 
    available_signals: Set[str]
) -> Optional[Dict[str, Any]]:
    """
    Десериализует область графика.
    Фильтрует сигналы, которых нет в available_signals.
    """
    area_id = area_data.get('id', 1)
    
    # Фильтруем сигналы — оставляем только существующие
    raw_signals = area_data.get('signals', [])
    valid_signals = [s for s in raw_signals if s in available_signals]
    
    # Если после фильтрации не осталось сигналов — пропускаем область
    # (но можно оставить пустую, если хотите)
    
    # Десериализуем маркеры
    shapes = []
    for shape_data in area_data.get('shapes', []):
        shape = deserialize_shape(shape_data)
        if shape is not None:
            shapes.append(shape)
    
    return {
        'id': area_id,
        'signals': valid_signals,
        'shapes': shapes,
        'cursor_time': None,  # Пересчитывается при загрузке
        'x_range': None,      # Пересчитывается при загрузке
        'y_range': None       # Пересчитывается при загрузке
    }


def create_visualizer_state(
    selected_signals: Set[str],
    plot_areas: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Создаёт объект состояния визуализатора для сохранения.
    
    Args:
        selected_signals: Набор выбранных сигналов
        plot_areas: Список областей графиков
    
    Returns:
        Словарь, готовый для JSON сериализации
    """
    return {
        'version': STATE_VERSION,
        'selected_signals': sorted(list(selected_signals)),
        'plot_areas': [serialize_plot_area(pa) for pa in plot_areas]
    }


def load_visualizer_state(
    state_data: Optional[Dict[str, Any]],
    available_signals: Set[str]
) -> tuple[Set[str], List[Dict[str, Any]], List[str]]:
    """
    Загружает и валидирует состояние визуализатора.
    
    Args:
        state_data: Данные состояния из JSON (может быть None)
        available_signals: Набор доступных сигналов в проекте
    
    Returns:
        Tuple из:
        - selected_signals: Набор выбранных сигналов (отфильтрованный)
        - plot_areas: Список областей графиков (отфильтрованный)
        - warnings: Список предупреждений о пропущенных сигналах
    """
    warnings = []
    
    # Если состояния нет — возвращаем пустые значения
    if state_data is None:
        return set(), [], []
    
    # Проверяем версию (для будущей совместимости)
    version = state_data.get('version', 1)
    if version > STATE_VERSION:
        warnings.append(f"Версия состояния ({version}) новее поддерживаемой ({STATE_VERSION})")
    
    # Загружаем выбранные сигналы
    raw_selected = state_data.get('selected_signals', [])
    selected_signals = set()
    missing_signals = []
    
    for sig in raw_selected:
        if sig in available_signals:
            selected_signals.add(sig)
        else:
            missing_signals.append(sig)
    
    if missing_signals:
        warnings.append(f"Сигналы не найдены и пропущены: {', '.join(missing_signals)}")
    
    # Загружаем области графиков
    plot_areas = []
    for area_data in state_data.get('plot_areas', []):
        area = deserialize_plot_area(area_data, available_signals)
        if area is not None:
            plot_areas.append(area)
    
    return selected_signals, plot_areas, warnings


def state_to_json(state: Dict[str, Any]) -> str:
    """Конвертирует состояние в JSON строку"""
    return json.dumps(state, ensure_ascii=False, indent=2)


def state_from_json(json_str: str) -> Optional[Dict[str, Any]]:
    """Парсит JSON строку в состояние"""
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None