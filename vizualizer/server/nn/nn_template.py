"""
nn_template.py
Утилиты для элемента "Шаблон" (nn-template):
  - чтение сохранённого дизайн-проекта нейросети по КОДУ (ККС),
    список доступных дизайнов для автокомплита
  - воссоздание строки структуры (аналог generateStructureString() из
    neural_app.js) и словаря options по позициям слоёв — то, что
    напрямую скармливается в BlockStruct(structure, options=...)
  - вычисление hash'а "актуальности" обученной модели: если дизайн,
    настройки обучения или входные данные поменялись — модель считается
    устаревшей и это видно в UI при открытии проекта

Хранилище проектов переиспользует конвенции main.py: файлы .json лежат в
config_path("projectDataFolder", config), внутри {"project": {code, type, ...},
"elements": {...}, "connections": [...]}. Код (ККС) ищем по содержимому
(project.code), а не по имени файла — так же, как это делает
main.load_project_by_code().

ВАЖНО: config_path НЕ импортируется из main напрямую (даже лениво внутри
функции) — этот модуль используется в двух разных процессах:
  1) внутри процесса FastAPI (main.py), где модуль main существует;
  2) внутри train_worker.py — САМОСТОЯТЕЛЬНОГО процесса, запускаемого
     subprocess'ом, где main.py вообще не импортирован (и импортировать
     его туда не стоит — это утащит за собой mount статики, CORS и все
     тяжёлые зависимости main.py в процесс обучения).
Поэтому config_path внедряется через set_config_path() — каждый процесс
передаёт СВОЮ реализацию:
  - main.py при старте:      nn_template.set_config_path(config_path)
  - train_worker.py при старте: nn_template.set_config_path(<своя мини-реализация>)
"""
import os
import json
import hashlib
from typing import Dict, List, Optional, Tuple, Callable

#from Block_Struct import BlockStruct

# Множество кодов слоёв дизайна сети — берём напрямую из BlockStruct,
# а не гадаем строковую константу PROJECT_TYPE.NEURAL_TEMPLATE с фронта
# (её определения не было в присланных файлах).
DESIGN_LAYER_TYPES = {'c','sc','mp','ct','us','bn','den','drop','re','fl','act','pad','add','out'}

# Типы элементов, которые встречаются только в training-режиме (не в дизайне сети) —
# по ним отсекаем не-дизайн проекты, даже если в них случайно совпадёт код слоя.
TRAINING_ONLY_TYPES = {'nn-template', 'nn-settings', 'dataset', 'filter',
                        'timefilter', 'timeshift', 'labeler', 'input-signal', 'table', 'group'}


# ---------------------------------------------------------------------------
# Инъекция config_path (см. докстринг выше) — обязательна к вызову перед
# использованием любой из функций этого модуля.
# ---------------------------------------------------------------------------

_config_path_fn: Optional[Callable[[str, str], Optional[str]]] = None
_template_dir_fn: Optional[Callable[[], Optional[str]]] = None

DEBUG = os.environ.get('NN_TEMPLATE_DEBUG', '0') == '1'


def set_config_path(fn: Callable[[str, str], Optional[str]]) -> None:
    """fn(setting_key: str, config: str) -> абсолютный путь | None,
    сигнатура идентична main.config_path()."""
    global _config_path_fn
    _config_path_fn = fn


def set_template_dir(fn: Callable[[], Optional[str]]) -> None:
    """fn() -> абсолютный путь к глобальной templateDataFolder (не зависит от config).
    Нужно, ЕСЛИ у вас дизайн-проекты сети сохраняются с project.type == "template" —
    тогда main.py/api/project/save уводит их не в projectDataFolder/<config>, а в
    общую templateDataFolder (см. main.save_project: `if project_type == "template":
    target = "templates"`). Если это не ваш случай — просто не вызывайте этот сеттер,
    поиск по этой папке будет молча пропущен."""
    global _template_dir_fn
    _template_dir_fn = fn


