"""
nn_export.py — библиотека для экспорта neural_network-проекта:
  - построение inference-конфига (адаптация generate_inference_config.py)
  - упаковка модели + метаданных + конфига в ZIP
"""
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

import pandas as pd


CONFIG_VERSION = "1.0"
TRAINING_ONLY_STEP_TYPES = frozenset({"timefilter", "timeshift"})


# =============================================================================
# Обход графа — 1:1 из generate_inference_config.py
# =============================================================================

def find_nn_template(project):
    for eid, elem in project["elements"].items():
        if elem.get("nnType") == "nn-template":
            return eid
    raise ValueError("В проекте нет элемента nn-template")


def incoming_edges(project, element_id):
    edges = [c for c in project.get("connections", []) if c["toElement"] == element_id]
    edges.sort(key=lambda c: int(c["toPort"].split("-")[1]))
    return edges


def find_x_labeler(project):
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
    tmpl_id = find_nn_template(project)
    elements = project["elements"]
    result, seen = [], set()
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
    elements = project["elements"]
    visited, out = set(), []

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
                    continue
            if src.get("nnType") == "timeshift" and conn.get("fromPort") == "out-1":
                continue
            walk(conn["fromElement"])
        out.append(eid)

    walk(end_id)
    return out


def _walk_y_path_back(project, y_labeler_id):
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
    names, seen = [], set()
    for conn in incoming_edges(project, dataset_eid):
        src_id = conn["fromElement"]
        src = project["elements"][src_id]
        if src["type"] != "input-signal":
            raise ValueError("Неподдерживаемый вход dataset %r: не input-signal" % src_id)
        name = (src["props"] or {}).get("name", "").strip()
        if not name:
            raise ValueError("input-signal %r не имеет имени" % src_id)
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


# =============================================================================
# chain_input
# =============================================================================

def _collect_range_checks(project, verbose=False):
    x_labeler_id = find_x_labeler(project)
    chain_ids = collect_x_chain(project, x_labeler_id)
    checks, seen = [], set()
    for eid in chain_ids:
        elem = project["elements"][eid]
        if elem.get("nnType") != "filter":
            continue
        for r in (elem.get("props") or {}).get("rules", []):
            col, mn, mx = r.get("column"), r.get("min"), r.get("max")
            if not col or (mn is None and mx is None) or col in seen:
                continue
            seen.add(col)
            checks.append({
                "column": col,
                "min": float(mn) if mn is not None else None,
                "max": float(mx) if mx is not None else None,
            })
    return checks


def build_chain_input(project, verbose=False):
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
                mn, mx = r.get("min"), r.get("max")
                if mn is None or mx is None:
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

    return steps


# =============================================================================
# chain_output
# =============================================================================

def build_chain_output(project, output_codes, verbose=False):
    chain_output, prediction_shift = {}, {}
    y_labelers = find_y_labelers(project)
    if not y_labelers:
        raise ValueError("Не найден Y-производитель (labeler с y_column)")

    if len(y_labelers) > 1 and len(output_codes) != len(y_labelers):
        raise ValueError(
            "Количество Y-производителей (%d) не совпадает с количеством "
            "выходных KKS (%d)" % (len(y_labelers), len(output_codes)))

    for i, (y_labeler_id, y_col) in enumerate(y_labelers):
        out_code = output_codes[i] if i < len(output_codes) else y_col
        path = _walk_y_path_back(project, y_labeler_id)
        steps, shift = [], None
        for eid in path:
            elem = project["elements"][eid]
            nn = elem.get("nnType")
            props = elem.get("props") or {}
            if nn == "filter" and not steps:
                for r in props.get("rules", []):
                    if (r.get("column") == y_col and r.get("normalize")
                            and r.get("min") is not None and r.get("max") is not None):
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
    return chain_output, prediction_shift


# =============================================================================
# Вспомогательные
# =============================================================================

def max_window_size(steps):
    m = 1
    for s in steps:
        if s["type"] == "labeler":
            m = max(m, int(s.get("window_size", 1)))
    return m


def collect_input_signal_meta(project, ordered_names):
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
    return [by_name.get(n, {"name": n, "dimension": "",
                            "description": "", "comment": ""}) for n in ordered_names]


def _normalize_section_signs(obj):
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
# Главная функция сборки конфига
# =============================================================================

def build_config(project,
                 rt_version=CONFIG_VERSION,
                 resample_freq="30min",
                 datetime_format="%d.%m.%Y %H:%M:%S",
                 verbose=False):
    proj_meta = project.get("project") or {}

    chain_input = build_chain_input(project, verbose=verbose)
    if not chain_input:
        raise ValueError("Не удалось построить chain_input")
    if chain_input[0]["type"] != "dataset":
        raise ValueError("chain_input должен начинаться с 'dataset'")

    proj_code = (proj_meta.get("code") or "").strip()
    proj_desc = (proj_meta.get("description") or "").strip()
    if not proj_code:
        raise ValueError("В проекте отсутствует project.code")

    output_codes = [proj_code]
    output_descriptions = [proj_desc]
    chain_output, prediction_shift = build_chain_output(project, output_codes, verbose=verbose)

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

    cfg = {
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
    return _normalize_section_signs(cfg)


# =============================================================================
# Упаковка в ZIP
# =============================================================================

def build_zip(project_payload: dict,
              model_path: str,
              model_meta_path: str,
              base_name: str) -> bytes:
    """
    Возвращает байты ZIP-архива с тремя файлами:
      <base_name>.keras          — обученная модель
      <base_name>_meta.json      — метаданные модели
      <base_name>.config.json    — inference-конфиг
    """
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"Модель не найдена: {model_path}")
    if not os.path.isfile(model_meta_path):
        raise FileNotFoundError(f"Метаданные модели не найдены: {model_meta_path}")

    cfg = build_config(project_payload)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(model_path, arcname=f"{base_name}.keras")
        zf.write(model_meta_path, arcname=f"{base_name}_meta.json")
        zf.writestr(
            f"{base_name}.config.json",
            json.dumps(cfg, ensure_ascii=False, indent=2)
        )
    buf.seek(0)
    return buf.getvalue()