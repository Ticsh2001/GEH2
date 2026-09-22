#!/usr/bin/env python3
"""
generate_inference_config.py — генератор JSON-конфига для инференса
проекта типа neural_network.

Использование:
    python generate_inference_config.py <project.json> [-o <out.json>] [-v]

Выходной конфиг потребляется nn_inference_runtime.py. Никакой код под
конкретный проект не генерируется.

Структура конфига
-----------------
    {
      "config_version": "1.0",
      "runtime_version": "1.0",
      "generated_at": "...",
      "project": {
        "code": [...],            # KKS выходных сигналов модели
        "description": [...]      # описания выходных сигналов
      },
      "input_contract": {
        "resample_freq": "30min",
        "datetime_format": "%d.%m.%Y %H:%M:%S",
        "min_history_rows": 48,
        "min_history_time": "24h"
      },
      "prediction_shift": {<KKS>: {"value": N, "unit": U} | null},
      "inputs": [
        {"name", "dimension", "description", "comment"}, ...
      ],
      "chain_input": [
        {"type": "dataset", ...},
        {"type": "normalize", "rules": [...]},   // опционально
        {"type": "labeler", ...}
      ],
      "chain_output": {
        <KKS>: [{"type": "denormalize", "min": ..., "max": ...}]
      }
    }
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import pandas as pd


RUNTIME_MODULE_NAME = "nn_inference_runtime"
CONFIG_VERSION = "1.0"

# Шаги, которые нужны только для обучения: на инференсе не выполняются.
TRAINING_ONLY_STEP_TYPES = frozenset({"timefilter", "timeshift"})


# =============================================================================
# Обход графа проекта
# =============================================================================

def load_project(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_nn_template(project):
    for eid, elem in project["elements"].items():
        if elem.get("nnType") == "nn-template":
            return eid
    raise ValueError("В проекте нет элемента nn-template")


def incoming_edges(project, element_id):
    """Все входящие рёбра, отсортированные по числовому индексу в toPort."""
    edges = [c for c in project.get("connections", [])
             if c["toElement"] == element_id]
    edges.sort(key=lambda c: int(c["toPort"].split("-")[1]))
    return edges


def find_x_labeler(project):
    """Найти labeler, чей out-0 кормит X-вход модели."""
    tmpl_id = find_nn_template(project)
    elements = project["elements"]

    candidates = []
    for conn in incoming_edges(project, tmpl_id):
        src = elements.get(conn["fromElement"], {})
        if src.get("nnType") == "labeler":
            x_cols = (src.get("props") or {}).get("x_columns") or []
            if x_cols:
                candidates.append((conn["toPort"], src["id"]))

    if not candidates:
        raise ValueError("Не найден labeler, формирующий X для nn-template")

    for port, eid in candidates:
        if port == "in-1":
            return eid
    candidates.sort(key=lambda p: int(p[0].split("-")[1]))
    return candidates[0][1]


def find_y_labelers(project):
    """Список (labeler_id, y_column) для Y-производящих labeler'ов."""
    tmpl_id = find_nn_template(project)
    elements = project["elements"]
    result = []
    seen = set()
    for conn in incoming_edges(project, tmpl_id):
        if conn["toPort"] not in ("in-2", "in-4"):
            continue
        src = elements.get(conn["fromElement"], {})
        if src.get("nnType") != "labeler":
            continue
        y_col = (src.get("props") or {}).get("y_column")
        if not y_col or src["id"] in seen:
            continue
        seen.add(src["id"])
        result.append((src["id"], y_col))
    return result


def collect_x_chain(project, end_id):
    """Backward DFS от end_id; возвращает id элементов в прямом порядке."""
    elements = project["elements"]
    visited = set()
    out = []

    def walk(eid):
        if eid in visited:
            return
        visited.add(eid)
        elem = elements[eid]

        if elem["type"] == "input-signal":
            out.append(eid)
            return

        for conn in incoming_edges(project, eid):
            src = elements.get(conn["fromElement"], {})
            if src.get("nnType") == "labeler":
                x_cols = (src.get("props") or {}).get("x_columns") or []
                if not x_cols:
                    continue  # y-only labeler
            if src.get("nnType") == "timeshift" and conn.get("fromPort") == "out-1":
                continue  # ветка «будущее» — только для Y
            walk(conn["fromElement"])

        out.append(eid)

    walk(end_id)
    return out


