# main.py — чистая версия с поддержкой состояния визуализатора

import os
import json
import uuid
import pickle
import tempfile
from typing import Dict, List, Any, Optional
from io import BytesIO
from update_projects import update_projects_if_templates_changed

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
TEMPLATES_PATH = os.path.join(BASE_DIR, "formula_templates.json")
SIGNAL_INDEX_PATH = os.path.join(BASE_DIR, ".signal_index.pkl")


# =============================================================================
# PYDANTIC МОДЕЛИ
# =============================================================================

class VisualizerStateRequest(BaseModel):
    """Запрос на сохранение состояния визуализатора"""
    session_token: str
    state: Dict[str, Any]


class VisualizerStateResponse(BaseModel):
    """Ответ с состоянием визуализатора"""
    success: bool
    state: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class VisualizeSessionRequest(BaseModel):
    """Запрос на создание сессии визуализации"""
    signals: List[str]
    code: str = ""
    visualizer_state: Optional[Dict[str, Any]] = None


# =============================================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# =============================================================================

STATE = {
    "settings": None,
    "signals": None,
    "signal_index": None,
    "templates": None
}

# Хранилище сессий визуализатора (в памяти)
visualize_sessions: Dict[str, Dict[str, Any]] = {}


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ — ЗАГРУЗКА НАСТРОЕК
# =============================================================================

def load_settings() -> Dict:
    """Загружает настройки из settings.json"""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_templates() -> Dict:
    """Загружает шаблоны формул"""
    if not os.path.exists(TEMPLATES_PATH):
        return {"templates": []}
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ — СИГНАЛЫ
# =============================================================================

def load_signals_from_folder(folder: str) -> List[Dict]:
    """Загружает описания сигналов из CSV файлов"""
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    if not os.path.isdir(folder_abs):
        raise FileNotFoundError(f"signalDataFolder not found: {folder_abs}")

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
    """Загружает сигналы из проектов (синтетические сигналы)"""
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


def refresh_signals_cache():
    """Обновляет кэш сигналов (базовые + из проектов)"""
    settings = STATE["settings"] or {}
    base_folder = settings.get("signalDataFolder")
    proj_folder = settings.get("projectDataFolder")

    base = load_signals_from_folder(base_folder) if base_folder else []
    proj = load_project_signals(proj_folder) if proj_folder else []

    merged = {}
    for s in base:
        merged[s["Tagname"]] = s
    for s in proj:
        merged[s["Tagname"]] = s  # проекты перекрывают CSV

    out = list(merged.values())
    out.sort(key=lambda x: x["Tagname"])
    STATE["signals"] = out


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ — ИНДЕКС СИГНАЛОВ
# =============================================================================

def build_signal_index(folder: str) -> Dict[str, List[str]]:
    """Строит индекс: signal_name -> list of files"""
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    
    if not os.path.isdir(folder_abs):
        raise FileNotFoundError(f"Signal data folder not found: {folder_abs}")
    
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
                if signal_name not in signal_index:
                    signal_index[signal_name] = []
                signal_index[signal_name].append(filepath)
            
            print(f"  ✓ {filename}: {len(signal_columns)} signals")
        except Exception as e:
            print(f"  ✗ Failed to index {filename}: {e}")
            continue
    
    print(f"[OK] Total unique signals indexed: {len(signal_index)}")
    return signal_index


