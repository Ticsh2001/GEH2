"""
train_worker.py
Отдельный сервис обучения. Запускается как subprocess:

    python train_worker.py <job_id>

Читает training_jobs/<job_id>/job.json, строит нейросеть по дизайн-проекту
через BlockStruct (Block_Struct.py), обучает на переданных X_train/Y_train/
X_val/Y_val, на каждой эпохе дописывает training_jobs/<job_id>/metrics.jsonl,
по завершении сохраняет модель и обновляет job.json.

Специально НЕ импортируется в main.py напрямую — TensorFlow инициализирует
GPU-контекст один раз на процесс, и мы не хотим тащить это в FastAPI-воркер.

По той же причине этот скрипт НЕ импортирует main.py (это утащило бы за
собой FastAPI/CORS/mount статики и все зависимости main.py в процесс
обучения). Вместо этого он сам, минимально, читает settings.json и
воспроизводит main.config_path(), и передаёт эту функцию в nn_template
через nn_template.set_config_path(...) — см. NOTE ниже. main.py делает
то же самое своей "боевой" config_path() в @app.on_event("startup").
"""
import sys
import os
import json
import datetime
import traceback

import numpy as np
import pandas as pd
import tensorflow as tf

# Файлы лежат в nn/ рядом друг с другом — при запуске "python train_worker.py"
# Python сам добавляет папку скрипта в sys.path[0], так что sibling-импорты
# ниже резолвятся без префикса пакета.
import training_queue as tq
import nn_template
import nn_settings
from Block_Struct import BlockStruct, create_neural_model

# ---------------------------------------------------------------------------
# NOTE: минимальный самостоятельный config_path — дублирует небольшой кусок
# логики main.py (_abs_folder/config_path). Если в main.py поменяется схема
# путей (например появится ещё один уровень вложенности) — поправьте и тут.
# nn/ лежит на один уровень ниже корня проекта (там же, где main.py и
# settings.json), отсюда PROJECT_ROOT = папка выше nn/.
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SETTINGS_PATH = os.path.join(PROJECT_ROOT, "settings.json")

with open(_SETTINGS_PATH, "r", encoding="utf-8") as _f:
    _SETTINGS = json.load(_f)


def _standalone_abs_folder(setting_key: str):
    base = _SETTINGS.get(setting_key)
    if not base:
        return None
    if not os.path.isabs(base):
        base = os.path.normpath(os.path.join(PROJECT_ROOT, base))
    return base


def _standalone_config_path(setting_key: str, config: str):
    base = _standalone_abs_folder(setting_key)
    if not base:
        return None
    return os.path.join(base, config) if config else base


nn_template.set_config_path(_standalone_config_path)
nn_template.set_template_dir(lambda: _standalone_abs_folder("templateDataFolder"))


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def _load_xlsx_as_array(path: str) -> np.ndarray:
    df = pd.read_excel(path)
    if 'datetime' in df.columns:
        df = df.drop(columns=['datetime'])
    return df.to_numpy(dtype='float32')


class MetricsLoggerCallback(tf.keras.callbacks.Callback):
    """Пишет метрики каждой эпохи в metrics.jsonl и обновляет прогресс в job.json.
    Формат подобран так, чтобы фронт мог рисовать график обучения "почти
    в реальном времени" простым поллингом /api/nn/train/status/{job_id}
    (сама визуализация — отдельная задача на потом, тут только источник данных)."""

    def __init__(self, job_id: str, total_epochs: int):
        super().__init__()
        self.job_id = job_id
        self.total_epochs = total_epochs

    def on_epoch_end(self, epoch, logs=None):
        logs = logs or {}
        record = {'epoch': epoch + 1, 'timestamp': _now_iso()}
        record.update({k: float(v) for k, v in logs.items()})
        with open(tq._metrics_path(self.job_id), 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

        job = tq.get_job(self.job_id)
        if job:
            job['progress'] = {'epoch': epoch + 1, 'total_epochs': self.total_epochs, 'last_metrics': record}
            tq._write_job(self.job_id, job)


def build_model(design_project: dict, input_shape: tuple) -> tf.keras.Model:
    structure, options = nn_template.build_structure_and_options(design_project)
    block = BlockStruct('m', structure, options=options)
    input_layer = tf.keras.Input(shape=input_shape)
    model = create_neural_model(block, input_layer)
    return model


def run(job_id: str):
    job = tq.get_job(job_id)
    if job is None:
        raise RuntimeError(f"Job {job_id} не найден")

    try:
        config = job['config']
        design_code = job['design_code']
        settings = job.get('settings', {})
        inputs = job['inputs']

        design_project = nn_template.load_design_project(config, design_code)

        X_train = _load_xlsx_as_array(inputs['X_train'])
        Y_train = _load_xlsx_as_array(inputs['Y_train'])
        # Выровнять количество образцов (временное решение, пока не исправлена логика labeler)
        min_len = min(len(X_train), len(Y_train))
        X_train = X_train[:min_len]
        Y_train = Y_train[:min_len]
        X_val = _load_xlsx_as_array(inputs['X_val']) if inputs.get('X_val') else None
        Y_val = _load_xlsx_as_array(inputs['Y_val']) if inputs.get('Y_val') else None
        if X_val is not None and Y_val is not None:
            min_val_len = min(len(X_val), len(Y_val))
            X_val = X_val[:min_val_len]
            Y_val = Y_val[:min_val_len]

        model = build_model(design_project, input_shape=(X_train.shape[1],))

        optimizer, compile_kwargs, fit_kwargs, extra_callbacks = nn_settings.build_training_kwargs(settings)
        model.compile(optimizer=optimizer, **compile_kwargs)

        epochs = int(fit_kwargs.pop('epochs', 50))
        batch_size = int(fit_kwargs.pop('batch_size', 32))

        validation_data = (X_val, Y_val) if X_val is not None and Y_val is not None else None
        if validation_data is not None:
            # validation_split несовместим с явным validation_data — X_val/Y_val в приоритете
            fit_kwargs.pop('validation_split', None)

        model.fit(
            X_train, Y_train,
            validation_data=validation_data,
            epochs=epochs,
            batch_size=batch_size,
            verbose=2,
            callbacks=[MetricsLoggerCallback(job_id, epochs)] + extra_callbacks,
            **fit_kwargs,
        )

        model_path = nn_template.get_model_path(config, job['project_code'], job['element_id'])
        model.save(model_path)

        meta = {
            'design_code': design_code,
            'design_hash': nn_template.design_hash(design_project),
            'settings': settings,
            'input_hashes': job.get('input_hashes', {}),
            'train_hash': job['train_hash'],
            'trained_at': _now_iso(),
            'job_id': job_id,
        }
        with open(nn_template.get_model_meta_path(config, job['project_code'], job['element_id']),
                  'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        job = tq.get_job(job_id)
        job['status'] = 'done'
        job['finished_at'] = _now_iso()
        tq._write_job(job_id, job)

    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        job = tq.get_job(job_id) or job
        job['status'] = 'error'
        job['error'] = err
        job['finished_at'] = _now_iso()
        tq._write_job(job_id, job)
        sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Использование: python train_worker.py <job_id>", file=sys.stderr)
        sys.exit(2)
    run(sys.argv[1])