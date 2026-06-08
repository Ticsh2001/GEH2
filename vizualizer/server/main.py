# main.py — версия с поддержкой конфигураций

import os
import re
import json
import uuid
import pickle
import tempfile
from typing import Dict, List, Any, Optional
from io import BytesIO
from update_projects import update_projects_if_templates_changed
from datetime import datetime
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Query, File, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
from fastapi import Body
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from urllib.parse import quote
from export import export_selected_projects
from export import get_code_length

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
TEMPLATES_PATH = os.path.join(BASE_DIR, "formula_templates.json")
SIGNAL_INDEX_CACHE = {}          # кэш индексов сигналов: config -> (folder_state, index)
SIGNALS_CACHE = {}              # кэш списков сигналов: config -> list
TABLES_CACHE = {}               # кэш списка таблиц: config -> list

# =============================================================================
# PYDANTIC МОДЕЛИ
# =============================================================================

class VisualizerStateRequest(BaseModel):
    session_token: str
    state: Dict[str, Any]

class VisualizerStateResponse(BaseModel):
    success: bool
    state: Optional[Dict[str, Any]] = None
    message: Optional[str] = None

class VisualizeSessionRequest(BaseModel):
    signals: List[str]
    code: str = ""
    visualizer_state: Optional[Dict[str, Any]] = None

# =============================================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# =============================================================================

STATE = {
    "settings": None,
    "signals": None,          # больше не глобальный, оставлен для обратной совместимости (не используется)
    "templates": None,
    "configurations": [],     # список имён конфигураций
}

TAGS_FILE = "tags.json"

def load_tags_data():
    if not os.path.exists(TAGS_FILE):
        return {"tags": [], "assignments": {}}
    try:
        with open(TAGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"tags": [], "assignments": {}}

def save_tags_data(data):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# -----------------------------------------------------------------------------
# Вспомогательные функции для работы с путями конфигураций
# -----------------------------------------------------------------------------

def _abs_folder(setting_key: str) -> Optional[str]:
    """Абсолютный путь к базовой папке из настроек (без учёта конфигурации)."""
    base = STATE["settings"].get(setting_key)
    if not base:
        return None
    if not os.path.isabs(base):
        base = os.path.normpath(os.path.join(BASE_DIR, base))
    return base

def config_path(setting_key: str, config: str) -> Optional[str]:
    """Возвращает абсолютный путь к подпапке <config> внутри базовой папки."""
    base = _abs_folder(setting_key)
    if not base:
        return None
    return os.path.join(base, config) if config else base

def ensure_config_dirs(config: str):
    """Создаёт подпапки конфигурации во всех рабочих папках, если их ещё нет."""
    for key in ("projectDataFolder", "signalDataFolder", "signalArchiveFolder",
                "tablesFolder", "deletedFolder"):  # добавим deletedFolder
        base = _abs_folder(key)
        if base:
            full = os.path.join(base, config)
            os.makedirs(full, exist_ok=True)

# -----------------------------------------------------------------------------
# Работа с таблицами
# -----------------------------------------------------------------------------

def load_tables_from_folder(folder: str) -> List[Dict]:
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    if not os.path.isdir(folder_abs):
        return []
    items = []
    for name in os.listdir(folder_abs):
        if not name.lower().endswith(".xlsx"):
            continue
        base_name = os.path.splitext(name)[0]
        items.append({"Name": base_name, "Description": ""})
    items.sort(key=lambda x: x["Name"].lower())
    return items

def load_tables_meta(folder: str) -> Dict[str, str]:
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    meta_path = os.path.join(folder_abs, "tables.json")
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        meta = {}
        if isinstance(data, list):
            for obj in data:
                if isinstance(obj, dict) and obj:
                    k, v = next(iter(obj.items()))
                    meta[str(k)] = str(v)
        elif isinstance(data, dict):
            meta = {str(k): str(v) for k, v in data.items()}
        return meta
    except Exception as e:
        print(f"[WARN] failed to read tables.json: {e}")
        return {}

def get_tables_for_config(config: str) -> List[Dict]:
    """Возвращает список таблиц для заданной конфигурации, используя кэш."""
    if config in TABLES_CACHE:
        return TABLES_CACHE[config]
    folder = config_path("tablesFolder", config)
    if not folder:
        return []
    items = load_tables_from_folder(folder)
    meta = load_tables_meta(folder)
    for item in items:
        name = item["Name"]
        if name in meta:
            item["Description"] = meta[name]
    TABLES_CACHE[config] = items
    return items

# -----------------------------------------------------------------------------
# Загрузка настроек и шаблонов
# -----------------------------------------------------------------------------

def load_settings() -> Dict:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_templates() -> Dict:
    if not os.path.exists(TEMPLATES_PATH):
        return {"templates": []}
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# -----------------------------------------------------------------------------
# Сигналы (базовые + проектные)
# -----------------------------------------------------------------------------