def load_signal_index(folder: str) -> Dict[str, List[str]]:
    """Загружает индекс из кэша или перестраивает"""
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    
    def get_folder_state(path: str) -> dict:
        if not os.path.isdir(path):
            return {}
        state = {}
        for name in os.listdir(path):
            if name.lower().endswith(".csv"):
                filepath = os.path.join(path, name)
                state[name] = os.path.getmtime(filepath)
        return state
    
    current_state = get_folder_state(folder_abs)
    
    # Пробуем загрузить кэш
    if os.path.exists(SIGNAL_INDEX_PATH):
        try:
            with open(SIGNAL_INDEX_PATH, "rb") as f:
                cached_data = pickle.load(f)
            
            if isinstance(cached_data, dict) and "_folder_state" in cached_data:
                cached_state = cached_data["_folder_state"]
                cached_index = cached_data["index"]
                
                if cached_state == current_state:
                    print(f"[OK] Signal index loaded from cache ({len(cached_index)} signals)")
                    return cached_index
                else:
                    print(f"[INFO] CSV files changed, rebuilding index...")
            else:
                print(f"[INFO] Old cache format, rebuilding index...")
        except Exception as e:
            print(f"[WARN] Failed to load cached index: {e}")
    
    # Перестраиваем индекс
    index = build_signal_index(folder)
    
    # Сохраняем с метаданными
    try:
        cache_data = {"index": index, "_folder_state": current_state}
        with open(SIGNAL_INDEX_PATH, "wb") as f:
            pickle.dump(cache_data, f)
        print(f"[OK] Signal index cached with folder state")
    except Exception as e:
        print(f"[WARN] Failed to cache signal index: {e}")
    
    return index


def load_signal_data_optimized(signal_names: List[str], folder: str) -> Dict[str, pd.DataFrame]:
    """Загружает только нужные сигналы из только нужных файлов"""
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    
    signal_index = STATE.get("signal_index", {})
    if not signal_index:
        raise RuntimeError("Signal index not initialized")
    
    signal_names_set = set(signal_names)
    found_signals = {}
    files_to_load = set()
    
    for signal_name in signal_names_set:
        if signal_name in signal_index:
            files_to_load.update(signal_index[signal_name])
    
    print(f"[INFO] Loading {len(signal_names_set)} signals from {len(files_to_load)} files")
    
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


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ — ПРОЕКТЫ И ЗАВИСИМОСТИ
# =============================================================================

def get_project_path(filename: str) -> str:
    """Возвращает абсолютный путь к файлу проекта"""
    folder = STATE["settings"].get("projectDataFolder")
    if not folder:
        raise RuntimeError("projectDataFolder not configured")
    
    project_dir = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = os.path.join(project_dir, filename)
    if not path.startswith(project_dir):
        raise HTTPException(status_code=400, detail="Path traversal attempt")
         
    return path


def extract_input_signals_from_project(project_data: Dict) -> List[str]:
    """Извлекает имена входных сигналов из данных проекта"""
    elements = project_data.get("elements", {})
    input_signals = []
    
    for elem_id, elem_data in elements.items():
        if elem_data.get("type") == "input-signal":
            props = elem_data.get("props", {})
            signal_name = props.get("name")
            if signal_name:
                input_signals.append(signal_name)
    
    return input_signals


def load_project_by_code(code: str) -> Dict | None:
    """Загружает проект по его коду (Tagname)"""
    folder = STATE["settings"].get("projectDataFolder")
    if not folder:
        return None
    
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    if not os.path.isdir(folder_abs):
        return None
    
    for name in os.listdir(folder_abs):
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder_abs, name)
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


def is_base_signal(signal_name: str) -> bool:
    """Проверяет, есть ли сигнал в архиве (базовый сигнал с данными)"""
    signal_index = STATE.get("signal_index", {})
    return signal_name in signal_index


def resolve_signal_dependencies(
    signal_names: List[str],
    visited: set = None,
    resolved: Dict[str, Dict] = None
) -> tuple[set, Dict[str, Dict]]:
    """Рекурсивно разворачивает зависимости сигналов"""
    if visited is None:
        visited = set()
    if resolved is None:
        resolved = {}
    
    base_signals = set()
    
    for signal_name in signal_names:
        if not signal_name or signal_name in visited:
            continue
        visited.add(signal_name)
        
        if is_base_signal(signal_name):
            base_signals.add(signal_name)
            continue
        
        project = load_project_by_code(signal_name)
        if project is None:
            base_signals.add(signal_name)
            print(f"[WARN] Signal '{signal_name}' not found in archive or projects")
            continue
        
        formula = project.get("formula", "")
        dependencies = extract_input_signals_from_project(project)
        
        print(f"[INFO] Synthetic signal '{signal_name}' depends on: {dependencies}")
        
        resolved[signal_name] = {
            "formula": formula,
            "dependencies": dependencies
        }
        
        sub_base, _ = resolve_signal_dependencies(dependencies, visited, resolved)
        base_signals.update(sub_base)
    
    return base_signals, resolved


