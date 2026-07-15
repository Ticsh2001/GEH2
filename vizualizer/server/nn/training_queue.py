"""
training_queue.py
Очередь обучения нейросетей. Один GPU -> одновременно обучается только
одна модель, остальные заявки ждут своей очереди. Хранение — файловое
(training_jobs/<job_id>/job.json), чтобы:
  - очередь переживала рестарт бэкенда
  - воркер (отдельный процесс) и API общались без RPC — просто читают/пишут
    один и тот же файл + metrics.jsonl

Использование в main.py:

    import training_queue as tq

    @app.on_event("startup")
    async def _start_dispatcher():
        tq.start_dispatcher()

    @app.post("/api/nn/train")
    async def train_nn(payload: dict = Body(...)):
        job_id = tq.enqueue_job(...)
        return {"job_id": job_id, "status": "queued"}

    @app.get("/api/nn/train/status/{job_id}")
    async def train_status(job_id: str):
        return tq.get_job_with_metrics(job_id)
"""
import os
import json
import uuid
import asyncio
import subprocess
import sys
import time
from typing import Optional, List, Dict

JOBS_DIR = os.environ.get('TRAINING_JOBS_DIR', os.path.join(os.path.dirname(__file__), 'training_jobs'))
WORKER_SCRIPT = os.environ.get('TRAIN_WORKER_SCRIPT', os.path.join(os.path.dirname(__file__), 'train_worker.py'))
POLL_INTERVAL_SEC = float(os.environ.get('TRAINING_DISPATCH_INTERVAL', '3'))

TRAINING_USER = os.environ.get('TRAINING_USER', 'tishchenkova@lofi.pgt')


os.makedirs(JOBS_DIR, exist_ok=True)

_dispatcher_task: Optional[asyncio.Task] = None
_current_process: Optional[subprocess.Popen] = None
_current_job_id: Optional[str] = None
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Работа с job.json / metrics.jsonl на диске
# ---------------------------------------------------------------------------

def _job_dir(job_id: str) -> str:
    return os.path.join(JOBS_DIR, job_id)


def _job_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), 'job.json')


def _metrics_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), 'metrics.jsonl')


