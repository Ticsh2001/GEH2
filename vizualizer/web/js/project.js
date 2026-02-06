/**
 * Модуль управления проектом (сохранение, загрузка)
 * project.js
 */

// --- миграция id: '-' -> '_' с обновлением всех ссылок ---
function migrateIdsDashToUnderscore() {
  const map = {};

  // 1) собрать map старых id → новых
  Object.values(AppState.elements).forEach(el => {
    if (typeof el.id === 'string' && el.id.includes('-')) {
      map[el.id] = el.id.replace(/-/g, '_');
    }
  });

  if (!Object.keys(map).length) return;

  // 2) DOM id + data-element
  Object.entries(map).forEach(([oldId, newId]) => {
    const dom = document.getElementById(oldId);
    if (dom) dom.id = newId;

    if (dom) {
      dom.querySelectorAll('[data-element]').forEach(p => {
        if (p.dataset.element === oldId) p.dataset.element = newId;
      });
    }
  });

  // 3) AppState.elements ключи
  Object.entries(map).forEach(([oldId, newId]) => {
    const el = AppState.elements[oldId];
    if (!el) return;
    el.id = newId;
    AppState.elements[newId] = el;
    delete AppState.elements[oldId];
  });

  // 4) connections
  AppState.connections.forEach(c => {
    if (map[c.fromElement]) c.fromElement = map[c.fromElement];
    if (map[c.toElement]) c.toElement = map[c.toElement];
  });

  // 5) формулы
  const escapeRegex = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  Object.values(AppState.elements).forEach(el => {
    if (el.type === 'formula' && el.props?.expression) {
      let expr = el.props.expression;
      Object.entries(map).forEach(([oldId, newId]) => {
        const re = new RegExp(`(^|[^A-Za-z0-9_])${escapeRegex(oldId)}(?![A-Za-z0-9_])`, 'g');
        expr = expr.replace(re, (m, p1) => `${p1}${newId}`);
      });
      el.props.expression = expr;
    }
  });

  // 6) selected + modal
  if (map[AppState.selectedElement]) AppState.selectedElement = map[AppState.selectedElement];
  const modal = document.getElementById('modal-overlay');
  if (modal && map[modal.dataset.elementId]) modal.dataset.elementId = map[modal.dataset.elementId];
}