def topological_sort_signals(synthetic_signals: Dict[str, Dict]) -> List[str]:
    """Топологическая сортировка синтетических сигналов"""
    if not synthetic_signals:
        return []
    
    in_degree = {name: 0 for name in synthetic_signals}
    graph = {name: [] for name in synthetic_signals}
    
    for name, data in synthetic_signals.items():
        for dep in data.get("dependencies", []):
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


@app.on_event("startup")
def startup():
    """Инициализация при запуске"""
    settings = load_settings()
    STATE["settings"] = settings

    project_dir = settings.get("projectDataFolder")
    if project_dir and not os.path.isabs(project_dir):
        project_dir = os.path.normpath(os.path.join(BASE_DIR, project_dir))

    update_projects_if_templates_changed(
        project_dir=project_dir,
        templates_path=TEMPLATES_PATH
    )
    
    folder = settings.get("signalDataFolder")
    if not folder:
        raise RuntimeError("settings.json: signalDataFolder is required")
    
    refresh_signals_cache()
    STATE["templates"] = load_templates()
    STATE["signal_index"] = load_signal_index(settings.get("signalArchiveFolder"))

    print(f"[OK] Loaded signals: {len(STATE['signals'])}")
    print(f"[OK] Signal index has {len(STATE['signal_index'])} unique signals")
    print(f"[OK] Loaded templates: {len(STATE['templates'].get('templates', []))}")


# =============================================================================
# API — НАСТРОЙКИ И СИГНАЛЫ
# =============================================================================

@app.get("/api/settings")
def api_settings():
    """Возвращает настройки приложения"""
    return STATE["settings"]


@app.get("/api/signals")
def api_signals(q: str = "", limit: int = 50):
    """Поиск сигналов по маске (* — wildcard)"""
    signals = STATE["signals"] or []
    
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
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


@app.get("/api/formula-templates")
def api_formula_templates():
    """Возвращает шаблоны формул"""
    return STATE.get("templates") or {"templates": []}


# =============================================================================
# API — ПРОЕКТЫ
# =============================================================================

