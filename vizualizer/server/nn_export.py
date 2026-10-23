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
from io import BytesIO
from export import prepare_code_for_system



import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Для работы без графического интерфейса на сервере
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from docx import Document
from docx.shared import Pt, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH


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
        "startup_condition": proj_meta.get("startup_condition"),
        "diagnostic_rule": proj_meta.get("diagnostic_rule"),
    }
    return _normalize_section_signs(cfg)

# =============================================================================
# Генерация схемы и Word-документа
# =============================================================================

def _generate_model_diagram(inputs, nn_kks, rule_kks=None, startup_kks=None):
    n = len(inputs)
    gap_y = 1.2 # Чуть больше расстояния между входами
    y_inputs = [-(i * gap_y) for i in range(n)]
    model_y = -(n - 1) * gap_y / 2 if n > 1 else 0
    startup_y = max(y_inputs) + 1.8 if y_inputs else 1.8

    # Координаты по X
    x_in = 0.5
    w_in = 4.5 # Увеличили ширину входа для KKS
    
    x_model = 6.0
    w_model = 1.5
    h_model = max(1.5, n * gap_y)
    
    x_out = 8.5
    w_out = 4.0 # Выход тоже сделаем достаточно широким, но это прямоугольник
    h_out = 0.6
    
    x_rule = 13.5
    w_rule = 3.0
    h_rule = 1.5

    fig_width = 18 # Холст шире
    fig_height = max(5, n * 1.4)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.set_xlim(0, 18)
    
    min_y = min(y_inputs) - 1.0 if y_inputs else -2.0
    max_y = startup_y + 1.0
    ax.set_ylim(min_y, max_y)
    ax.axis('off')

    def draw_pin(x, y, w, h, text):
        # Рисуем "кол" одним полигоном, чтобы не было внутренней линии
        tip_w = h * 0.8
        rect_w = w - tip_w
        
        points = [
            [x, y - h/2],             # нижний левый
            [x, y + h/2],             # верхний левый
            [x + rect_w, y + h/2],    # верхний правый (основание треугольника)
            [x + w, y],               # острие
            [x + rect_w, y - h/2],    # нижний правый (основание треугольника)
            [x, y - h/2]              # замыкаем
        ]
        ax.add_patch(patches.Polygon(points, closed=True, edgecolor='#4a90d9', facecolor='#e6f0fa', linewidth=1.5))
        ax.text(x + rect_w/2, y, text, ha='center', va='center', fontsize=9, fontweight='bold')

    def draw_rect(x, y, w, h, text, color='#10b981'):
        ax.add_patch(patches.Rectangle((x, y - h/2), w, h, linewidth=1.5, edgecolor=color, facecolor='#d1fae5'))
        ax.text(x + w/2, y, text, ha='center', va='center', fontsize=10, fontweight='bold')

    # 1. Входные сигналы
    for i, y in enumerate(y_inputs):
        draw_pin(x_in, y, w_in, 0.5, inputs[i])
        ax.annotate('', xy=(x_model, model_y), xytext=(x_in + w_in, y),
                    arrowprops=dict(arrowstyle='->', color='#6b7280', lw=1.5, connectionstyle="arc3,rad=0.1" if y != model_y else "arc3,rad=0"))

    # 2. Модель
    ax.add_patch(patches.Rectangle((x_model, model_y - h_model/2), w_model, h_model, linewidth=2, edgecolor='#f97316', facecolor='#ffedd5'))
    ax.text(x_model + w_model/2, model_y, "МОДЕЛЬ", ha='center', va='center', fontsize=12, fontweight='bold', color='#c2410c')

    # 3. Выход модели (Прямоугольник)
    draw_rect(x_out, model_y, w_out, h_out, nn_kks)
    ax.annotate('', xy=(x_out, model_y), xytext=(x_model + w_model, model_y),
                arrowprops=dict(arrowstyle='->', color='#6b7280', lw=1.5))

    # 4. Правило и условие
    if rule_kks:
        ax.add_patch(patches.Rectangle((x_rule, model_y - h_rule/2), w_rule, h_rule, linewidth=2, edgecolor='#8b5cf6', facecolor='#ede9fe'))
        ax.text(x_rule + w_rule/2, model_y, f"ПРАВИЛО\n{rule_kks}", ha='center', va='center', fontsize=10, fontweight='bold', color='#6d28d9')
        
        ax.annotate('', xy=(x_rule, model_y), xytext=(x_out + w_out, model_y),
                    arrowprops=dict(arrowstyle='->', color='#6b7280', lw=1.5))
                    
        if startup_kks:
            draw_pin(x_in, startup_y, w_in, 0.5, startup_kks)
            ax.text(x_in + w_in/2, startup_y + 0.4, "Условие активации", ha='center', va='bottom', fontsize=10, color='#4a90d9')
            
            # Стрелка от условия к правилу (вход сверху)
            ax.annotate('', xy=(x_rule + w_rule/2, model_y + h_rule/2), xytext=(x_in + w_in, startup_y),
                        arrowprops=dict(arrowstyle='->', color='#6b7280', lw=1.5, connectionstyle="arc3,rad=-0.2"))

    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight', transparent=True, pad_inches=0.1)
    plt.close(fig)
    buf.seek(0)
    return buf