def _walk_y_path_back(project, y_labeler_id):
    """Путь от y-labeler'а назад к входу.

    Порядок = ОБРАТНЫЙ порядку применения при обучении. Первый элемент —
    y_labeler, последний — input-signal.
    """
    elements = project["elements"]
    path = [y_labeler_id]
    visited = {y_labeler_id}
    current = y_labeler_id
    while True:
        elem = elements[current]
        if elem["type"] == "input-signal":
            break
        inc = incoming_edges(project, current)
        if not inc:
            break
        nxt = inc[0]["fromElement"]
        if nxt in visited:
            break
        visited.add(nxt)
        path.append(nxt)
        current = nxt
    return path


def _collect_dataset_inputs(project, dataset_eid, verbose=False):
    """Уникальные имена сигналов на входах dataset, в порядке toPort.

    Дедуп по ИМЕНИ сигнала (а не по id элемента) — как в backend'е.
    """
    names = []
    seen = set()
    for conn in incoming_edges(project, dataset_eid):
        src_id = conn["fromElement"]
        src = project["elements"][src_id]
        if src["type"] != "input-signal":
            raise ValueError(
                "Неподдерживаемый вход dataset %r: не input-signal "
                "(цепочки dataset не поддерживаются)" % src_id)
        name = (src["props"] or {}).get("name", "").strip()
        if not name:
            raise ValueError("input-signal %r не имеет имени" % src_id)
        if name in seen:
            if verbose:
                print("  [chain_input] сигнал %r приходит на несколько портов "
                      "dataset — оставляю первое вхождение" % name,
                      file=sys.stderr)
            continue
        seen.add(name)
        names.append(name)
    return names


# =============================================================================
# Сборка chain_input
# =============================================================================

def _collect_range_checks(project, verbose=False):
    """Собрать границы фильтра из X-пути — для диагностики на инференсе.

    Возвращает список {"column", "min", "max"} по всем filter-правилам,
    где задана хотя бы одна граница. Используется только для проверки
    входных данных на «выпадение» из диапазона, на котором обучалась
    модель. Сами преобразования не выполняет.
    """
    x_labeler_id = find_x_labeler(project)
    chain_ids = collect_x_chain(project, x_labeler_id)

    checks = []
    seen = set()
    for eid in chain_ids:
        elem = project["elements"][eid]
        if elem.get("nnType") != "filter":
            continue
        props = elem.get("props") or {}
        for r in props.get("rules", []):
            col = r.get("column")
            mn = r.get("min")
            mx = r.get("max")
            if not col or (mn is None and mx is None):
                continue
            if col in seen:
                continue
            seen.add(col)
            checks.append({
                "column": col,
                "min": float(mn) if mn is not None else None,
                "max": float(mx) if mx is not None else None,
            })
    if verbose and checks:
        print("  [range_checks] %d правил(о) для диагностики: %s"
              % (len(checks), [c["column"] for c in checks]),
              file=sys.stderr)
    return checks


def build_chain_input(project, verbose=False):
    """Построить цепочку X-преобразований.

    Шаги:
      - dataset    -> как в проекте
      - filter     -> заменяем на normalize (только правила с normalize:true,
                      границы min/max берём из правила)
      - timefilter -> исключаем (training-only)
      - timeshift  -> исключаем (training-only)
      - labeler    -> как в проекте (y_column=None)
    """
    x_labeler_id = find_x_labeler(project)
    chain_ids = collect_x_chain(project, x_labeler_id)

    steps = []
    for eid in chain_ids:
        elem = project["elements"][eid]
        if elem["type"] == "input-signal":
            continue
        nn = elem["nnType"]
        props = elem.get("props") or {}

        if nn in TRAINING_ONLY_STEP_TYPES:
            if verbose:
                print("  [chain_input] пропущен training-only шаг: %s (%s)"
                      % (eid, nn), file=sys.stderr)
            continue

        if nn == "dataset":
            names = _collect_dataset_inputs(project, eid, verbose)
            steps.append({
                "type": "dataset",
                "ref_signal_index": int(props.get("reference_signal_index", 0) or 0),
                "interpolation": props.get("interpolation", "linear"),
                "inputs": names,
            })
            continue

        if nn == "filter":
            norm_rules = []
            for r in props.get("rules", []):
                if not r.get("normalize"):
                    continue
                mn = r.get("min")
                mx = r.get("max")
                if mn is None or mx is None:
                    print("  [chain_input] ВНИМАНИЕ: правило нормализации "
                          "для %r пропущено — нет min/max "
                          "(пересохраните проект в UI, чтобы заполнить границы)."
                          % r.get("column"), file=sys.stderr)
                    continue
                norm_rules.append({
                    "column": r["column"],
                    "min": float(mn),
                    "max": float(mx),
                })
            if norm_rules:
                steps.append({"type": "normalize", "rules": norm_rules})
            continue

        if nn == "labeler":
            steps.append({
                "type": "labeler",
                "x_columns": props.get("x_columns", []),
                "y_column": None,
                "window_size": int(props.get("window_size", 1)),
                "window_unit": props.get("window_unit", "rows"),
            })
            continue

        print("  [chain_input] ВНИМАНИЕ: неизвестный nnType %r — пропущен" % nn,
              file=sys.stderr)

    return steps


