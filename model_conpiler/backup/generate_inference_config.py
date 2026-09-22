#!/usr/bin/env python3
"""
generate_inference_config.py — emit a JSON config describing a
neural_network project's X-producing preprocessing chain.

Usage:
    python generate_inference_config.py <project.json> [-o <output.json>]
                                                       [--keep-training-steps]
                                                       [-v]

The output config is consumed at runtime by `nn_inference_runtime.py`.
No Python code is generated per project.

Notes on training-only steps
----------------------------
By default, `timefilter` and `timeshift` steps are excluded from the
X-chain because they only make sense during training:

  - timefilter: filters rows by absolute date intervals (e.g. shutdown
    periods) during dataset preparation. At inference we want to predict
    on whatever data is provided, without dropping rows by date.

  - timeshift: aligns X (at time t) with Y (at time t + shift) during
    training. At inference the model itself produces the prediction; we
    feed current X. Keeping out-0 would needlessly drop the last
    `shift_value` rows and reduce the number of predictions we can make.

Pass --keep-training-steps to include them (rarely useful).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


RUNTIME_MODULE_NAME = "nn_inference_runtime"
CONFIG_VERSION = "2.0"

# Step types considered training-only and excluded from the config by default.
TRAINING_ONLY_STEP_TYPES = frozenset({"timefilter", "timeshift"})


# ============================================================================
# 1. Project-graph traversal
# ============================================================================

def load_project(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_nn_template(project):
    for eid, elem in project["elements"].items():
        if elem.get("nnType") == "nn-template":
            return eid
    raise ValueError("Project has no nn-template element")


def incoming_edges(project, element_id):
    """All edges targeting element_id, sorted by the numeric index in toPort."""
    edges = [c for c in project.get("connections", []) if c["toElement"] == element_id]
    edges.sort(key=lambda c: int(c["toPort"].split("-")[1]))
    return edges

def find_prediction_shift(project):
    """Найти сдвиг между X и Y (prediction_shift).

    Y-путь в проекте всегда заходит в nn-template через in-2 или in-4 от
    labeler'а с непустым y_column. Идём от этого labeler'а назад и находим
    первый timeshift — именно его shift_value/shift_unit определяет, на
    сколько предсказание модели отстоит от момента X.

    Возвращает {"value": N, "unit": U} или None, если Y строится без
    временного сдвига относительно X.
    """
    elements = project['elements']
    nn_template_id = find_nn_template(project)

    # Найти Y-labeler'ы (идут в in-2 или in-4 nn-template).
    y_labelers = []
    for conn in incoming_edges(project, nn_template_id):
        if conn['toPort'] not in ('in-2', 'in-4'):
            continue
        src = elements.get(conn['fromElement'], {})
        if src.get('nnType') == 'labeler' and (src.get('props') or {}).get('y_column'):
            y_labelers.append(src['id'])

    if not y_labelers:
        return None

    # BFS назад от Y-labeler'ов до первого timeshift'а.
    visited = set()
    queue = list(y_labelers)
    while queue:
        eid = queue.pop(0)
        if eid in visited:
            continue
        visited.add(eid)
        elem = elements[eid]
        if elem.get('nnType') == 'timeshift':
            props = elem.get('props') or {}
            return {
                'value': int(props.get('shift_value', 1)),
                'unit': props.get('shift_unit', 'days'),
            }
        if elem['type'] == 'input-signal':
            continue
        for conn in incoming_edges(project, eid):
            queue.append(conn['fromElement'])

    return None


def find_x_labeler(project):
    """Find the labeler whose out-0 feeds the model's X input."""
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
        raise ValueError("No X-producing labeler found feeding nn-template")

    for port, eid in candidates:
        if port == "in-1":
            return eid
    candidates.sort(key=lambda p: int(p[0].split("-")[1]))
    return candidates[0][1]


def collect_x_chain(project, end_id):
    """Backward DFS from end_id; returns element ids in forward (input→output) order.

    Y-only branches are never traversed because we start at the X-labeler
    and only walk its inputs. timeshift out-1 ports are also skipped.
    """
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
                continue
            walk(conn["fromElement"])

        out.append(eid)

    walk(end_id)
    return out