def _build_model_docx_bytes(project_payload, config_data, startup_data=None, rule_data=None):
    """Генерирует DOCX с описанием модели."""
    doc = Document()
    
    section = doc.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(3)
    section.right_margin = Cm(1.5)

    def add_text(text="", bold=False):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        pf = p.paragraph_format
        pf.first_line_indent = Cm(1.25)
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.5
        run = p.add_run(text)
        run.font.name = "Times New Roman"
        run.font.size = Pt(12)
        run.bold = bold
        return p

    def add_multiline(text="", bold=False):
        lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if not lines:
            add_text("", bold=bold)
            return
        for line in lines:
            add_text(line, bold=bold)

    def add_heading(text):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pf = p.paragraph_format
        pf.space_before = Pt(12)
        pf.space_after = Pt(6)
        pf.line_spacing = 1.5
        run = p.add_run(text)
        run.font.name = "Times New Roman"
        run.font.size = Pt(14)
        run.bold = True
        return p

    proj_meta = project_payload.get("project", {})
    code = proj_meta.get("code", "")
    desc = proj_meta.get("description", "")
    metrics = proj_meta.get("metrics", {}) or {}

    # ==================== РАЗДЕЛ 1: МОДЕЛЬ ====================
    add_heading("1. Модель")

    add_text("KKS модели:", bold=True)
    add_multiline(code)

    add_text("Описание:", bold=True)
    add_multiline(desc)

    add_text("Основные метрики:", bold=True)
    mae = metrics.get("mae")
    rmse = metrics.get("rmse")
    mse = metrics.get("mse")
    r2 = metrics.get("r2")
    bias = metrics.get("bias")
    std = metrics.get("std")
    p95 = metrics.get("p95")
    p99 = metrics.get("p99")
    
    # Форматируем аккуратно
    f_mae = f"{mae:.4g}" if mae is not None else "—"
    f_rmse = f"{rmse:.4g}" if rmse is not None else "—"
    f_mse = f"{mse:.4g}" if mse is not None else "—"
    f_r2 = f"{r2:.4f}" if r2 is not None else "—"
    f_bias = f"{bias:.4g}" if bias is not None else "—"
    f_std = f"{std:.4g}" if std is not None else "—"
    f_p95 = f"{p95:.4g}" if p95 is not None else "—"
    f_p99 = f"{p99:.4g}" if p99 is not None else "—"

    metrics_text_1 = f"MAE: {f_mae};  RMSE: {f_rmse};  MSE: {f_mse};  R²: {f_r2}"
    metrics_text_2 = f"Bias (Среднее): {f_bias};  Стд. отклонение: {f_std}"
    metrics_text_3 = f"95-й перцентиль (по модулю): {f_p95};  99-й перцентиль (по модулю): {f_p99}"
    
    add_multiline(metrics_text_1)
    add_multiline(metrics_text_2)
    add_multiline(metrics_text_3)

    add_text("Схема модели:", bold=True)
    inputs = [inp["name"] for inp in config_data.get("inputs", [])]
    if inputs:
        nn_kks = proj_meta.get("code", "")
        rule_kks = rule_data.get("kks") if rule_data else None
        startup_kks = startup_data.get("kks") if startup_data else None
        img_buf = _generate_model_diagram(inputs, nn_kks, rule_kks, startup_kks)
        # Центрируем картинку без красной строки
        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.paragraph_format.first_line_indent = Cm(0)
        # Делаем картинку шириной во всю доступную область (16 см максимум, но 15 см безопаснее)
        p_img.add_run().add_picture(img_buf, width=Inches(6.0))
    else:
        add_text("(нет входных сигналов)")

    add_text("Используемые параметры:", bold=True)
    if config_data.get("inputs"):
        for inp in config_data["inputs"]:
            name = inp.get("name", "")
            d = inp.get("description", "")
            line = f"- {name}"
            if d:
                line += f" — {d}"
            add_multiline(line)
    else:
        add_text("- (нет)")

    # ==================== РАЗДЕЛ 2: УСЛОВИЯ ЗАПУСКА ====================
    if startup_data:
        add_heading("2. Условия запуска")
        
        add_text("KKS условия:", bold=True)
        add_multiline(startup_data['kks'])
        
        if startup_data.get("description"):
            add_text("Описание условия:", bold=True)
            add_multiline(startup_data['description'])

        # --- НОВОЕ: Адаптация кода ---
        if startup_data.get("code"):
            add_text("Формула:", bold=True)
            # Собираем имена входных сигналов для правильной обработки
            input_names = [inp["name"] for inp in startup_data.get("inputs", [])]
            prepared_code = prepare_code_for_system(startup_data["code"], input_names)
            add_multiline(prepared_code)
            # ------------------------------
            
        add_text("Используемые параметры условия:", bold=True)
        if startup_data.get("inputs"):
            for inp in startup_data["inputs"]:
                name = inp.get("name", "")
                d = inp.get("description", "")
                line = f"- {name}"
                if d:
                    line += f" — {d}"
                add_multiline(line)
        else:
            add_text("- (нет)")

    # ==================== РАЗДЕЛ 3: ДИАГНОСТИЧЕСКОЕ ПРАВИЛО ====================
    if rule_data:
        add_heading("3. Диагностическое правило")
        
        # Точно копируем логику из export.py _build_rules_docx_bytes
        add_text(f"Правило ({rule_data['kks']}):", bold=True)

        add_text("Краткое описание:", bold=True)
        add_multiline(rule_data.get("description", ""))

        add_text("Причина:", bold=True)
        add_multiline(rule_data.get("possibleCause", ""))

        add_text("Логика определения:", bold=True)
        add_text("") # Пустая строка для отступа

        add_text("Формула:", bold=True)
        if rule_data.get("code"):
            # --- НОВОЕ: Адаптация кода ---
            input_names = [inp["name"] for inp in rule_data.get("inputs", [])]
            prepared_code = prepare_code_for_system(rule_data["code"], input_names)
            add_multiline(prepared_code)
            # ------------------------------
        else:
            add_text("- (пусто)")

        add_text("Используемые параметры:", bold=True)
        if rule_data.get("inputs"):
            for inp in rule_data["inputs"]:
                name = inp.get("name", "")
                d = inp.get("description", "").replace("\n", "; ")
                line = f"- {name}"
                if d:
                    line += f" — {d}"
                add_multiline(line)
        else:
            add_text("- (нет)")

        add_text("Время срабатывания:", bold=True)
        add_text("") # Пустая строка

        add_text("Методические указания:", bold=True)
        add_multiline(rule_data.get("guidelines", ""))

    out = BytesIO()
    doc.save(out)
    return out.getvalue()


# =============================================================================
# Упаковка в ZIP
# =============================================================================

def build_zip(project_payload: dict,
              model_path: str,
              model_meta_path: str,
              base_name: str,
              startup_data: dict = None,
              rule_data: dict = None) -> bytes:
    """
    Возвращает байты ZIP-архива.
    """
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"Модель не найдена: {model_path}")
    if not os.path.isfile(model_meta_path):
        raise FileNotFoundError(f"Метаданные модели не найдены: {model_meta_path}")

    cfg = build_config(project_payload)
    
    # Генерируем Word
    docx_bytes = _build_model_docx_bytes(project_payload, cfg, startup_data, rule_data)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(model_path, arcname=f"{base_name}.keras")
        zf.write(model_meta_path, arcname=f"{base_name}_meta.json")
        zf.writestr(
            f"{base_name}.config.json",
            json.dumps(cfg, ensure_ascii=False, indent=2)
        )
        # Добавляем Word
        zf.writestr(f"{base_name}_описание.docx", docx_bytes)
        
    buf.seek(0)
    return buf.getvalue()