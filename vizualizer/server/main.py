import os
import json
from typing import Dict, List
import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uuid
import pickle
from fastapi.middleware.cors import CORSMiddleware

import tempfile
from io import BytesIO

import pickle  # <-- отсутствовал






BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
TEMPLATES_PATH = os.path.join(BASE_DIR, "formula_templates.json")
SIGNAL_INDEX_PATH = os.path.join(BASE_DIR, ".signal_index.pkl")

def load_templates() -> Dict:
    if not os.path.exists(TEMPLATES_PATH):
        return {"templates": []}
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_settings() -> Dict:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_signals_from_folder(folder: str) -> List[Dict]:
    # folder может быть относительным
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    if not os.path.isdir(folder_abs):
        raise FileNotFoundError(f"signalDataFolder not found: {folder_abs}")

    signals_map = {}  # Tagname -> Description (последний wins)
    for name in os.listdir(folder_abs):
        if not name.lower().endswith(".csv"):
            continue
        path = os.path.join(folder_abs, name)
        try:
            df = pd.read_csv(path, sep=';')[['Tagname', 'Description', 'Engineering Unit']]
            df = df.dropna(subset=['Tagname'])
            for _, row in df.iterrows():
                tag = str(row['Tagname']).strip()
                desc = "" if pd.isna(row['Description']) else str(row['Description']).strip()
                unit = "" if pd.isna(row['Engineering Unit']) else str(row['Engineering Unit']).strip()
                desc = ", ".join([desc, unit])
                if tag:
                    signals_map[tag] = desc
        except Exception as e:
            # пропускаем "плохие" csv, но можно логировать
            print(f"[WARN] failed to read {path}: {e}")

    # в список
    out = [{'Tagname': k, 'Description': v} for k, v in signals_map.items()]
    out.sort(key=lambda x: x['Tagname'])
    return out



app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Кэш сигналов в памяти
STATE = {
    "settings": None,
    "signals": None,
    "signal_index": None
}


def build_signal_index(folder: str) -> Dict[str, List[str]]:
    """
    При запуске: проходим по всем CSV файлам и создаем индекс
    signal_name -> list of files where it's present
    """
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(
        os.path.join(BASE_DIR, folder)
    )
    
    if not os.path.isdir(folder_abs):
        raise FileNotFoundError(f"Signal data folder not found: {folder_abs}")
    
    signal_index = {}
    
    print(f"[INFO] Building signal index from {folder_abs}...")
    
    for filename in os.listdir(folder_abs):
        if not filename.lower().endswith(".csv"):
            continue
        
        filepath = os.path.join(folder_abs, filename)
        
        try:
            # Читаем только первую строку (заголовок)
            df_header = pd.read_csv(
                filepath,
                nrows=0,  # Читаем только заголовок
                encoding="ISO-8859-2",
                sep=";"
            )
            
            columns = df_header.columns.tolist()
            
            # Удаляем служебные столбцы из индекса
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
    
    # Сохраняем индекс в pickle для быстрого восстановления
    try:
        with open(SIGNAL_INDEX_PATH, "wb") as f:
            pickle.dump(signal_index, f)
        print(f"[OK] Signal index cached to {SIGNAL_INDEX_PATH}")
    except Exception as e:
        print(f"[WARN] Failed to cache signal index: {e}")
    
    return signal_index


def load_signal_index(folder: str) -> Dict[str, List[str]]:
    """
    Загружает индекс либо из кэша, либо перестраивает его
    """
    if os.path.exists(SIGNAL_INDEX_PATH):
        try:
            with open(SIGNAL_INDEX_PATH, "rb") as f:
                index = pickle.load(f)
            print(f"[OK] Signal index loaded from cache")
            return index
        except Exception as e:
            print(f"[WARN] Failed to load cached index: {e}")
    
    # Если кэша нет, перестраиваем
    return build_signal_index(folder)