def _collect_dataset_inputs(project, dataset_eid, verbose=False):
    """Return the ordered list of input-signal names feeding the dataset.

    Mirrors the training backend (dataprocessing.load_input_data): incoming
    edges are sorted by toPort, then column order is determined by the FIRST
    occurrence of each signal NAME. If the same signal element is wired to
    several ports (an editing artifact / UI duplicate), or two different
    elements share the same props.name, only the first column survives —
    exactly as `signals_data[signal_name] = df` would do on the backend.
    """
    names = []
    seen_names = set()
    for conn in incoming_edges(project, dataset_eid):
        src_id = conn["fromElement"]
        src = project["elements"][src_id]
        if src["type"] != "input-signal":
            raise ValueError(
                "Unsupported: dataset input %r is not an input-signal "
                "(chained datasets are not handled)" % (src_id,)
            )
        name = (src["props"] or {}).get("name", "").strip()
        if not name:
            raise ValueError("input-signal %r has no name" % (src_id,))
        if name in seen_names:
            if verbose:
                print(
                    "  Note: signal %r feeds %s through multiple ports "
                    "(element %r); keeping the first occurrence only."
                    % (name, dataset_eid, src_id),
                    file=sys.stderr,
                )
            continue
        seen_names.add(name)
        names.append(name)
    return names


def _element_to_step(project, eid, verbose=False):
    """Turn one project element on the X path into a chain-step dict."""
    elem = project["elements"][eid]
    nn = elem["nnType"]
    props = elem.get("props") or {}

    if nn == "dataset":
        names = _collect_dataset_inputs(project, eid, verbose=verbose)
        ref_idx = int(props.get("reference_signal_index", 0) or 0)
        if ref_idx >= len(names):
            raise ValueError(
                "dataset %r: reference_signal_index=%d is out of range "
                "for the deduplicated input list %r"
                % (eid, ref_idx, names)
            )
        return {
            "type": "dataset",
            "ref_signal_index": ref_idx,
            "interpolation": props.get("interpolation", "linear"),
            "inputs": names,
        }

    if nn == "filter":
        return {"type": "filter", "rules": props.get("rules", [])}

    if nn == "timefilter":
        return {"type": "timefilter", "intervals": props.get("intervals", [])}

    if nn == "timeshift":
        return {
            "type": "timeshift",
            "shift_value": props.get("shift_value", 1),
            "shift_unit": props.get("shift_unit", "days"),
        }

    if nn == "labeler":
        return {
            "type": "labeler",
            "x_columns": props.get("x_columns", []),
            "y_column": None,  # X path only — y_column is irrelevant here
            "window_size": props.get("window_size", 1),
            "window_unit": props.get("window_unit", "rows"),
        }

    raise ValueError("Unsupported nnType on X path: %r" % (nn,))


def build_chain(project, exclude_training_only=True, verbose=False):
    x_labeler_id = find_x_labeler(project)
    chain_ids = collect_x_chain(project, x_labeler_id)
    steps = []
    for eid in chain_ids:
        elem = project["elements"][eid]
        if elem["type"] == "input-signal":
            continue  # consumed by the dataset step
        nn = elem["nnType"]
        if exclude_training_only and nn in TRAINING_ONLY_STEP_TYPES:
            if verbose:
                print(
                    "  Excluding training-only step: %s (%s)" % (eid, nn),
                    file=sys.stderr,
                )
            continue
        steps.append(_element_to_step(project, eid, verbose=verbose))
    return steps


def max_window_size(steps):
    m = 1
    for s in steps:
        if s["type"] == "labeler":
            m = max(m, int(s.get("window_size", 1)))
    return m


def collect_input_signal_meta(project, ordered_names):
    """Return per-signal metadata in the same order as `ordered_names`."""
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


def collect_y_signal_names(project):
    """Column names referenced as y_column by any labeler.

    Used purely for diagnostics: if a y-column references a signal that
    is NOT present in the X-chain's inputs, that signal is Y-only and is
    correctly excluded from the config.
    """
    out = set()
    for elem in project["elements"].values():
        if elem.get("nnType") != "labeler":
            continue
        y_col = (elem.get("props") or {}).get("y_column")
        if y_col:
            out.add(y_col)
    return out