@app.get("/api/project/list")
def list_projects():
    """Список всех проектов"""
    folder = STATE["settings"].get("projectDataFolder")
    if not folder:
        raise HTTPException(status_code=500, detail="Project folder not configured")

    project_dir = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    os.makedirs(project_dir, exist_ok=True)

    projects = []
    for fname in sorted(os.listdir(project_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(project_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        project_meta = payload.get("project", {})
        projects.append({
            "filename": fname,
            "code": project_meta.get("code") or project_meta.get("tagname") or "",
            "description": project_meta.get("description") or "",
            "type": project_meta.get("type") or ""
        })
    return {"projects": projects}


@app.post("/api/project/save")
async def save_project(request: Request):
    """Сохраняет проект"""
    try:
        data = await request.json()
        filename = data.get("filename")
        content = data.get("content")

        if not filename or not content:
            raise HTTPException(status_code=400, detail="Filename and content are required")
        
        path = get_project_path(filename)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
        
        # Обновляем кэш сигналов
        refresh_signals_cache()
        
        print(f"[OK] Project saved: {filename}, signals cache refreshed: {len(STATE['signals'])} signals")
        return {"status": "ok", "message": f"Project saved to {filename}"}
        
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error saving project: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during save")


@app.get("/api/project/load/{filename}")
def load_project(filename: str):
    """Загружает проект"""
    try:
        path = get_project_path(filename)
        
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


# =============================================================================
# API — ДАННЫЕ СИГНАЛОВ
# =============================================================================

@app.post("/api/signal-data")
async def api_signal_data(request: Request):
    """Загружает данные сигналов из архива"""
    try:
        data = await request.json()
        signal_names = data.get("signal_names", [])
        output_format = data.get("format", "parquet")
        
        if not signal_names:
            raise HTTPException(status_code=400, detail="signal_names is required")
        
        folder = STATE["settings"].get("signalArchiveFolder")
        if not folder:
            raise HTTPException(status_code=500, detail="signalArchiveFolder not configured")
        
        signals_data = load_signal_data_optimized(signal_names, folder)
        
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
    """Экспортирует данные в Parquet"""
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
    """Экспортирует данные в JSON"""
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
async def api_resolve_signals(request: Request):
    """Разворачивает зависимости сигналов (матрёшку)"""
    try:
        data = await request.json()
        signal_names = data.get("signals", [])
        
        print(f"[INFO] Resolving dependencies for signals: {signal_names}")
        
        base_signals, synthetic_signals = resolve_signal_dependencies(signal_names)
        computation_order = topological_sort_signals(synthetic_signals)
        
        print(f"[INFO] Base signals: {base_signals}")
        print(f"[INFO] Synthetic signals: {list(synthetic_signals.keys())}")
        print(f"[INFO] Computation order: {computation_order}")
        
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


# =============================================================================
# API — ВИЗУАЛИЗАТОР
# =============================================================================

@app.post("/api/visualize/session")
async def create_visualize_session(request: Request):
    """Создаёт сессию визуализации"""
    try:
        data = await request.json()
        signals = data.get("signals", [])
        code = data.get("code", "")
        visualizer_state = data.get("visualizer_state")
        
        if not isinstance(signals, list):
            raise HTTPException(status_code=400, detail="signals must be a list")
        
        token = uuid.uuid4().hex
        
        visualize_sessions[token] = {
            "signals": signals,
            "code": code,
            "visualizer_state": visualizer_state
        }
        
        print(f"[OK] Created visualize session: {token}, signals: {len(signals)}, has_state: {visualizer_state is not None}")
        
        return {"token": token}
    
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"[ERROR] create_visualize_session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/visualize/session/{token}")
async def get_visualize_session(token: str):
    """Возвращает данные сессии визуализации"""
    session = visualize_sessions.get(token)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "signals": session.get("signals", []),
        "code": session.get("code", ""),
        "visualizer_state": session.get("visualizer_state")
    }


@app.post("/api/visualize/save-state")
async def save_visualizer_state(request: VisualizerStateRequest) -> VisualizerStateResponse:
    """Сохраняет состояние визуализатора (вызывается из Streamlit)"""
    try:
        # Сохраняем состояние в сессию
        if request.session_token in visualize_sessions:
            visualize_sessions[request.session_token]["visualizer_state"] = request.state
        else:
            # Создаём новую запись если сессии нет
            visualize_sessions[request.session_token] = {
                "signals": [],
                "code": "",
                "visualizer_state": request.state
            }
        
        print(f"[OK] Saved visualizer state for session: {request.session_token}")
        
        return VisualizerStateResponse(
            success=True,
            state=request.state,
            message="Состояние сохранено"
        )
    except Exception as e:
        print(f"[ERROR] save_visualizer_state failed: {e}")
        return VisualizerStateResponse(
            success=False,
            message=f"Ошибка сохранения: {str(e)}"
        )


@app.get("/api/visualize/get-state/{session_token}")
async def get_visualizer_state(session_token: str) -> VisualizerStateResponse:
    """Возвращает состояние визуализатора (вызывается из редактора)"""
    session = visualize_sessions.get(session_token)
    
    if session is None:
        return VisualizerStateResponse(
            success=False,
            message="Сессия не найдена"
        )
    
    state = session.get("visualizer_state")
    
    if state is None:
        return VisualizerStateResponse(
            success=False,
            message="Состояние визуализатора не сохранено"
        )
    
    return VisualizerStateResponse(
        success=True,
        state=state
    )


# =============================================================================
# СТАТИЧЕСКИЕ ФАЙЛЫ (ФРОНТЕНД)
# =============================================================================

WEB_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "web"))
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")