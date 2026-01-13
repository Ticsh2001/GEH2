import os
import json
from typing import Dict, List
import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

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
            df = pd.read_csv(path, sep=';')[['Tagname', 'Description']]
            df = df.dropna(subset=['Tagname'])
            for _, row in df.iterrows():
                tag = str(row['Tagname']).strip()
                desc = "" if pd.isna(row['Description']) else str(row['Description']).strip()
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

# Кэш сигналов в памяти
STATE = {
    "settings": None,
    "signals": None
}

@app.on_event("startup")
def startup():
    settings = load_settings()
    STATE["settings"] = settings
    folder = settings.get("signalDataFolder")
    if not folder:
        raise RuntimeError("settings.json: signalDataFolder is required")
    STATE["signals"] = load_signals_from_folder(folder)
    print(f"[OK] loaded signals: {len(STATE['signals'])}")

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


# Раздаём фронтенд
WEB_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "web"))
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")