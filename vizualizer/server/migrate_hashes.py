import os
import sys
import json
import argparse

# Принудительный вывод в консоль (stderr), чтобы обойти любые буферы
sys.stderr.write(">>> Старт скрипта миграции...\n")
sys.stderr.flush()

try:
    from dataprocessing import compute_hash, get_meta_path, get_datasets_dir
    sys.stderr.write(">>> Модуль dataprocessing успешно загружен.\n")
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write(f"❌ Ошибка импорта dataprocessing: {e}\n")
    sys.stderr.flush()
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

def load_settings():
    if not os.path.exists(SETTINGS_PATH):
        raise FileNotFoundError(f"Файл настроек не найден: {SETTINGS_PATH}")
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def migrate_element(element_id: str, elements: dict, connections: list, config: str, project_code: str, visited: set):
    if element_id in visited:
        return
    visited.add(element_id)

    elem = elements[element_id]
    elem_type = elem.get("nnType") or elem.get("type")

    # Пропускаем элементы без файлов
    if elem_type in ('input-signal', 'nn-settings', 'group'):
        return

    # 1. Рекурсивно мигрируем входы
    input_hashes = {}
    for conn in connections:
        if conn['toElement'] == element_id:
            src_id = conn['fromElement']
            src_elem = elements[src_id]
            nn_type = src_elem.get('nnType') or src_elem.get('type')

            if nn_type == 'input-signal':
                input_hashes[src_id] = 'signal_fixed'
            elif nn_type == 'nn-settings':
                input_hashes[src_id] = compute_hash(src_elem.get('props', {}))
            else:
                migrate_element(src_id, elements, connections, config, project_code, visited)
                src_meta_path = get_meta_path(config, project_code, src_id)
                if os.path.exists(src_meta_path):
                    with open(src_meta_path, 'r') as f:
                        src_meta = json.load(f)
                    input_hashes[src_id] = src_meta.get('hash', '')
                else:
                    input_hashes[src_id] = ''

    # 2. Читаем meta.json текущего элемента
    meta_path = get_meta_path(config, project_code, element_id)
    if not os.path.exists(meta_path):
        sys.stderr.write(f"    ⚠️  {element_id} ({elem_type}): meta.json не найден, пропускаем.\n")
        sys.stderr.flush()
        return

    with open(meta_path, 'r') as f:
        meta = json.load(f)

    outputs = meta.get('outputs', {})

    # 3. Собираем mtime файлов
    out_mtimes = []
    for rel in outputs.values():
        if rel:
            full_path = os.path.join(get_datasets_dir(config), rel)
            if os.path.exists(full_path):
                st = os.stat(full_path)
                out_mtimes.append(f"{st.st_mtime_ns}_{st.st_size}")
            else:
                out_mtimes.append("missing")

    # 4. Считаем новый final_hash
    final_hash = compute_hash({
        'props': elem.get('props', {}),
        'input_hashes': input_hashes,
        'type': elem_type,
        'out_mtimes': out_mtimes
    })

    # 5. Записываем обратно
    meta['hash'] = final_hash
    
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    sys.stderr.write(f"    ✅ {element_id} ({elem_type}): хэш обновлен.\n")
    sys.stderr.flush()

def migrate_project(project_path: str, config: str):
    sys.stderr.write(f"\n📂 Обработка проекта: {os.path.basename(project_path)}\n")
    sys.stderr.flush()
    
    with open(project_path, 'r', encoding='utf-8') as f:
        project_data = json.load(f)

    project_code = project_data.get('project', {}).get('code', '')
    if not project_code:
        sys.stderr.write("  ❌ В проекте отсутствует project.code, пропускаем.\n")
        return False

    sys.stderr.write(f"  🔑 Код проекта: {project_code}\n")
    sys.stderr.flush()
    
    elements = project_data.get('elements', {})
    connections = project_data.get('connections', [])
    
    visited = set()
    for elem_id in elements.keys():
        migrate_element(elem_id, elements, connections, config, project_code, visited)
        
    return True

def main():
    parser = argparse.ArgumentParser(description="Миграция хэшей всех нейросетевых проектов в конфигурации")
    parser.add_argument("--config", required=True, help="Имя конфигурации (например, TEC_20)")
    args = parser.parse_args()

    try:
        settings = load_settings()
        base_folder = settings.get("projectDataFolder")
        if not base_folder:
            raise ValueError("В settings.json не настроен projectDataFolder")
        
        if not os.path.isabs(base_folder):
            base_folder = os.path.normpath(os.path.join(BASE_DIR, base_folder))
            
        project_dir = os.path.join(base_folder, args.config)
        sys.stderr.write(f">>> Ищем проекты в папке: {project_dir}\n")
        sys.stderr.flush()
        
        if not os.path.isdir(project_dir):
            raise FileNotFoundError(f"Папка проектов не найдена: {project_dir}")

        success_count = 0
        skip_count = 0
        
        for fname in os.listdir(project_dir):
            if not fname.endswith(".json"):
                continue
                
            path = os.path.join(project_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                # Нас интересуют только проекты обучения
                if data.get("project", {}).get("type") == "neural_network":
                    if migrate_project(path, args.config):
                        success_count += 1
                else:
                    skip_count += 1
            except Exception as e:
                sys.stderr.write(f"❌ Ошибка чтения файла {fname}: {e}\n")
                sys.stderr.flush()

        sys.stderr.write(f"\n✨ Завершено! Успешно обновлено проектов: {success_count}. Пропущено (не neural_network): {skip_count}.\n")
        sys.stderr.flush()

    except Exception as e:
        sys.stderr.write(f"\n❌ Критическая ошибка: {e}\n")
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()