def load_signals_from_folder(folder: str) -> List[Dict]:
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    if not os.path.isdir(folder_abs):
        return []
    signals_map = {}
    for name in os.listdir(folder_abs):
        if not name.lower().endswith(".csv"):
            continue
        path = os.path.join(folder_abs, name)
        try:
            try:
                df = pd.read_csv(path, sep=';')[['Tagname', 'Description', 'Engineering Unit']]
            except KeyError:
                df = pd.read_csv(path, sep=';')[['Tagname', 'Description']]
            df = df.dropna(subset=['Tagname'])
            for _, row in df.iterrows():
                tag = str(row['Tagname']).strip()
                desc = "" if pd.isna(row['Description']) else str(row['Description']).strip()
                try:
                    unit = "" if pd.isna(row['Engineering Unit']) else str(row['Engineering Unit']).strip()
                except KeyError:
                    unit = ""
                desc_full = ", ".join([x for x in [desc, unit] if x])
                if tag:
                    signals_map[tag] = {
                        "Tagname": tag,
                        "Description": desc_full,
                        "EngineeringUnit": unit
                    }
        except Exception as e:
            print(f"[WARN] failed to read {path}: {e}")
    out = list(signals_map.values())
    out.sort(key=lambda x: x["Tagname"])
    return out

def load_project_signals(folder: str) -> List[Dict]:
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    if not os.path.isdir(folder_abs):
        return []
    out = []
    for name in os.listdir(folder_abs):
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder_abs, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            proj = payload.get("project", {}) or {}
            code = (proj.get("code") or "").strip()
            if not code:
                continue
            desc = (proj.get("description") or "").strip()
            dim = (proj.get("dimension") or "").strip()
            out.append({
                "Tagname": code,
                "Description": desc,
                "EngineeringUnit": dim,
                "Type": proj.get("type", "")
            })
        except Exception as e:
            print(f"[WARN] failed to read project {path}: {e}")
            continue
    out.sort(key=lambda x: x["Tagname"])
    return out

def get_signals_for_config(config: str) -> List[Dict]:
    """Возвращает объединённый список сигналов для конфигурации, с кэшированием."""
    if config in SIGNALS_CACHE:
        return SIGNALS_CACHE[config]
    base_folder = config_path("signalDataFolder", config)
    proj_folder = config_path("projectDataFolder", config)
    base = load_signals_from_folder(base_folder) if base_folder else []
    proj = load_project_signals(proj_folder) if proj_folder else []
    merged = {}
    for s in base:
        merged[s["Tagname"]] = s
    for s in proj:
        merged[s["Tagname"]] = s
    out = list(merged.values())
    out.sort(key=lambda x: x["Tagname"])
    SIGNALS_CACHE[config] = out
    return out

def invalidate_signals_cache(config: str):
    """Сбрасывает кэш сигналов для конкретной конфигурации."""
    SIGNALS_CACHE.pop(config, None)

# -----------------------------------------------------------------------------
# Индекс сигналов (архив)
# -----------------------------------------------------------------------------

def build_signal_index(folder: str) -> Dict[str, List[str]]:
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    if not os.path.isdir(folder_abs):
        return {}
    signal_index = {}
    print(f"[INFO] Building signal index from {folder_abs}...")
    for filename in os.listdir(folder_abs):
        if not filename.lower().endswith(".csv"):
            continue
        filepath = os.path.join(folder_abs, filename)
        try:
            df_header = pd.read_csv(filepath, nrows=0, encoding="ISO-8859-2", sep=";")
            columns = df_header.columns.tolist()
            signal_columns = [c for c in columns if c not in ["DATE", "TIME", "datetime"]]
            for signal_name in signal_columns:
                signal_index.setdefault(signal_name, []).append(filepath)
        except Exception as e:
            print(f"  ✗ Failed to index {filename}: {e}")
            continue
    return signal_index

def get_folder_state(path: str) -> dict:
    if not os.path.isdir(path):
        return {}
    state = {}
    for name in os.listdir(path):
        if name.lower().endswith(".csv"):
            filepath = os.path.join(path, name)
            state[name] = os.path.getmtime(filepath)
    return state

def load_signal_index(config: str) -> Dict[str, List[str]]:
    """Загружает индекс сигналов для конфигурации, используя кэш в памяти."""
    archive = config_path("signalArchiveFolder", config)
    if not archive:
        return {}
    current_state = get_folder_state(archive)
    cached_entry = SIGNAL_INDEX_CACHE.get(config)
    if cached_entry:
        cached_state, cached_index = cached_entry
        if cached_state == current_state:
            return cached_index
    index = build_signal_index(archive)
    SIGNAL_INDEX_CACHE[config] = (current_state, index)
    return index

def load_signal_data_optimized(signal_names: List[str], config: str) -> Dict[str, pd.DataFrame]:
    archive = config_path("signalArchiveFolder", config)
    if not archive:
        raise RuntimeError("signalArchiveFolder not configured")
    signal_index = load_signal_index(config)
    signal_names_set = set(signal_names)
    files_to_load = set()
    for signal_name in signal_names_set:
        if signal_name in signal_index:
            files_to_load.update(signal_index[signal_name])
    found_signals = {}
    for filepath in files_to_load:
        try:
            df = pd.read_csv(filepath, encoding="ISO-8859-2", sep=";")
            df["TIME"] = df["TIME"].str.replace(",", ".", regex=False)
            df["TIME"] = df["TIME"].str.split(".").str[0]
            combined = df["DATE"] + " " + df["TIME"]
            df["datetime"] = pd.to_datetime(combined, format="%d.%m.%Y %H:%M:%S", errors="coerce")
            df = df.dropna(subset=["datetime"])
            df = df.drop(['DATE', 'TIME'], axis=1)
            df = df.sort_values("datetime")
            available_columns = set(df.columns) & signal_names_set
            for signal_name in available_columns:
                if signal_name not in found_signals:
                    found_signals[signal_name] = df[["datetime", signal_name]].copy()
                    found_signals[signal_name].columns = ["datetime", "value"]
        except Exception as e:
            print(f"[WARN] Failed to read {filepath}: {e}")
            continue
    return found_signals