def _write_job(job_id: str, data: dict):
    with open(_job_path(job_id), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    


def get_job(job_id: str) -> Optional[dict]:
    path = _job_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_job_with_metrics(job_id: str, tail: int = 200) -> Optional[dict]:
    """Статус job'а + последние `tail` строк метрик (эпох) для графика на фронте."""
    job = get_job(job_id)
    if job is None:
        return None
    metrics: List[dict] = []
    mpath = _metrics_path(job_id)
    if os.path.exists(mpath):
        with open(mpath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines[-tail:]:
            line = line.strip()
            if line:
                try:
                    metrics.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    job['metrics'] = metrics
    return job


def list_jobs(config: Optional[str] = None, project_code: Optional[str] = None,
              element_id: Optional[str] = None, statuses: Optional[List[str]] = None) -> List[dict]:
    jobs = []
    if not os.path.isdir(JOBS_DIR):
        return jobs
    for jid in os.listdir(JOBS_DIR):
        job = get_job(jid)
        if not job:
            continue
        if config is not None and job.get('config') != config:
            continue
        if project_code is not None and job.get('project_code') != project_code:
            continue
        if element_id is not None and job.get('element_id') != element_id:
            continue
        if statuses is not None and job.get('status') not in statuses:
            continue
        jobs.append(job)
    jobs.sort(key=lambda j: j.get('created_at', ''))
    return jobs


def find_active_job_for_element(element_id: str, project_code: str) -> Optional[dict]:
    active = list_jobs(project_code=project_code, element_id=element_id, statuses=['queued', 'running'])
    return active[0] if active else None


# ---------------------------------------------------------------------------
# Постановка в очередь
# ---------------------------------------------------------------------------

def enqueue_job(config: str, project_code: str, element_id: str, design_code: str,
                 user: str, settings: dict, inputs: Dict[str, str], train_hash: str) -> str:
    """
    inputs: {"X_train": abs_path, "Y_train": abs_path, "X_val": abs_path, "Y_val": abs_path}
    settings: гиперпараметры, снятые со связанного элемента "Настройка"
              (epochs, batch_size, optimizer, loss, metrics, ...)
    train_hash: nn_template.compute_train_hash(...) — сохранится в meta.json модели
                после обучения, чтобы потом проверять актуальность
    """
    job_id = uuid.uuid4().hex[:12]
    os.makedirs(_job_dir(job_id), exist_ok=True)
    os.chmod(_job_dir(job_id), 0o777)
    os.chmod(JOBS_DIR, 0o777)           # на случай, если корневая папка была создана без прав
    job = {
        'job_id': job_id,
        'config': config,
        'project_code': project_code,
        'element_id': element_id,
        'design_code': design_code,
        'user': user,
        'status': 'queued',
        'created_at': _now_iso(),
        'started_at': None,
        'finished_at': None,
        'error': None,
        'settings': settings,
        'inputs': inputs,
        'train_hash': train_hash,
        'progress': {'epoch': 0, 'total_epochs': settings.get('epochs')},
    }
    _write_job(job_id, job)
    os.chmod(_job_path(job_id), 0o666)
    return job_id


def cancel_job(job_id: str) -> bool:
    """Убрать job из очереди, если он ещё не начал обучаться.
    Обучение "в процессе" не прерываем (риск оставить GPU/процесс в подвешенном
    состоянии) — только помечаем как queued->cancelled."""
    job = get_job(job_id)
    if not job or job['status'] != 'queued':
        return False
    job['status'] = 'cancelled'
    job['finished_at'] = _now_iso()
    _write_job(job_id, job)
    return True


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec='seconds')


# ---------------------------------------------------------------------------
# Диспетчер: следит, чтобы одновременно работал не более чем один train_worker
# ---------------------------------------------------------------------------

def start_dispatcher():
    """Вызывать один раз при старте FastAPI (on_event('startup'))."""
    global _dispatcher_task
    _reconcile_on_startup()
    if _dispatcher_task is None or _dispatcher_task.done():
        _dispatcher_task = asyncio.create_task(_dispatch_loop())


def _reconcile_on_startup():
    """Если бэкенд перезапустили во время обучения, процесс воркера мёртв,
    но job.json мог остаться в 'running' — переводим такие job'ы в 'error',
    чтобы они не блокировали очередь навечно."""
    for job in list_jobs(statuses=['running']):
        job['status'] = 'error'
        job['error'] = 'Обучение прервано перезапуском сервера'
        job['finished_at'] = _now_iso()
        _write_job(job['job_id'], job)


async def _dispatch_loop():
    global _current_process, _current_job_id
    while True:
        try:
            async with _lock:
                if _current_process is not None:
                    ret = _current_process.poll()
                    if ret is not None:
                        # процесс завершился — воркер сам обязан был выставить
                        # статус done/error в job.json; подстрахуемся на случай краша
                        job = get_job(_current_job_id) if _current_job_id else None
                        if job and job['status'] == 'running':
                            job['status'] = 'error'
                            job['error'] = f'Процесс обучения завершился неожиданно (код {ret})'
                            job['finished_at'] = _now_iso()
                            _write_job(_current_job_id, job)
                        _current_process = None
                        _current_job_id = None

                if _current_process is None:
                    queued = list_jobs(statuses=['queued'])
                    if queued:
                        job = queued[0]
                        job['status'] = 'running'
                        job['started_at'] = _now_iso()
                        _write_job(job['job_id'], job)
                        _current_process = subprocess.Popen(
                            ['sudo', '-u', TRAINING_USER, sys.executable, WORKER_SCRIPT, job['job_id']],
                            cwd=os.path.dirname(WORKER_SCRIPT) or '.',)
                        _current_job_id = job['job_id']
        except Exception as e:
            print(f"[training_queue] dispatcher error: {e}")
        await asyncio.sleep(POLL_INTERVAL_SEC)


def current_running_job_id() -> Optional[str]:
    return _current_job_id


def queue_position(job_id: str) -> Optional[int]:
    """0 = сейчас обучается, 1 = следующий, и т.д. None, если job не найден в очереди."""
    if job_id == _current_job_id:
        return 0
    queued = list_jobs(statuses=['queued'])
    for i, j in enumerate(queued, start=1):
        if j['job_id'] == job_id:
            return i
    return None