# =============================================================================
# Сборка chain_output и prediction_shift
# =============================================================================

def build_chain_output(project, output_codes, verbose=False):
    """Собрать выходные преобразования и сдвиг предсказания.

    Возвращает (chain_output, prediction_shift), ключённые output-KKS
    (то есть project.code из исходного проекта, НЕ y_column).

    Y-колонка используется только для trace-back: понять, какие именно
    преобразования выполнялись при обучении, чтобы воспроизвести их
    инверсию для выхода модели.
    """
    chain_output = {}
    prediction_shift = {}

    y_labelers = find_y_labelers(project)
    if not y_labelers:
        raise ValueError(
            "В проекте не найден Y-производитель (labeler с y_column). "
            "Невозможно определить выходные преобразования модели.")

    if len(y_labelers) > 1 and len(output_codes) != len(y_labelers):
        raise ValueError(
            "Количество Y-производителей (%d) не совпадает с количеством "
            "выходных KKS-кодов (%d). Multi-output пока не поддержан."
            % (len(y_labelers), len(output_codes)))

    for i, (y_labeler_id, y_col) in enumerate(y_labelers):
        # Пока предполагаем 1-1: первый Y -> первый output KKS.
        out_code = output_codes[i] if i < len(output_codes) else y_col

        path = _walk_y_path_back(project, y_labeler_id)
        steps = []
        shift = None
        for eid in path:
            elem = project["elements"][eid]
            nn = elem.get("nnType")
            props = elem.get("props") or {}

            if nn == "filter" and not steps:
                for r in props.get("rules", []):
                    if (r.get("column") == y_col
                            and r.get("normalize")
                            and r.get("min") is not None
                            and r.get("max") is not None):
                        steps.append({
                            "type": "denormalize",
                            "min": float(r["min"]),
                            "max": float(r["max"]),
                        })
                        break

            if nn == "timeshift" and shift is None:
                shift = {
                    "value": int(props.get("shift_value", 0)),
                    "unit": props.get("shift_unit", "days"),
                }

        chain_output[out_code] = steps
        prediction_shift[out_code] = shift
        if verbose:
            print("  [chain_output] %s (Y=%s): shift=%r, steps=%d"
                  % (out_code, y_col, shift, len(steps)), file=sys.stderr)

    return chain_output, prediction_shift


# =============================================================================
# Прочие утилиты
# =============================================================================

def max_window_size(steps):
    m = 1
    for s in steps:
        if s["type"] == "labeler":
            m = max(m, int(s.get("window_size", 1)))
    return m


def collect_input_signal_meta(project, ordered_names):
    """Метаданные сигналов в порядке `ordered_names`."""
    by_name = {}
    for elem in project["elements"].values():
        if elem.get("type") != "input-signal":
            continue
        props = elem.get("props") or {}
        name = (props.get("name") or "").strip()
        if name:
            by_name[name] = {
                "name": name,
                "dimension": props.get("dimension", ""),
                "description": props.get("description", ""),
                "comment": props.get("comment", ""),
            }
    return [
        by_name.get(n, {"name": n, "dimension": "",
                        "description": "", "comment": ""})
        for n in ordered_names
    ]