const Project = {
    /**
     * Инициализация
     */
        /**
     * Инициализация
     */
init() {
    document.getElementById('btn-new').addEventListener('click', () => this.newProject());
    document.getElementById('btn-save').addEventListener('click', () => this.saveProject());
    document.getElementById('btn-load').addEventListener('click', () => this.openProjectListModal());
    document.getElementById('btn-project-settings').addEventListener('click', () => {
        Modal.showProjectPropertiesModal();
    });

    // Работа с модалкой выбора проекта
    this.projectList = [];
    this.filteredProjectList = [];
    this.selectedProjectFilename = null;
    this.selectedProjectSource = 'projects';

    document.getElementById('project-cancel').addEventListener('click', () => this.closeProjectListModal());
    document.getElementById('project-refresh').addEventListener('click', () => this.refreshProjectList());

    document.getElementById('project-load').addEventListener('click', () => {
        if (this.selectedProjectFilename) {
            this.loadProjectFromList(this.selectedProjectFilename, this.selectedProjectSource);
        }
    });

    document.getElementById('project-search').addEventListener('input', (event) => {
        this.filterProjectList(event.target.value);
    });
},

    /**
     * Новый проект
     */
    newProject() {
        if (Object.keys(AppState.elements).length > 0) {
            if (!confirm('Создать новый проект? Несохранённые изменения будут потеряны.')) {
                return;
            }
        }

        document.getElementById('workspace').innerHTML = '';
        document.getElementById('connections-svg').innerHTML = '';

        resetState();
        Viewport.updateTransform();
    },

        /**
     * Запрос имени файла и загрузка с сервера
     */
    async loadProjectPrompt() {
        const filename = window.prompt(
            "Введите имя файла проекта для загрузки (с сервера). Пример: scheme_logic.json", 
            AppState.project.code ? `${AppState.project.code}_${AppState.project.type}.json` : "scheme_type.json"
        );
        
        if (!filename) return; // Отмена

        try {
            // Используем обертку из Settings.js для запроса к /api/project/load
            const data = await Settings.loadProject(filename);
            
            // Если загрузка успешна, вызываем основную функцию обработки данных
            this._processLoadedData(data);
            alert(`Проект "${filename}" успешно загружен с сервера.`);

        } catch (error) {
            console.error('Ошибка загрузки проекта:', error);
            alert(`Ошибка загрузки проекта: ${error.message}`);
        }
    },

    /**
     * Сохранение проекта
     */
        async saveProject() {
    // 1. Проверяем свойства проекта
    if (!AppState.project.code) {
        Modal.showProjectPropertiesModal();
        alert('Пожалуйста, укажите код проекта перед сохранением.');
        return;
    }

    // Обновляем размеры рамок перед сохранением
    updateFrameChildren();
    // нормализуем id
    migrateIdsDashToUnderscore();

    // подчистим связи прямо перед сохранением
    const exists = (id) => !!AppState.elements[id];
    AppState.connections = (AppState.connections || [])
      .map(c => ({
        ...c,
        fromElement: exists(c.fromElement) ? c.fromElement : c.fromElement.replace(/-/g, '_'),
        toElement: exists(c.toElement) ? c.toElement : c.toElement.replace(/-/g, '_')
      }))
      .filter(c => exists(c.fromElement) && exists(c.toElement))
      .filter((c, idx, arr) => {
        const key = `${c.fromElement}|${c.fromPort}|${c.toElement}|${c.toPort}`;
        return arr.findIndex(x =>
          `${x.fromElement}|${x.fromPort}|${x.toElement}|${x.toPort}` === key
        ) === idx;
      });

    // Генерируем код заранее
    let generatedCode = '';
    if (typeof CodeGen !== 'undefined' && typeof CodeGen.generate === 'function') {
        try {
            generatedCode = CodeGen.generate() || '';
        } catch (err) {
            console.error('Code generation failed:', err);
        }
    }

    // НОВОЕ: получаем состояние визуализатора перед сохранением
    let visualizerState = AppState.project?.visualizer_state || null;
    
    if (AppState.currentVisualizerToken) {
        try {
            const freshState = await App.fetchVisualizerState();
            if (freshState) {
                visualizerState = freshState;
                console.log('Состояние визуализатора обновлено перед сохранением');
            }
        } catch (err) {
            console.warn('Не удалось получить состояние визуализатора:', err);
        }
    }

    // 2. Сборка объекта проекта
    const project = {
        version: '1.0',
        project: AppState.project,
        elements: AppState.elements,
        connections: AppState.connections,
        counter: AppState.elementCounter,
        viewport: {
            zoom: AppState.viewport.zoom,
            panX: AppState.viewport.panX,
            panY: AppState.viewport.panY
        },
        code: generatedCode,
        visualizer_state: visualizerState  // НОВОЕ: сохраняем состояние визуализатора
    };

    const filename = `${AppState.project.code || 'scheme'}_${AppState.project.type}.json`;
    const target = AppState.project.type === PROJECT_TYPE.TEMPLATE ? 'templates' : 'projects';

    // 3. Сохранение на сервер
    try {
        await Settings.saveProject(filename, project, target);
        
        // НОВОЕ: обновляем состояние в AppState после успешного сохранения
        AppState.project.visualizer_state = visualizerState;
        
        alert(`Проект успешно сохранен на сервере как: ${filename}`);
    } catch (error) {
        console.error('Ошибка сохранения проекта:', error);
        alert(`Ошибка сохранения проекта: ${error.message}`);
    }
},

    async showProjectList() {
        try {
            const result = await Settings.listProjects(); // нужно реализовать в settings.js
            const list = result.projects || [];

            if (list.length === 0) {
                alert('Проекты в папке не найдены.');
                return;
            }

            const choice = window.prompt(
                'Список проектов:\n' + list.map((p, i) => `${i + 1}. ${p.code || p.filename} — ${p.description}`).join('\n') +
                '\n\nВведите номер проекта для загрузки:',
                '1'
            );
            const index = parseInt(choice, 10) - 1;
            if (isNaN(index) || !list[index]) return;

            await this.loadProjectByFilename(list[index].filename);
        } catch (error) {
            console.error(error);
            alert('Не удалось получить список проектов: ' + error.message);
        }
    },

    async loadProjectByFilename(filename) {
        try {
            const data = await Settings.loadProject(filename);
            this._processLoadedData(data);
            alert(`Проект "${filename}" загружен.`);
        } catch (error) {
            console.error(error);
            alert('Ошибка загрузки проекта: ' + error.message);
        }
    },

    openProjectListModal() {
  const modal = document.getElementById('modal-project-list');
  modal.classList.remove('hidden');
  document.body.classList.add('modal-open'); // если есть такой класс для блокировки скролла
  this.refreshProjectList();
},

closeProjectListModal() {
  const modal = document.getElementById('modal-project-list');
  modal.classList.add('hidden');
  document.body.classList.remove('modal-open');
},

async refreshProjectList() {
  const tbody = document.getElementById('project-list-body');
  tbody.innerHTML = `<tr><td colspan="4" class="project-list__empty">Загрузка…</td></tr>`;
  try {
    const result = await Settings.listProjects();
    this.projectList = result.projects || [];
    this.filteredProjectList = [...this.projectList];
    this.renderProjectList();
  } catch (err) {
    console.error(err);
    tbody.innerHTML = `<tr><td colspan="4" class="project-list__empty">Ошибка: ${err.message}</td></tr>`;
  }
},

renderProjectList() {
  const tbody = document.getElementById('project-list-body');
  const loadBtn = document.getElementById('project-load');
  loadBtn.disabled = true;
  this.selectedProjectFilename = null;
  this.selectedProjectSource = 'projects'; // ← Сброс по умолчанию

  if (!this.filteredProjectList.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="project-list__empty">Ничего не найдено</td></tr>`;
    return;
  }

  tbody.innerHTML = '';
  this.filteredProjectList.forEach((item) => {
    const tr = document.createElement('tr');
    tr.dataset.source = item.source || 'projects';          // ← НОВОЕ
    tr.innerHTML = `
      <td>${item.code || ''}</td>
      <td>${item.description || ''}</td>
      <td>${item.type || ''}</td>
      <td>${item.filename || ''}</td>
    `;
    tr.addEventListener('click', () => {
      this.highlightRow(tr);
      this.selectedProjectFilename = item.filename;
      this.selectedProjectSource = item.source || 'projects'; // ← НОВОЕ
      loadBtn.disabled = false;
    });
    tr.addEventListener('dblclick', () => {
      this.highlightRow(tr);
      this.selectedProjectFilename = item.filename;
      this.selectedProjectSource = item.source || 'projects'; // ← НОВОЕ
      loadBtn.disabled = false;
      this.loadProjectFromList(item.filename, this.selectedProjectSource);
    });
    tbody.appendChild(tr);
  });
},

highlightRow(row) {
  const tbody = row.parentElement;
  [...tbody.children].forEach((tr) => tr.classList.remove('selected'));
  row.classList.add('selected');
},


// Фильтр по поисковой строке
filterProjectList(query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) {
    this.filteredProjectList = [...this.projectList];
  } else {
    this.filteredProjectList = this.projectList.filter((item) => {
      return [
        item.filename,
        item.code,
        item.description,
        item.type
      ].some((field) => (field || '').toLowerCase().includes(q));
    });
  }
  this.renderProjectList();
},

async loadProjectFromList(filename, source = 'projects') {
  try {
    const data = await Settings.loadProject(filename, source);
    this._processLoadedData(data);
    this.closeProjectListModal();
    alert(`Проект "${filename}" успешно загружен.`);
  } catch (error) {
    console.error(error);
    alert('Ошибка загрузки проекта: ' + error.message);
  }
},






    /**
     * Загрузка проекта
     */
_processLoadedData(data) {
  try {
    document.getElementById('workspace').innerHTML = '';
    document.getElementById('connections-svg').innerHTML = '';
    resetState();

    if (data.project) {
      AppState.project = { ...AppState.project, ...data.project };
    }

    // НОВОЕ: загружаем состояние визуализатора
    if (data.visualizer_state) {
      AppState.project.visualizer_state = data.visualizer_state;
      console.log('Загружено состояние визуализатора:', data.visualizer_state);
    } else {
      AppState.project.visualizer_state = null;
    }
    
    // НОВОЕ: сбрасываем токен предыдущей сессии визуализатора
    AppState.currentVisualizerToken = null;

    AppState.elementCounter = data.counter || 0;

    if (data.viewport) {
      AppState.viewport.zoom = data.viewport.zoom || 1;
      AppState.viewport.panX = data.viewport.panX || 0;
      AppState.viewport.panY = data.viewport.panY || 0;
    }

    const elements = data.elements || {};
    Object.values(elements)
      .filter(e => e.type === 'output-frame')
      .forEach(elemData => {
        Elements.addElement(
          elemData.type,
          elemData.x,
          elemData.y,
          elemData.props,
          elemData.id,
          elemData.width,
          elemData.height
        );
      });

    Object.values(elements)
      .filter(e => e.type !== 'output-frame')
      .forEach(elemData => {
        Elements.addElement(
          elemData.type,
          elemData.x,
          elemData.y,
          elemData.props,
          elemData.id,
          elemData.width,
          elemData.height
        );
      });

    AppState.connections = data.connections || [];

    // Миграция id: '-' -> '_'
    migrateIdsDashToUnderscore();

    // очистка соединений: удалить битые и дубликаты
    const exists = (id) => !!AppState.elements[id];

    AppState.connections = (AppState.connections || [])
      .filter(c => exists(c.fromElement) && exists(c.toElement))
      .filter((c, idx, arr) => {
        const key = `${c.fromElement}|${c.fromPort}|${c.toElement}|${c.toPort}`;
        return arr.findIndex(x =>
          `${x.fromElement}|${x.fromPort}|${x.toElement}|${x.toPort}` === key
        ) === idx;
      });

    // корректно восстанавливаем счётчик
    const counterFromFile = Number(data.counter);
    AppState.elementCounter = Number.isFinite(counterFromFile) ? counterFromFile : 0;

    const maxIdSuffix = Object.values(AppState.elements).reduce((max, el) => {
        if (!el?.id) return max;
        const match = String(el.id).match(/_(\d+)$/);
        const num = match ? parseInt(match[1], 10) : NaN;
        return Number.isFinite(num) ? Math.max(max, num) : max;
    }, 0);

    AppState.elementCounter = Math.max(AppState.elementCounter, maxIdSuffix);

    Viewport.updateTransform();
    Connections.drawConnections();
    updateFrameChildren();

  } catch (e) {
    alert('Ошибка обработки данных проекта: ' + e.message);
    console.error(e);
  }
}
};