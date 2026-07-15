"""
nn_settings.py
Единый источник правды для параметров обучения (nn_settings_params.json):
  - фронтенд рендерит по нему форму элемента "Настройка" (через уже
    существующий в neural_app.js универсальный рендерер paramMeta —
    ничего дополнительно писать не нужно, схема просто маппится в
    blockParams['nn-settings'].paramMeta/defaults)
  - бэкенд (train_worker.py) "проецирует" сохранённые пользователем
    значения в аргументы optimizer / model.compile() / model.fit() /
    callbacks

Чтобы добавить новый гиперпараметр — правится только
nn_settings_params.json, этот файл и train_worker.py трогать не нужно
(если только это не принципиально новый *тип* назначения, отличный от
optimizer_name/optimizer_kwarg/compile/fit/callback/callback_arg).
"""
import os
import json

import tensorflow as tf

SETTINGS_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'training_params.json')

OPTIMIZER_CLASSES = {
    'adam': tf.keras.optimizers.Adam,
    'sgd': tf.keras.optimizers.SGD,
    'rmsprop': tf.keras.optimizers.RMSprop,
    'adagrad': tf.keras.optimizers.Adagrad,
    'adamw': tf.keras.optimizers.AdamW,
    'nadam': tf.keras.optimizers.Nadam,
}

CALLBACK_CLASSES = {
    'early_stopping': tf.keras.callbacks.EarlyStopping,
    'reduce_lr': tf.keras.callbacks.ReduceLROnPlateau,
}


def load_settings_schema() -> dict:
    with open(SETTINGS_SCHEMA_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _cast(value, meta):
    t = meta.get('type')
    if value is None:
        value = meta.get('default')
    if t == 'number':
        return float(value)
    if t == 'boolean':
        return bool(value)
    return value


def build_training_kwargs(settings: dict):
    """
    settings: props элемента "Настройка" (то, что сохранил пользователь в
              модалке — подмножество ключей схемы, отсутствующие берутся
              из default).
    Возвращает (optimizer_instance, compile_kwargs, fit_kwargs, callbacks).
    """
    schema = load_settings_schema()

    optimizer_name = settings.get('optimizer', schema.get('optimizer', {}).get('default', 'adam'))
    optimizer_kwargs = {}
    compile_kwargs = {}
    fit_kwargs = {}
    callback_enabled = {}
    callback_kwargs = {}

    for key, meta in schema.items():
        value = _cast(settings.get(key), meta)
        target = meta.get('target')

        if target == 'optimizer_name':
            continue
        elif target == 'optimizer_kwarg':
            optimizer_kwargs[meta['optimizer_arg']] = value
        elif target == 'compile':
            compile_kwargs[meta['compile_arg']] = value
        elif target == 'fit':
            fit_kwargs[meta['fit_arg']] = value
        elif target == 'callback':
            callback_enabled[meta['callback']] = value
        elif target == 'callback_arg':
            callback_kwargs.setdefault(meta['callback'], {})[meta['callback_arg']] = value
        # неизвестный target -> молча игнорируем, чтобы можно было добавлять
        # в json произвольные вспомогательные поля без падения бэкенда

    optimizer_cls = OPTIMIZER_CLASSES.get(optimizer_name, tf.keras.optimizers.Adam)
    optimizer = optimizer_cls(**optimizer_kwargs)

    callbacks = []
    for name, enabled in callback_enabled.items():
        if not enabled:
            continue
        cls = CALLBACK_CLASSES.get(name)
        if cls is None:
            continue
        callbacks.append(cls(**callback_kwargs.get(name, {})))

    return optimizer, compile_kwargs, fit_kwargs, callbacks