def _require_config_path() -> Callable[[str, str], Optional[str]]:
    if _config_path_fn is None:
        raise RuntimeError(
            "nn_template: config_path не задан. Вызовите nn_template.set_config_path(...) "
            "при старте процесса (main.py в @app.on_event('startup'), "
            "train_worker.py — в начале скрипта)."
        )
    return _config_path_fn


def _projects_dir(config: str) -> str:
    config_path = _require_config_path()
    folder = config_path("projectDataFolder", config)
    if not folder:
        raise RuntimeError("projectDataFolder not configured")
    return folder


DESIGN_PROJECT_TYPE = 'neural_template'  # project.type у дизайн-проектов сети — подтверждено на реальном файле


def is_design_project(payload: dict) -> bool:
    """Отличает дизайн-проект сети (собран в режиме 'design' из слоёв c/den/...)
    от прочих проектов (formula-проекты, training-проекты со сборкой датасетов).
    Основной признак — project.type; эвристика по составу nnType слоёв
    оставлена как fallback на случай старых файлов без выставленного type."""
    proj_meta = payload.get('project', {}) or {}
    if proj_meta.get('type') == DESIGN_PROJECT_TYPE:
        return True

    elements = payload.get('elements', {}) or {}
    if not elements:
        return False
    types = {(e.get('nnType') or e.get('type')) for e in elements.values()}
    if types & TRAINING_ONLY_TYPES:
        return False
    return bool(types & DESIGN_LAYER_TYPES)