# -----------------------------------------------------------------------------
# Проекты и зависимости
# -----------------------------------------------------------------------------

def get_project_path(filename: str, config: str) -> str:
    folder = config_path("projectDataFolder", config)
    if not folder:
        raise RuntimeError("projectDataFolder not configured")
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(folder, filename)
    if not path.startswith(folder):
        raise HTTPException(status_code=400, detail="Path traversal attempt")
    return path

def get_storage_path(filename: str, storage: str = "projects", config: str = None) -> str:
    key_map = {
        "projects": "projectDataFolder",
        "templates": "templateDataFolder"
    }
    key = key_map.get(storage)
    if not key:
        raise HTTPException(status_code=400, detail="Unknown storage")
    if storage == "templates":
        # шаблоны общие, не зависят от конфигурации
        base_dir = _abs_folder(key)
    else:
        base_dir = config_path(key, config)
    if not base_dir:
        raise HTTPException(status_code=500, detail=f"{key} not configured")
    os.makedirs(base_dir, exist_ok=True)
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(base_dir, filename)
    if not path.startswith(base_dir):
        raise HTTPException(status_code=400, detail="Path traversal attempt")
    return path

def extract_input_signals_from_project(project_data: Dict) -> List[str]:
    elements = project_data.get("elements", {})
    input_signals = []
    for elem_id, elem_data in elements.items():
        if elem_data.get("type") == "input-signal":
            props = elem_data.get("props", {})
            signal_name = props.get("name")
            if signal_name:
                input_signals.append(signal_name)
    return input_signals

def load_project_by_code(code: str, config: str) -> Optional[Dict]:
    folder = config_path("projectDataFolder", config)
    if not folder or not os.path.isdir(folder):
        return None
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            proj = payload.get("project", {})
            if proj.get("code") == code:
                return {
                    "project": proj,
                    "formula": payload.get("code", ""),
                    "elements": payload.get("elements", {})
                }
        except Exception as e:
            print(f"[WARN] Error reading project {path}: {e}")
            continue
    return None

def is_base_signal(signal_name: str, config: str) -> bool:
    signal_index = load_signal_index(config)
    return signal_name in signal_index

def resolve_signal_dependencies(
    signal_names: List[str],
    config: str,
    visited: set = None,
    resolved: Dict[str, Dict] = None
) -> tuple[set, Dict[str, Dict]]:
    if visited is None:
        visited = set()
    if resolved is None:
        resolved = {}
    base_signals = set()
    for signal_name in signal_names:
        if not signal_name or signal_name in visited:
            continue
        visited.add(signal_name)
        if is_base_signal(signal_name, config):
            base_signals.add(signal_name)
            continue
        project = load_project_by_code(signal_name, config)
        if project is None:
            base_signals.add(signal_name)
            continue
        formula = project.get("formula", "")
        dependencies = extract_input_signals_from_project(project)
        resolved[signal_name] = {
            "formula": formula,
            "dependencies": dependencies
        }
        sub_base, _ = resolve_signal_dependencies(dependencies, config, visited, resolved)
        base_signals.update(sub_base)
    return base_signals, resolved