# ============================================================================
# 2. Runtime discovery and version read
# ============================================================================

def _locate_runtime():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, RUNTIME_MODULE_NAME + ".py")
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Runtime module %r not found next to the generator. "
            "Expected at: %s" % (RUNTIME_MODULE_NAME, path)
        )
    return path


# ============================================================================
# 3. Config assembly
# ============================================================================

def build_config(project, rt_version,
                 resample_freq="30min",
                 datetime_format="%d.%m.%Y %H:%M:%S",
                 exclude_training_only=True,
                 verbose=False):
    proj_meta = project.get("project") or {}
    chain = build_chain(
        project,
        exclude_training_only=exclude_training_only,
        verbose=verbose,
    )
    if not chain:
        raise ValueError("Could not extract an X-chain from the project")
    if chain[0]["type"] != "dataset":
        raise ValueError(
            "The X-chain must start with a 'dataset' step; got %r"
            % (chain[0]["type"],)
        )

    ordered_names = list(chain[0]["inputs"])
    input_meta = collect_input_signal_meta(project, ordered_names)
    min_history = max_window_size(chain)

    # Diagnostic: Y-only signals referenced by any labeler.
    if verbose:
        y_names = collect_y_signal_names(project)
        y_only = sorted(y_names - set(ordered_names))
        if y_only:
            print(
                "  Y-only signal(s) referenced by labelers but not used in X: %r"
                % (y_only,),
                file=sys.stderr,
            )

    return {
        "config_version": CONFIG_VERSION,
        "runtime_version": rt_version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "code": proj_meta.get("code", ""),
            "description": proj_meta.get("description", "") or "",
        },
        "input_contract": {
            "resample_freq": resample_freq,
            "datetime_format": datetime_format,
            "min_history_rows": min_history,
        },
        "prediction_shift": find_prediction_shift(project), 
        "inputs": input_meta,
        "chain": chain,
    }

def _normalize_section_signs(obj):
    """Recursively replace '§' with '_' in every string inside obj.

    This keeps signal names consistent between the metadata section
    (inputs[].name) and every place the chain refers to signals by name
    (dataset.inputs, labeler.x_columns, filter.rules[].column).
    Index-based fields (ref_signal_index) are unaffected.
    """
    if isinstance(obj, str):
        return obj.replace("§", "_")
    if isinstance(obj, list):
        return [_normalize_section_signs(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _normalize_section_signs(v) for k, v in obj.items()}
    return obj


def generate(project_path, output_path=None, runtime_path=None,
             keep_training_steps=False, verbose=False):
    if runtime_path is None:
        runtime_path = _locate_runtime()

    project = load_project(project_path)
    cfg = build_config(
        project,
        '1.0',
        exclude_training_only=not keep_training_steps,
        verbose=verbose,
    )
    cfg = _normalize_section_signs(cfg)

    if output_path is None:
        code = cfg["project"]["code"] or "unknown_project"
        safe = "".join(
            c if (c.isalnum() or c in "._-") else "_" for c in code
        )
        output_path = "inference_%s.config.json" % safe

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    return output_path


def main():
    ap = argparse.ArgumentParser(
        description="Generate a JSON config describing a project's X-chain."
    )
    ap.add_argument("project", help="Path to the neural_network project JSON.")
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output config path (default: inference_<code>.config.json).",
    )
    ap.add_argument(
        "--runtime", default=None,
        help="Path to nn_inference_runtime.py (default: sibling).",
    )
    ap.add_argument(
        "--keep-training-steps", action="store_true",
        help="Do not exclude timefilter/timeshift from the X-chain "
             "(default: they are omitted as training-only).",
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print per-step diagnostics to stderr.",
    )
    args = ap.parse_args()

    out = generate(
        args.project,
        args.output,
        args.runtime,
        keep_training_steps=args.keep_training_steps,
        verbose=args.verbose,
    )
    print("Generated: %s" % out)


if __name__ == "__main__":
    main()