def _scan_dir(folder: str):
    if not folder or not os.path.isdir(folder):
        if DEBUG:
            print(f"[nn_template] каталог отсутствует или не задан: {folder!r}")
        return
    for fname in sorted(os.listdir(folder)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(folder, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as e:
            if DEBUG:
                print(f"[nn_template] не смог прочитать {path}: {e}")
            continue
        if DEBUG:
            proj_meta = payload.get('project', {}) or {}
            elements = payload.get('elements', {}) or {}
            types = sorted({(el.get('nnType') or el.get('type')) for el in elements.values()})
            print(f"[nn_template] файл={fname} code={proj_meta.get('code')!r} "
                  f"type={proj_meta.get('type')!r} element_types={types} "
                  f"is_design={is_design_project(payload)}")
        yield fname, payload


def _iter_project_files(config: str):
    yield from _scan_dir(_projects_dir(config))
    if _template_dir_fn is not None:
        yield from _scan_dir(_template_dir_fn())


def _load_project_file(config: str, project_code: str) -> Optional[dict]:
    """Ищет файл проекта по коду (ККС) в каталоге конфигурации — так же,
    как main.load_project_by_code(), но без ограничения source='projects'."""
    for _fname, payload in _iter_project_files(config):
        proj_meta = payload.get('project', {}) or {}
        code = (proj_meta.get('code') or proj_meta.get('tagname') or '').strip()
        if code == project_code:
            return payload
    return None


def list_design_projects(config: str, query: str = '') -> List[str]:
    """Возвращает список кодов (ККС) дизайн-проектов сети в конфигурации,
    отфильтрованных по подстроке query (регистронезависимо), для автокомплита
    в модалке элемента "Шаблон". query может содержать '*' как в вводе
    пользователя — трактуем его как wildcard (просто ищем по вхождению
    оставшейся части строки)."""
    q = (query or '').replace('*', '').strip().lower()
    result = []
    for _fname, payload in _iter_project_files(config):
        if not is_design_project(payload):
            continue
        proj_meta = payload.get('project', {}) or {}
        code = (proj_meta.get('code') or proj_meta.get('tagname') or '').strip()
        if not code:
            continue
        if not q or q in code.lower():
            result.append(code)
    return sorted(set(result))


def debug_scan(config: str) -> dict:
    """ВРЕМЕННАЯ диагностика — не вызывать из боевого кода. Показывает
    буквально всё, что видит бэкенд: реальный абсолютный путь к папке,
    какие файлы в ней есть, что распарсилось, и почему каждый файл
    попал/не попал в список дизайнов. Подключить к отдельному GET-эндпоинту
    (см. main_py_additions.py) и открыть в браузере — весь ответ будет
    прямо в теле JSON, без необходимости смотреть логи сервера."""
    out = {
        'config': config,
        'config_path_is_set': _config_path_fn is not None,
        'template_dir_is_set': _template_dir_fn is not None,
        'files': [],
    }
    try:
        out['projects_dir'] = _projects_dir(config)
        out['projects_dir_exists'] = os.path.isdir(out['projects_dir'])
    except Exception as e:
        out['projects_dir_error'] = str(e)
        return out

    if _template_dir_fn is not None:
        try:
            out['template_dir'] = _template_dir_fn()
        except Exception as e:
            out['template_dir_error'] = str(e)

    for _fname, payload in _iter_project_files(config):
        proj_meta = payload.get('project', {}) or {}
        elements = payload.get('elements', {}) or {}
        types = sorted({(el.get('nnType') or el.get('type')) for el in elements.values()})
        out['files'].append({
            'filename': _fname,
            'code': proj_meta.get('code'),
            'type': proj_meta.get('type'),
            'element_types': types,
            'is_design_project': is_design_project(payload),
        })
    return out


def load_design_project(config: str, design_code: str) -> dict:
    payload = _load_project_file(config, design_code)
    if payload is None:
        raise FileNotFoundError(f"Дизайн-проект '{design_code}' не найден в конфигурации '{config}'")
    if not is_design_project(payload):
        raise ValueError(f"Проект '{design_code}' не является дизайном нейросети (нет слоёв {sorted(DESIGN_LAYER_TYPES)})")
    return payload


# ---------------------------------------------------------------------------
# Воссоздание структуры и options — ТОЧНАЯ копия логики
# NeuralApp.generateStructureString() из neural_app.js (топосортировка +
# правила для 'out'/'add'), но с параллельным накоплением options[idx].
# Это нужно, чтобы BlockStruct(structure, options=options) построил ровно
# ту сеть, которую пользователь собрал визуально в режиме "design".
# ---------------------------------------------------------------------------

def build_structure_and_options(design_project: dict) -> Tuple[str, Dict[int, dict]]:
    elements: Dict[str, dict] = design_project.get('elements', {})
    connections: List[dict] = design_project.get('connections', [])

    # 1. Топологическая сортировка (как в JS)
    in_degree = {eid: 0 for eid in elements}
    graph: Dict[str, list] = {eid: [] for eid in elements}
    for c in connections:
        graph[c['fromElement']].append(c['toElement'])
        in_degree[c['toElement']] = in_degree.get(c['toElement'], 0) + 1

    queue = [eid for eid in elements if in_degree[eid] == 0]
    sorted_ids = []
    while queue:
        eid = queue.pop(0)
        sorted_ids.append(eid)
        for to in graph[eid]:
            in_degree[to] -= 1
            if in_degree[to] == 0:
                queue.append(to)

    if len(sorted_ids) != len(elements):
        raise ValueError("В дизайн-проекте обнаружен цикл связей — структуру построить нельзя")

    pos = {eid: i for i, eid in enumerate(sorted_ids)}

    def out_edges(eid):
        return [c for c in connections if c['fromElement'] == eid]

    out_indices: Dict[str, int] = {}
    out_counter = 0
    parts: List[str] = []
    options: Dict[int, dict] = {}

    group_idx = 0  # индекс группы = позиция в итоговой строке structure (совпадает с индексом в parts)

    for eid in sorted_ids:
        elem = elements[eid]
        nn_type = elem.get('nnType') or elem.get('type')

        if nn_type == 'out':
            out_indices[eid] = out_counter
            out_counter += 1
            parts.append('out')
            group_idx += 1
            continue

        if nn_type == 'add':
            skip_indices = []
            for c in connections:
                if c['toElement'] == eid and c.get('toPort') != 'in-0':
                    src = c['fromElement']
                    if src in out_indices:
                        skip_indices.append(out_indices[src])
            skip_indices.sort()
            parts.append('add{%s}' % ','.join(str(i) for i in skip_indices))
            group_idx += 1
            continue

        # обычный слой
        edges = out_edges(eid)
        need_out = False
        if len(edges) > 1:
            edges = sorted(edges, key=lambda c: pos[c['toElement']])
            need_out = True  # хотя бы одно разветвление помимо основного пути

        parts.append(nn_type)
        options[group_idx] = dict(elem.get('props', {}))
        group_idx += 1

        if need_out:
            out_indices[eid] = out_counter
            out_counter += 1
            parts.append('out')
            group_idx += 1

    return '_'.join(parts), options


def design_hash(design_project: dict) -> str:
    """Хэш дизайна: любое изменение слоёв/связей/параметров -> новый хэш."""
    payload = {
        'elements': {eid: {'nnType': e.get('nnType') or e.get('type'), 'props': e.get('props', {})}
                     for eid, e in design_project.get('elements', {}).items()},
        'connections': design_project.get('connections', []),
    }
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# Пути хранения обученных моделей и job'ов очереди
# ---------------------------------------------------------------------------

def get_models_dir(config: str) -> str:
    """Требует ключ "trainedModelsFolder" в settings.json (по аналогии с
    "projectDataFolder"/"signalDataFolder" и т.д.) + добавить его в список
    ключей в main.ensure_config_dirs(), чтобы подпапка конфигурации
    создавалась автоматически при старте — см. main_py_additions.py."""
    config_path = _require_config_path()
    path = config_path("trainedModelsFolder", config)
    if not path:
        raise RuntimeError('"trainedModelsFolder" не задан в settings.json')
    os.makedirs(path, exist_ok=True)
    return path


def get_model_path(config: str, project_code: str, element_id: str) -> str:
    return os.path.join(get_models_dir(config), f"{project_code}_{element_id}.keras")


def get_model_meta_path(config: str, project_code: str, element_id: str) -> str:
    return os.path.join(get_models_dir(config), f"{project_code}_{element_id}_meta.json")


def compute_train_hash(design_code: str, d_hash: str, settings_props: dict, input_hashes: dict) -> str:
    payload = {
        'design_code': design_code,
        'design_hash': d_hash,
        'settings': settings_props,
        'input_hashes': input_hashes,
    }
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def get_template_status(element_id: str, project: dict, config: str, project_code: str,
                         current_job_lookup=None) -> dict:
    """Статус элемента "Шаблон" для окраски при открытии проекта.
    current_job_lookup: необязательная функция(element_id, project_code) -> job dict|None,
    чтобы отразить "в очереди / обучается" даже если модель ещё не сохранена.
    """
    elements = project.get('elements', {})
    elem = elements.get(element_id, {})
    design_code = elem.get('props', {}).get('design_code', '')

    result = {
        'trained': False,
        'up_to_date': False,
        'design_code': design_code,
        'job': None,
    }

    if current_job_lookup:
        job = current_job_lookup(element_id, project_code)
        if job:
            result['job'] = {'job_id': job.get('job_id'), 'status': job.get('status')}

    if not design_code:
        return result

    meta_path = get_model_meta_path(config, project_code, element_id)
    if not os.path.exists(meta_path):
        return result

    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    result['trained'] = os.path.exists(get_model_path(config, project_code, element_id))

    try:
        design_project = load_design_project(config, design_code)
        d_hash = design_hash(design_project)
    except FileNotFoundError:
        return result  # дизайн удалён/переименован — считаем неактуальным

    # settings + входные данные сверяем по тому, что было сохранено при обучении
    # (без повторного чтения connected-элементов здесь, т.к. это делает вызывающая
    # сторона на этапе постановки в очередь — см. main_py_additions.build_train_job_payload)
    current_hash = compute_train_hash(design_code, d_hash, meta.get('settings', {}), meta.get('input_hashes', {}))
    result['up_to_date'] = (meta.get('train_hash') == current_hash)
    result['trained_at'] = meta.get('trained_at')
    return result