def load_signal_data_optimized(signal_names: List[str], folder: str) -> Dict[str, pd.DataFrame]:
    """
    Загружает только нужные сигналы из только нужных файлов
    Returns: {signal_name -> DataFrame}
    """
    folder_abs = folder if os.path.isabs(folder) else os.path.normpath(
        os.path.join(BASE_DIR, folder)
    )
    
    signal_index = STATE.get("signal_index", {})
    if not signal_index:
        raise RuntimeError("Signal index not initialized")
    
    signal_names_set = set(signal_names)
    found_signals = {}
    files_to_load = set()
    
    # Определяем, какие файлы нужно загружать
    for signal_name in signal_names_set:
        if signal_name in signal_index:
            files_to_load.update(signal_index[signal_name])
    
    print(f"[INFO] Loading {len(signal_names_set)} signals from {len(files_to_load)} files")
    
    # Загружаем данные из файлов
    for filepath in files_to_load:
        try:
            df = pd.read_csv(
                filepath,
                encoding="ISO-8859-2",
                sep=";"
            )
            
            # Обработка даты/времени
            df["TIME"] = df["TIME"].str.replace(",", ".", regex=False)
            df["TIME"] = df["TIME"].str.split(".").str[0]
            combined = df["DATE"] + " " + df["TIME"]
            df["datetime"] = pd.to_datetime(
                combined, format="%d.%m.%Y %H:%M:%S", errors="coerce"
            )
            df = df.dropna(subset=["datetime"])
            df = df.drop(['DATE', 'TIME'], axis=1)
            
            # Сортируем по datetime
            df = df.sort_values("datetime")
            
            # Извлекаем только нужные сигналы
            available_columns = set(df.columns) & signal_names_set
            for signal_name in available_columns:
                if signal_name not in found_signals:
                    # Сохраняем datetime и значение сигнала
                    found_signals[signal_name] = df[["datetime", signal_name]].copy()
                    found_signals[signal_name].columns = ["datetime", "value"]
        
        except Exception as e:
            print(f"[WARN] Failed to read {filepath}: {e}")
            continue
    
    return found_signals






@app.on_event("startup")
def startup():
    settings = load_settings()
    STATE["settings"] = settings
    folder = settings.get("signalDataFolder")
    if not folder:
        raise RuntimeError("settings.json: signalDataFolder is required")
    STATE["signals"] = load_signals_from_folder(folder)
    STATE["templates"] = load_templates()
    STATE["signal_index"] = load_signal_index(settings.get("signalArchiveFolder"))

    print(f"[OK] loaded signals: {len(STATE['signals'])}")
    print(f"[OK] signal index has {len(STATE['signal_index'])} unique signals")
    print(f"[OK] loaded templates: {len(STATE['templates'].get('templates', []))}")

@app.get("/api/settings")
def api_settings():
    return STATE["settings"]

@app.get("/api/signals")
def api_signals(q: str = "", limit: int = 50):
    """
    q — маска со * (например *MAA*CP*)
    """
    signals = STATE["signals"] or []
    if not q:
        return {"items": signals[:limit], "total": len(signals)}

    # маска * -> regex
    import re
    escaped = re.escape(q).replace(r"\*", ".*")
    rx = re.compile("^" + escaped + "$", re.IGNORECASE)

    items = [s for s in signals if rx.match(s["Tagname"])]
    return {"items": items[:max(1, min(limit, 500))], "total": len(items)}

# Helper: возвращает абсолютный путь к файлу проекта
def get_project_path(filename: str):
    folder = STATE["settings"].get("projectDataFolder")
    if not folder:
        raise RuntimeError("projectDataFolder not configured")
    
    # Нормализуем путь к папке проектов
    project_dir = folder if os.path.isabs(folder) else os.path.normpath(os.path.join(BASE_DIR, folder))
    
    # Проверяем, что filename безопасен (не пытается выйти за пределы папки)
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = os.path.join(project_dir, filename)
    # Проверяем, что итоговый путь лежит внутри разрешенной директории
    if not path.startswith(project_dir):
         raise HTTPException(status_code=400, detail="Path traversal attempt")
         
    return path