def topological_sort_signals(synthetic_signals: Dict[str, Dict]) -> List[str]:
    if not synthetic_signals:
        return []
    in_degree = {name: 0 for name in synthetic_signals}
    graph = {name: [] for name in synthetic_signals}
    for name, data in synthetic_signals.items():
        for dep in data.get("dependencies", []):
            if dep == name:
                continue
            if dep in synthetic_signals:
                graph[dep].append(name)
                in_degree[name] += 1
    queue = [name for name, degree in in_degree.items() if degree == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if len(result) != len(synthetic_signals):
        cyclic = [name for name in synthetic_signals if name not in result]
        raise ValueError(f"Циклическая зависимость между сигналами: {cyclic}")
    return result

def upsert_formula_template_from_project(content: Dict[str, Any]) -> None:
    project_meta = content.get("project") or {}
    template_name = project_meta.get("code", "").strip()
    if not template_name:
        return
    elements = content.get("elements", {}) or {}
    input_signals = []
    for elem in elements.values():
        if elem.get("type") == "input-signal":
            signal_name = (elem.get("props") or {}).get("name")
            if signal_name:
                input_signals.append(signal_name)
    seen = set()
    ordered_inputs = []
    for name in input_signals:
        if name not in seen:
            ordered_inputs.append(name)
            seen.add(name)
    args_descriptions = project_meta.get("templateArgs") or {}
    args = {}
    arg_desc_lines = []
    for name in ordered_inputs:
        desc = (args_descriptions.get(name) or "").strip()
        arg_entry = {}
        if desc:
            arg_entry["description"] = desc
            arg_desc_lines.append(f"{name} - {desc}")
        else:
            arg_desc_lines.append(f"{name} -")
        args[name] = arg_entry
    general_description = (project_meta.get("description") or "").strip()
    full_description_parts = []
    if general_description:
        full_description_parts.append(general_description)
    if arg_desc_lines:
        full_description_parts.extend(arg_desc_lines)
    full_description = "; ".join(full_description_parts)
    entry = {
        "name": template_name,
        "args": args,
        "body": content.get("code", ""),
        "description": full_description
    }
    templates_data = load_templates()
    templates = templates_data.get("templates", [])
    found = False
    updated = []
    for tpl in templates:
        if tpl.get("name") == template_name:
            updated.append(entry)
            found = True
        else:
            updated.append(tpl)
    if not found:
        updated.append(entry)
    templates_data["templates"] = updated
    with open(TEMPLATES_PATH, "w", encoding="utf-8") as f:
        json.dump(templates_data, f, ensure_ascii=False, indent=2)
    STATE["templates"] = templates_data
    print(f"[OK] Template '{template_name}' saved/updated in formula_templates.json")

# =============================================================================
# FASTAPI ПРИЛОЖЕНИЕ
# =============================================================================

app = FastAPI(title="Logic Scheme Editor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Хранилище сессий визуализатора
visualize_sessions: Dict[str, Dict[str, Any]] = {}

@app.on_event("startup")
def startup():
    settings = load_settings()
    STATE["settings"] = settings

    # Определяем список конфигураций по подпапкам в projects
    project_base = _abs_folder("projectDataFolder")
    configs = []
    if project_base and os.path.isdir(project_base):
        configs = sorted([
            d for d in os.listdir(project_base)
            if os.path.isdir(os.path.join(project_base, d))
        ])
    STATE["configurations"] = configs

    # Создаём подпапки для каждой конфигурации во всех нужных папках
    for config in configs:
        ensure_config_dirs(config)
        # При необходимости можно прогнать обновление проектов по шаблонам для каждой конфигурации
        proj_dir = config_path("projectDataFolder", config)
        if proj_dir:
            update_projects_if_templates_changed(
                project_dir=proj_dir,
                templates_path=TEMPLATES_PATH
            )

    # Глобальные ресурсы
    STATE["templates"] = load_templates()
    os.makedirs(_abs_folder("templateDataFolder") or "", exist_ok=True)

    print(f"[OK] Configurations found: {configs}")
    print(f"[OK] Loaded templates: {len(STATE['templates'].get('templates', []))}")

# -----------------------------------------------------------------------------
# API: конфигурации
# -----------------------------------------------------------------------------

@app.get("/api/configurations")
def api_configurations():
    """Возвращает список доступных конфигураций."""
    return {"configurations": STATE["configurations"]}

# -----------------------------------------------------------------------------
# API: настройки и сигналы
# -----------------------------------------------------------------------------

@app.get("/api/settings")
def api_settings():
    return STATE["settings"]

@app.get("/api/tables")
def api_tables(q: str = "", limit: int = 50, config: str = Query(...)):
    items = get_tables_for_config(config)
    if not q:
        result_items = items[:limit]
        total = len(items)
    else:
        import re
        escaped = re.escape(q).replace(r"\*", ".*")
        rx = re.compile("^" + escaped + "$", re.IGNORECASE)
        filtered = [t for t in items if rx.match(t["Name"])]
        total = len(filtered)
        result_items = filtered[:max(1, min(limit, 500))]
    return JSONResponse(
        content={"items": result_items, "total": total},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

@app.get("/api/signals")
def api_signals(q: str = "", limit: int = 50, config: str = Query(...)):
    signals = get_signals_for_config(config)
    if not q:
        result = {"items": signals[:limit], "total": len(signals)}
    else:
        import re
        escaped = re.escape(q).replace(r"\*", ".*")
        rx = re.compile("^" + escaped + "$", re.IGNORECASE)
        items = [s for s in signals if rx.match(s["Tagname"])]
        result = {"items": items[:max(1, min(limit, 500))], "total": len(items)}
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

@app.get("/api/table/file/{name}")
def api_table_file(name: str, config: str = Query(...)):
    folder = config_path("tablesFolder", config)
    if not folder:
        raise HTTPException(status_code=500, detail="tablesFolder not configured")
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid table name")
    path = os.path.join(folder, f"{name}.xlsx")
    if not path.startswith(folder):
        raise HTTPException(status_code=400, detail="Path traversal attempt")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Table file not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{name}.xlsx",
        headers={"Cache-Control": "no-cache"}
    )

@app.get("/api/formula-templates")
def api_formula_templates():
    return STATE.get("templates") or {"templates": []}

# -----------------------------------------------------------------------------
# API: проекты
# -----------------------------------------------------------------------------

def collect_projects(directory: str, source_label: str) -> list[dict]:
    items = []
    if not directory or not os.path.isdir(directory):
        return items
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(directory, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        project_meta = payload.get("project", {}) or {}
        items.append({
            "filename": fname,
            "code": project_meta.get("code") or project_meta.get("tagname") or "",
            "description": project_meta.get("description") or "",
            "type": project_meta.get("type") or "",
            "source": source_label
        })
    return items

@app.get("/api/project/list")
def list_projects(config: str = Query(...)):
    def collect(directory_key: str, source_label: str) -> list[dict]:
        folder = config_path(directory_key, config) if directory_key == "projectDataFolder" else _abs_folder(directory_key)
        if not folder or not os.path.isdir(folder):
            return []
        items = []
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(folder, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                continue
            project_meta = payload.get("project", {}) or {}
            try:
                code_len = get_code_length(payload)
            except Exception:
                code_len = 0
            items.append({
                "filename": fname,
                "code": project_meta.get("code") or project_meta.get("tagname") or "",
                "description": project_meta.get("description") or "",
                "type": project_meta.get("type") or "",
                "author": project_meta.get("author") or "",
                "possibleCause": project_meta.get("possibleCause") or "",
                "status": project_meta.get("status") or "draft",
                "statusComment": project_meta.get("statusComment") or "",
                "statusHistory": project_meta.get("statusHistory") or [],
                "lastStatusChangedByAdmin": project_meta.get("lastStatusChangedByAdmin", False),
                "lastModifiedAt": project_meta.get("lastModifiedAt") or "",
                "lastModifiedBy": project_meta.get("lastModifiedBy") or "",
                "source": source_label,
                "codeLength": code_len
            })
        return items

    projects = collect("projectDataFolder", "projects")
    projects.extend(collect("templateDataFolder", "templates"))
    authors = sorted(set(project.get("author") for project in projects if project.get("author")))
    admin_author = STATE["settings"].get("adminAuthor", "")
    return {"projects": projects, "authors": authors, "adminAuthor": admin_author}

@app.post("/api/project/set-author")
async def set_project_author(request: Request, config: str = Query(...)):
    try:
        data = await request.json()
        filename = data.get("filename")
        new_author = data.get("author")
        if not filename or not new_author:
            raise HTTPException(status_code=400, detail="Filename and author are required")
        path = get_storage_path(filename, storage="projects", config=config)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Project not found")
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        if "project" not in content:
            content["project"] = {}
        content["project"]["author"] = new_author
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        invalidate_signals_cache(config)
        return {"status": "ok", "message": f"Author updated for {filename}"}
    except Exception as e:
        print(f"Error updating author: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/project/save")
async def save_project(request: Request, config: str = Query(...)):
    try:
        data = await request.json()
        filename = data.get("filename")
        content = data.get("content")
        target = data.get("target", "projects")
        if not filename or not content:
            raise HTTPException(status_code=400, detail="Filename and content are required")
        project_meta = content.get("project") or {}
        project_meta.setdefault("status", "draft")
        project_meta.setdefault("statusComment", "")
        project_meta.setdefault("statusHistory", [])
        project_type = project_meta.get("type", "parameter")
        if project_type == "template":
            target = "templates"
        path = get_storage_path(filename, storage=target, config=config if target == "projects" else None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        if project_type == "template":
            upsert_formula_template_from_project(content)
        invalidate_signals_cache(config)
        return {"status": "ok", "message": f"Project saved to {filename}"}
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error saving project: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during save")

@app.get("/api/project/load/{filename}")
def load_project(filename: str, source: str = "projects", config: str = Query(...)):
    try:
        path = get_storage_path(filename, storage=source, config=config if source == "projects" else None)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Project not found")
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        return content
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error loading project: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during load")

@app.post("/api/project/export-selected")
async def api_export_selected_projects(payload: dict = Body(...), config: str = Query(...)):
    try:
        filenames = payload.get("filenames", [])
        project_dir = config_path("projectDataFolder", config)
        if not project_dir:
            raise HTTPException(status_code=500, detail="projectDataFolder not configured")
        param_format = payload.get("param_format", "excel")
        result = export_selected_projects(filenames, project_dir, param_format)
        return StreamingResponse(
            BytesIO(result["content"]),
            media_type=result["media_type"],
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(result['filename'])}",
                "Cache-Control": "no-cache, no-store, must-revalidate"
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"Error exporting selected projects: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during export")

@app.get("/api/project/consumers/{code}")
def api_project_consumers(code: str, config: str = Query(...)):
    results = []
    # проекты в текущей конфигурации
    folder = config_path("projectDataFolder", config)
    if folder and os.path.isdir(folder):
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(folder, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                proj_meta = (payload.get("project") or {})
                proj_code = (proj_meta.get("code") or proj_meta.get("tagname") or "").strip()
                elements = payload.get("elements") or {}
                inputs = []
                for el in elements.values():
                    if el and el.get("type") == "input-signal":
                        name = ((el.get("props") or {}).get("name") or "").strip()
                        if name:
                            inputs.append(name)
                if code in set(inputs):
                    results.append({
                        "code": proj_code or "(без кода)",
                        "filename": fname,
                        "source": "projects",
                        "description": proj_meta.get("description", ""),
                        "type": proj_meta.get("type", "")
                    })
            except Exception as e:
                print(f"[WARN] Failed to inspect project {path}: {e}")
                continue
    # шаблоны не привязаны к конфигурации, их тоже можно проверять
    tmpl_folder = _abs_folder("templateDataFolder")
    if tmpl_folder and os.path.isdir(tmpl_folder):
        for fname in sorted(os.listdir(tmpl_folder)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(tmpl_folder, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                proj_meta = (payload.get("project") or {})
                proj_code = (proj_meta.get("code") or proj_meta.get("tagname") or "").strip()
                elements = payload.get("elements") or {}
                inputs = []
                for el in elements.values():
                    if el and el.get("type") == "input-signal":
                        name = ((el.get("props") or {}).get("name") or "").strip()
                        if name:
                            inputs.append(name)
                if code in set(inputs):
                    results.append({
                        "code": proj_code or "(без кода)",
                        "filename": fname,
                        "source": "templates",
                        "description": proj_meta.get("description", ""),
                        "type": proj_meta.get("type", "")
                    })
            except Exception as e:
                print(f"[WARN] Failed to inspect template {path}: {e}")
                continue
    results.sort(key=lambda x: x.get("code", "").lower())
    return {"consumers": results}

@app.post("/api/tags/list")
async def get_tags(config: str = Query(...)):
    # тэги глобальные, пока не зависят от конфигурации
    return load_tags_data()

@app.post("/api/tags/create")
async def create_tag(payload: dict = Body(...), config: str = Query(...)):
    user = payload.get("user")
    name = payload.get("name")
    color = payload.get("color")
    admin_author = STATE["settings"].get("adminAuthor", "")
    if user != admin_author:
        raise HTTPException(status_code=403, detail="Только админ может создавать теги")
    if not name or not color:
        raise HTTPException(status_code=400, detail="Нужно имя и цвет")
    data = load_tags_data()
    if any(t['name'] == name for t in data['tags']):
        raise HTTPException(status_code=400, detail="Тег с таким именем уже есть")
    new_tag = {"id": f"tag_{int(datetime.utcnow().timestamp())}", "name": name, "color": color}
    data["tags"].append(new_tag)
    save_tags_data(data)
    return new_tag

@app.post("/api/tags/assign")
async def assign_tags(payload: dict = Body(...)):   # убрали config
    filename = payload.get("filename")
    tag_ids = payload.get("tagIds", [])
    if not filename:
        raise HTTPException(status_code=400, detail="Нет имени файла")
    data = load_tags_data()
    data["assignments"][filename] = tag_ids
    save_tags_data(data)
    return {"status": "success"}

@app.post("/api/project/status/set")
async def set_project_status(payload: dict = Body(...), config: str = Query(...)):
    try:
        filename = payload.get("filename")
        new_status = payload.get("status")
        comment = payload.get("comment", "")
        user = payload.get("user", "")
        if not filename or new_status not in ("draft", "ready"):
            raise HTTPException(status_code=400, detail="Invalid filename or status")
        admin_author = STATE["settings"].get("adminAuthor", "")
        if user != admin_author:
            raise HTTPException(status_code=403, detail="Only admin can change status")
        file_path = get_storage_path(filename, storage="projects", config=config)
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail=f"Project file not found: {filename}")
        with open(file_path, "r", encoding="utf-8") as f:
            content = json.load(f)
        project_meta = content.setdefault("project", {})
        project_meta["status"] = new_status
        project_meta["lastStatusChangedByAdmin"] = True
        history = project_meta.setdefault("statusHistory", [])
        history.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "author": user,
            "action": "forced_draft" if new_status == "draft" else "forced_ready",
            "comment": comment.strip()
        })
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        return {"status": "success", "message": "Project status updated"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating project status: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.delete("/api/project/delete/{filename}")
async def delete_project(filename: str, request: Request, config: str = Query(...)):
    try:
        data = await request.json()
        current_user = data.get("user", "")
        if not current_user:
            raise HTTPException(status_code=400, detail="User not provided")
        path = get_storage_path(filename, storage="projects", config=config)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Project not found")
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        project_meta = content.get("project", {})
        project_author = project_meta.get("author", "")
        admin_author = STATE["settings"].get("adminAuthor", "")
        if project_author != current_user and current_user != admin_author:
            raise HTTPException(status_code=403, detail="Only the author or admin can delete this project")
        # Перемещаем в deleted_projects/<config>
        deleted_base = _abs_folder("deletedFolder") or os.path.join(BASE_DIR, "deleted_projects")
        deleted_dir = os.path.join(deleted_base, config)
        os.makedirs(deleted_dir, exist_ok=True)
        deleted_path = os.path.join(deleted_dir, filename)
        shutil.copy2(path, deleted_path)
        os.remove(path)
        invalidate_signals_cache(config)
        return {"status": "ok", "message": f"Project '{filename}' successfully moved to deleted_projects"}
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error deleting project: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during deletion")

# -----------------------------------------------------------------------------
# API: данные сигналов и зависимости
# -----------------------------------------------------------------------------

@app.post("/api/signal-data")
async def api_signal_data(request: Request, config: str = Query(...)):
    try:
        data = await request.json()
        signal_names = data.get("signal_names", [])
        output_format = data.get("format", "parquet")
        if not signal_names:
            raise HTTPException(status_code=400, detail="signal_names is required")
        signals_data = load_signal_data_optimized(signal_names, config)
        response = {
            "found": list(signals_data.keys()),
            "not_found": [s for s in signal_names if s not in signals_data],
            "format": output_format
        }
        if not signals_data:
            raise HTTPException(status_code=404, detail="No signals found")
        if output_format == "parquet":
            return await _export_parquet(signals_data, response)
        else:
            return await _export_json(signals_data, response)
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error in api_signal_data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def _export_parquet(signals_data: Dict[str, pd.DataFrame], meta: Dict):
    try:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            tmp_path = tmp.name
        rows = []
        for signal_name, df in signals_data.items():
            df_copy = df.copy()
            df_copy["signal_name"] = signal_name
            rows.append(df_copy)
        combined = pd.concat(rows, ignore_index=True)
        combined.to_parquet(tmp_path, compression='snappy', index=False)
        file_size = os.path.getsize(tmp_path)
        print(f"[OK] Exported {len(signals_data)} signals to Parquet: {file_size / 1024 / 1024:.2f} MB")
        return FileResponse(
            tmp_path,
            media_type="application/octet-stream",
            filename="signal_data.parquet",
            headers={"X-Signal-Meta": json.dumps(meta)}
        )
    except Exception as e:
        print(f"[ERROR] Parquet export failed: {e}")
        raise

async def _export_json(signals_data: Dict[str, pd.DataFrame], meta: Dict):
    try:
        data_dict = {}
        for signal_name, df in signals_data.items():
            df_copy = df.copy()
            df_copy["datetime"] = df_copy["datetime"].astype(str)
            data_dict[signal_name] = df_copy.to_dict(orient="records")
        response_data = {**meta, "data": data_dict}
        return JSONResponse(response_data)
    except Exception as e:
        print(f"[ERROR] JSON export failed: {e}")
        raise

@app.post("/api/resolve-signals")
async def api_resolve_signals(request: Request, config: str = Query(...)):
    try:
        data = await request.json()
        signal_names = data.get("signals", [])
        base_signals, synthetic_signals = resolve_signal_dependencies(signal_names, config)
        computation_order = topological_sort_signals(synthetic_signals)
        return {
            "base_signals": list(base_signals),
            "synthetic_signals": synthetic_signals,
            "computation_order": computation_order
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"[ERROR] resolve-signals failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------------------------------------------------
# API: визуализатор (без изменений, не зависит от файловой структуры)
# -----------------------------------------------------------------------------

@app.post("/api/visualize/session")
async def create_visualize_session(request: Request):
    try:
        data = await request.json()
        signals = data.get("signals", [])
        tables = data.get("tables", [])
        code = data.get("code", "")
        visualizer_state = data.get("visualizer_state")
        if not isinstance(signals, list):
            raise HTTPException(status_code=400, detail="signals must be a list")
        token = uuid.uuid4().hex
        visualize_sessions[token] = {
            "signals": signals,
            "tables": tables,
            "code": code,
            "visualizer_state": visualizer_state
        }
        return {"token": token}
    except Exception as e:
        print(f"[ERROR] create_visualize_session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/visualize/session/{token}")
async def get_visualize_session(token: str):
    session = visualize_sessions.get(token)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "signals": session.get("signals", []),
        "tables": session.get("tables", []),
        "code": session.get("code", ""),
        "visualizer_state": session.get("visualizer_state")
    }

@app.post("/api/visualize/save-state")
async def save_visualizer_state(request: VisualizerStateRequest) -> VisualizerStateResponse:
    try:
        if request.session_token in visualize_sessions:
            visualize_sessions[request.session_token]["visualizer_state"] = request.state
        else:
            visualize_sessions[request.session_token] = {
                "signals": [],
                "code": "",
                "visualizer_state": request.state
            }
        return VisualizerStateResponse(success=True, state=request.state, message="Состояние сохранено")
    except Exception as e:
        return VisualizerStateResponse(success=False, message=f"Ошибка сохранения: {str(e)}")

@app.get("/api/visualize/get-state/{session_token}")
async def get_visualizer_state(session_token: str) -> VisualizerStateResponse:
    session = visualize_sessions.get(session_token)
    if session is None:
        return VisualizerStateResponse(success=False, message="Сессия не найдена")
    state = session.get("visualizer_state")
    if state is None:
        return VisualizerStateResponse(success=False, message="Состояние визуализатора не сохранено")
    return VisualizerStateResponse(success=True, state=state)

@app.get("/api/tags/list")
async def get_tags_list():
    return load_tags_data()


@app.post("/api/check-syntax")
async def check_syntax(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Поддерживаются только файлы Excel (.xlsx, .xls)")

    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения Excel: {e}")

    code_col = next((c for c in df.columns if c.lower().strip() in ['код', 'code', 'формула']), None)
    signals_col = next((c for c in df.columns if c.lower().strip() in ['используемые сигналы', 'используемые параметры', 'used signals']), None)

    if not code_col:
        raise HTTPException(status_code=400, detail="В файле не найден столбец 'Код'")
    if not signals_col:
        raise HTTPException(status_code=400, detail="В файле не найден столбец 'Используемые сигналы'")

    remarks = []
    for idx, row in df.iterrows():
        code = str(row[code_col]) if pd.notna(row[code_col]) else ""
        signals_str = str(row[signals_col]) if pd.notna(row[signals_col]) else ""
        input_signals = [s.strip() for s in re.split(r'[;,]', signals_str) if s.strip()]

        row_remarks = []
        row_num = idx + 2

        if not code:
            row_remarks.append("Пустой код")
            remarks.append({"row": row_num, "remarks": row_remarks})
            continue

        # 1. Скобки и кавычки
        if code.count('(') != code.count(')'):
            row_remarks.append("Не совпадает количество открывающих и закрывающих скобок")
        if code.count("'") % 2 != 0:
            row_remarks.append("Нечётное количество одинарных кавычек")
        if code.count('"') % 2 != 0:
            row_remarks.append("Нечётное количество двойных кавычек")

        # 2. Недопустимые символы
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.,;:!?+-*/%<>=&|^()[]{}§'\"\n\r\t ")
        invalid_chars = set(code) - allowed_chars
        if invalid_chars:
            row_remarks.append(f"Недопустимые символы: {', '.join(repr(c) for c in invalid_chars)}")

        # 3. Унарный минус перед идентификатором (не перед числом и не перед скобкой)
        if re.search(r'(?<![a-zA-Z0-9_§])\s*-\s*(?=[A-Za-z_§])', code):
            row_remarks.append("Обнаружен унарный минус перед сигналом. При необходимости замените на '-1*'")

        # 4. Логические операторы
        for op in ['AND', 'OR', 'NOT']:
            if re.search(rf'\b{op}\b', code):
                row_remarks.append(f"Логический оператор {op} – рекомендуется заменить на {'&&' if op=='AND' else '||' if op=='OR' else '!'}")

        # 5. Аргументы HISTORY*/PREV в кавычках
        history_funcs = ['HISTORYAVG','HISTORYCOUNT','HISTORYSUM','HISTORYMAX','HISTORYMIN','HISTORYDIFF','HISTORYGRADIENT','PREV']
        for fn in history_funcs:
            pattern = re.compile(rf'\b{fn}\s*\(\s*([\'"]?)(?P<arg>[^\'",]+)\1\s*[,)]', re.IGNORECASE)
            for m in pattern.finditer(code):
                arg = m.group('arg').strip()
                if arg and arg[0].isdigit() and (m.group(1) is None or m.group(1) == ''):
                    row_remarks.append(f"{fn}: аргумент '{arg}' должен быть в кавычках (начинается с цифры)")

        # 5b. Первый аргумент INTERPOLATE/GETPOINT – проверка наличия в используемых сигналах
        for fn in ['INTERPOLATE', 'GETPOINT']:
            pattern = re.compile(rf'\b{fn}\s*\(\s*([\'"]?)(?P<arg>[^\'",]+)\1\s*[,)]', re.IGNORECASE)
            for m in pattern.finditer(code):
                arg = m.group('arg').strip()
                if arg and arg not in input_signals:
                    row_remarks.append(f"Таблицы '{arg}' нет в используемых параметрах")

        # 6. Неизвестные функции/опечатки (исключаем токены внутри строк)
        known_functions = {'WHEN','ABS','EXP','POW','LOG','LOG10','MIN','MAX','AVG','MED','ROUND',
                          'GETPOINT','INTERPOLATE','PREV','HISTORYAVG','HISTORYCOUNT','HISTORYSUM',
                          'HISTORYMAX','HISTORYMIN','HISTORYDIFF','HISTORYGRADIENT'}

        string_literals = re.findall(r'[\'"].*?[\'"]', code)

        for token in re.findall(r'[A-Z][A-Z0-9_]*', code):
            if token in known_functions or token in ('AND','OR','NOT','X','Y'):
                continue
            if token in input_signals:
                continue
            if re.match(r'P\d', token):
                continue
            if any(token in s for s in string_literals):
                continue
            if any(token in sig.replace('§', '_') for sig in input_signals):
                continue
            row_remarks.append(f"Возможно, неизвестная функция или опечатка: '{token}'")

        # 7. Проверка наличия сигналов
        for sig in input_signals:
            sig_underscored = sig.replace('§', '_')
            found = (sig in code) or (sig_underscored in code)
            if not found and sig[0].isdigit():
                found = ('P' + sig in code) or ('P' + sig_underscored in code)
            if not found:
                row_remarks.append(f"Сигнал '{sig}' не найден в выражении")

        # 8. Проверка префикса P для сигналов, начинающихся с цифры
        for sig in input_signals:
            if not sig[0].isdigit():
                continue
            sig_u = sig.replace('§', '_')
            # Ищем в коде упоминание сигнала без префикса P и не внутри кавычек (внутри функций оборачивается в кавычки)
            # Для этого удалим из кода все строковые литералы (в кавычках) и будем искать чистый идентификатор
            code_without_strings = re.sub(r'[\'"].*?[\'"]', '', code)
            # Составляем регулярку: граница слова, само имя сигнала (с учётом возможного _ вместо §), граница слова
            # Но нужно учесть, что имя сигнала может содержать цифры, буквы, §, _. Используем re.escape
            pattern_sig = re.compile(rf'(?<![A-Za-z0-9_]){re.escape(sig_u)}(?![A-Za-z0-9_])')
            if pattern_sig.search(code_without_strings):
                row_remarks.append(f"Сигнал '{sig}' начинается с цифры – необходимо добавить префикс P")

        if row_remarks:
            remarks.append({"row": row_num, "remarks": row_remarks})

    return remarks

@app.post("/api/export-remarks")
async def export_remarks(data: List[Dict[str, Any]] = Body(...)):
    """Принимает массив замечаний и возвращает Excel-файл."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Замечания"
    ws.append(["Строка", "Замечания"])
    for item in data:
        remarks_text = "\n".join(item.get("remarks", []))
        ws.append([item.get("row", ""), remarks_text])
    # Автоширина и перенос текста
    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            try:
                if cell.value:
                    lines = str(cell.value).split('\n')
                    max_line = max(len(line) for line in lines)
                    if max_line > max_length:
                        max_length = max_line
            except:
                pass
        adjusted_width = min(max_length + 2, 80)
        ws.column_dimensions[col_letter].width = adjusted_width
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=syntax_remarks.xlsx"}
    )



# -----------------------------------------------------------------------------
# Статические файлы (фронтенд)
# -----------------------------------------------------------------------------

WEB_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "web"))
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
print(f"[DEBUG] WEB_DIR: {WEB_DIR}")
for f in os.listdir(WEB_DIR):
    print(f"  {repr(f)}  | exists: {os.path.exists(os.path.join(WEB_DIR, f))}")