def _normalize_section_signs(obj):
    """Рекурсивно заменить '§' на '_' во всех строках конфига."""
    if isinstance(obj, str):
        return obj.replace("§", "_")
    if isinstance(obj, list):
        return [_normalize_section_signs(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _normalize_section_signs(v) for k, v in obj.items()}
    return obj


def _format_duration_human(seconds):
    if seconds >= 3600:
        return "%gh" % (seconds / 3600.0)
    if seconds >= 60:
        return "%gmin" % (seconds / 60.0)
    return "%gs" % seconds


# =============================================================================
# Runtime discovery / version read
# =============================================================================

def _locate_runtime():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, RUNTIME_MODULE_NAME + ".py")
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Модуль %r не найден рядом с генератором. Ожидался: %s"
            % (RUNTIME_MODULE_NAME, path))
    return path


def _read_runtime_version(runtime_path):
    with open(runtime_path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', line)
            if m:
                return m.group(1)
    raise RuntimeError("Не найден __version__ в %s" % runtime_path)


# =============================================================================
# Сборка конфига
# =============================================================================

def build_config(project, rt_version,
                 resample_freq="30min",
                 datetime_format="%d.%m.%Y %H:%M:%S",
                 verbose=False):
    proj_meta = project.get("project") or {}

    chain_input = build_chain_input(project, verbose=verbose)
    if not chain_input:
        raise ValueError("Не удалось построить chain_input")
    if chain_input[0]["type"] != "dataset":
        raise ValueError(
            "chain_input должен начинаться с шага 'dataset'; получено %r"
            % chain_input[0]["type"])

    # Output KKS — из project.code / project.description исходного проекта.
    proj_code = (proj_meta.get("code") or "").strip()
    proj_desc = (proj_meta.get("description") or "").strip()
    if not proj_code:
        raise ValueError("В проекте отсутствует project.code — "
                         "некуда записать KKS результата модели.")
    output_codes = [proj_code]
    output_descriptions = [proj_desc]

    chain_output, prediction_shift = build_chain_output(
        project, output_codes, verbose=verbose)

    ordered_names = list(chain_input[0]["inputs"])
    input_meta = collect_input_signal_meta(project, ordered_names)
    range_checks = _collect_range_checks(project, verbose=verbose)

    min_history_rows = max_window_size(chain_input)
    try:
        step_td = pd.Timedelta(resample_freq)
        total_seconds = step_td.total_seconds() * min_history_rows
        min_history_time = _format_duration_human(total_seconds)
    except Exception:
        min_history_time = None

    return {
        "config_version": CONFIG_VERSION,
        "runtime_version": rt_version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "code": output_codes,
            "description": output_descriptions,
        },
        "input_contract": {
            "resample_freq": resample_freq,
            "datetime_format": datetime_format,
            "min_history_rows": min_history_rows,
            "min_history_time": min_history_time,
        },
        "range_checks": range_checks,
        "prediction_shift": prediction_shift,
        "inputs": input_meta,
        "chain_input": chain_input,
        "chain_output": chain_output,
    }


def generate(project_path, output_path=None, runtime_path=None, verbose=False):
    if runtime_path is None:
        runtime_path = _locate_runtime()
    try:
        rt_version = _read_runtime_version(runtime_path)
    except RuntimeError:
        rt_version = CONFIG_VERSION

    project = load_project(project_path)
    cfg = build_config(project, rt_version, verbose=verbose)

    # Нормализация § -> _ по всему конфигу
    cfg = _normalize_section_signs(cfg)

    if output_path is None:
        code_hint = ""
        if cfg["project"]["code"]:
            code_hint = cfg["project"]["code"][0]
        elif (project.get("project") or {}).get("code"):
            code_hint = project["project"]["code"]
        safe = "".join(c if (c.isalnum() or c in "._-") else "_"
                       for c in str(code_hint or "inference"))
        output_path = "inference_%s.config.json" % safe

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    return output_path


def main():
    ap = argparse.ArgumentParser(
        description="Генерация JSON-конфига для инференса neural_network-проекта.")
    ap.add_argument("project", help="Путь к проекту (JSON).")
    ap.add_argument("-o", "--output", default=None,
                    help="Путь к выходному конфигу "
                         "(по умолчанию: inference_<code>.config.json).")
    ap.add_argument("--runtime", default=None,
                    help="Путь к nn_inference_runtime.py (по умолчанию — рядом).")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Печатать диагностику в stderr.")
    args = ap.parse_args()

    out = generate(args.project, args.output, args.runtime, verbose=args.verbose)
    print("Сгенерирован конфиг: %s" % out)


if __name__ == "__main__":
    main()