@app.post("/api/project/save")
async def save_project(request: Request):
    try:
        data = await request.json()
        filename = data.get("filename")
        content = data.get("content")

        if not filename or not content:
            raise HTTPException(status_code=400, detail="Filename and content are required")
        
        path = get_project_path(filename)
        
        # Сохраняем как JSON
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
            
        return {"status": "ok", "message": f"Project saved to {filename}"}
        
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error saving project: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during save")

@app.get("/api/project/load/{filename}")
def load_project(filename: str):
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
    
@app.get("/api/formula-templates")
def api_formula_templates():
    return STATE.get("templates") or {"templates": []}
    
@app.get("/api/project/list")
def list_projects():
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

@app.post("/api/signal-data")
async def api_signal_data(request: Request):
    """
    POST с JSON телом:
    {
        "signal_names": ["SIGNAL1", "SIGNAL2", ...],
        "format": "parquet"  # или "json"
    }
    
    Returns: Parquet файл с данными или JSON
    """
    try:
        data = await request.json()
        signal_names = data.get("signal_names", [])
        output_format = data.get("format", "parquet")  # По умолчанию Parquet
        
        if not signal_names:
            raise HTTPException(status_code=400, detail="signal_names is required")
        
        folder = STATE["settings"].get("signalArchiveFolder")
        if not folder:
            raise HTTPException(status_code=500, detail="signalArchiveFolder not configured")
        
        # Загружаем данные сигналов
        signals_data = load_signal_data_optimized(signal_names, folder)
        
        # Подготавливаем ответ
        response = {
            "found": list(signals_data.keys()),
            "not_found": [s for s in signal_names if s not in signals_data],
            "format": output_format
        }
        
        if not signals_data:
            raise HTTPException(status_code=404, detail="No signals found")
        
        # Экспортируем данные в зависимости от формата
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
    """
    Экспортирует данные в Parquet (НАМНОГО меньше чем JSON!)
    """
    from fastapi.responses import FileResponse
    
    try:
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            tmp_path = tmp.name
        
        # Каждый сигнал сохраняем как отдельную таблицу в Parquet
        # Используем структуру: datetime, signal_name, value
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
    """
    Экспортирует данные в JSON (медленнее и больше, но совместимее)
    """
    from fastapi.responses import JSONResponse
    
    try:
        # Формируем JSON с каждым сигналом отдельно
        data_dict = {}
        for signal_name, df in signals_data.items():
            df_copy = df.copy()
            df_copy["datetime"] = df_copy["datetime"].astype(str)
            data_dict[signal_name] = df_copy.to_dict(orient="records")
        
        response_data = {
            **meta,
            "data": data_dict
        }
        
        return JSONResponse(response_data)
    
    except Exception as e:
        print(f"[ERROR] JSON export failed: {e}")
        raise

# Простое файловое хранилище для сессий визуализации
VIS_SESSIONS_DIR = os.path.join(tempfile.gettempdir(), "viz_sessions")
os.makedirs(VIS_SESSIONS_DIR, exist_ok=True)

def _save_viz_session(data: Dict) -> str:
    token = uuid.uuid4().hex
    path = os.path.join(VIS_SESSIONS_DIR, f"{token}.pkl")
    with open(path, "wb") as f:
        pickle.dump(data, f)
    return token

def _load_viz_session(token: str) -> Dict:
    path = os.path.join(VIS_SESSIONS_DIR, f"{token}.pkl")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f)

@app.post("/api/visualize/session")
async def create_visualize_session(payload: Dict):
    signals = payload.get("signals", [])
    code = payload.get("code", "")
    if not isinstance(signals, list):
        raise HTTPException(status_code=400, detail="signals must be a list")
    token = _save_viz_session({"signals": signals, "code": code})
    return {"token": token}

@app.get("/api/visualize/session/{token}")
def get_visualize_session(token: str):
    data = _load_viz_session(token)
    if not data:
        raise HTTPException(status_code=404, detail="session not found")
    return data


# Раздаём фронтенд
WEB_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "web"))
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")