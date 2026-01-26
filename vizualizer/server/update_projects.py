# server/update_projects.py
import os, json, hashlib, subprocess, tempfile, shutil # Импортируем shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HASH_PATH = os.path.join(BASE_DIR, ".formula_templates.hash")
NODE_SCRIPT = os.path.normpath(os.path.join(BASE_DIR, "..", "web", "js", "regenerate_code.js"))

def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def update_projects_if_templates_changed(project_dir: str, templates_path: str):
    if not os.path.isdir(project_dir):
        print(f"[WARN] project dir not found: {project_dir}")
        return
    if not os.path.exists(templates_path):
        print(f"[WARN] templates not found: {templates_path}")
        return
    if not os.path.exists(NODE_SCRIPT):
        raise RuntimeError(f"Node script not found: {NODE_SCRIPT}")

    new_hash = _file_hash(templates_path)
    old_hash = None
    if os.path.exists(HASH_PATH):
        with open(HASH_PATH, "r", encoding="utf-8") as f:
            old_hash = f.read().strip()

    if new_hash == old_hash:
        print("[OK] formula_templates.json not changed")
        return

    print("[INFO] Templates changed -> regenerating project codes...")

    for fname in os.listdir(project_dir):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(project_dir, fname)
        try:
            result = subprocess.run(
                ["node", NODE_SCRIPT, path, templates_path],
                check=True,
                capture_output=True,
                text=True
            )
            
            if not result.stdout.strip():
                print(f"[ERROR] node returned empty stdout for {fname}")
                if result.stderr.strip():
                    print(f"[STDERR]\n{result.stderr}")
                raise ValueError(f"Empty stdout from node script for {fname}")
            
            updated = json.loads(result.stdout)
            
            # Используем NamedTemporaryFile с директорией, если она доступна,
            # или полагаемся на стандартное поведение (создание на том же разделе)
            # Если не помогает, нужно явно указывать tempfile.TemporaryDirectory
            with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", dir=project_dir) as tmp:
                json.dump(updated, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            
            try:
                # Копируем файл, а потом удаляем временный
                shutil.copy2(tmp_path, path) # copy2 сохраняет метаданные
                os.remove(tmp_path)
                print(f"[OK] updated {fname}")
            except Exception as e:
                print(f"[ERROR] failed to copy/remove temporary file for {fname}: {e}")
                # Попытка очистки временного файла, если он остался
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise

        except subprocess.CalledProcessError as e:
            print(f"[ERROR] node failed for {fname}: exit code {e.returncode}")
            if e.stderr:
                print(f"[STDERR]\n{e.stderr}")
            if e.stdout:
                print(f"[STDOUT]\n{e.stdout}")
            raise
        except json.JSONDecodeError as e:
            print(f"[ERROR] json parse failed for {fname}: {e}")
            print(f"[STDOUT]\n{result.stdout[:500]}")
            if result.stderr:
                print(f"[STDERR]\n{result.stderr}")
            raise
        except Exception as e:
            print(f"[ERROR] failed {fname}: {e}")
            raise

    with open(HASH_PATH, "w", encoding="utf-8") as f:
        f.write(new_hash)

    print("[OK] All projects regenerated.")