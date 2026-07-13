// neural_app.js — Конструктор нейросетей (общий тип nn-layer)
const NeuralApp = {
    blockParams: {},
    currentMode: 'design',

    async init() {
        this.initUser();
        AppState.project.type = PROJECT_TYPE.NEURAL_TEMPLATE;
        Settings.init().catch(console.error);
        this.loadConfigurations().catch(console.error);
        await this.fetchBlockParams();
        this.applyMode('design');  // начальный режим
        this.setupGlobalMouseHandlers();
        this.setupContextMenu();
        this.setupWorkspaceClick();
        this.setupMultiSelection();
        Viewport.init();
        Modal.init();

        document.getElementById('btn-new').addEventListener('click', () => this.newProject());
        document.getElementById('btn-save').addEventListener('click', () => this.saveProject());
        document.getElementById('btn-load').addEventListener('click', () => Project.openProjectListModal());
        document.getElementById('btn-generate-code').addEventListener('click', () => {
            alert('Структура сети:\n' + this.generateStructureString());
        });
        document.getElementById('btn-project-settings').addEventListener('click', () => {
            Modal.showProjectPropertiesModal();
        });

        document.getElementById('project-cancel').addEventListener('click', () => Project.closeProjectListModal());
        document.getElementById('project-refresh').addEventListener('click', () => Project.refreshProjectList());
        document.getElementById('project-load').addEventListener('click', () => {
            if (Project.selectedProjectFilename) {
                Project.loadProjectFromList(Project.selectedProjectFilename, Project.selectedProjectSource);
            }
        });
        const searchInput = document.getElementById('project-search');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => Project.filterProjectList(e.target.value));
        }

        // ВАЖНО: обработчики переключения режимов
        document.getElementById('btn-mode-design').addEventListener('click', () => this.switchMode('design'));
        document.getElementById('btn-mode-training').addEventListener('click', () => this.switchMode('training'));
    },

    applyMode(mode) {
        this.currentMode = mode;
        document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
        const btn = document.getElementById(`btn-mode-${mode}`);
        if (btn) btn.classList.add('active');

        AppState.project.type = mode === 'design' ? PROJECT_TYPE.NEURAL_TEMPLATE : PROJECT_TYPE.NEURAL_NETWORK;

        this.buildPalette();
        this.setupPaletteDragDrop();
    },

    switchMode(mode) {
        if (mode === this.currentMode) return;
        if (Object.keys(AppState.elements).length > 0) {
            if (!confirm('При переключении режима все несохранённые изменения пропадут. Продолжить?')) {
                return;
            }
        }
        // Очищаем рабочую область
        document.getElementById('workspace').innerHTML = '';
        document.getElementById('connections-svg').innerHTML = '';
        resetState();
        this.applyMode(mode);
        Viewport.updateTransform();
    },
    initUser() {
        let username = localStorage.getItem('lse_username');
        if (!username) {
            username = prompt('Представьтесь, пожалуйста:')?.trim() || 'Аноним';
            localStorage.setItem('lse_username', username);
        }
        AppState.currentUser = username;
        document.getElementById('user-badge-name').textContent = username;
    },

    async loadConfigurations() {
        try {
            const resp = await fetch('/api/configurations');
            const data = await resp.json();
            const configs = data.configurations || [];
            const select = document.getElementById('config-select');
            select.innerHTML = '';
            if (configs.length === 0) {
                select.innerHTML = '<option value="">Нет конфигураций</option>';
                AppState.currentConfig = '';
                return;
            }
            configs.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c;
                opt.textContent = c;
                select.appendChild(opt);
            });
            let saved = localStorage.getItem('lse_config');
            if (saved && configs.includes(saved)) select.value = saved;
            else select.value = configs[0];
            AppState.currentConfig = select.value;
            select.addEventListener('change', () => {
                AppState.currentConfig = select.value;
                localStorage.setItem('lse_config', AppState.currentConfig);
            });
        } catch (e) {
            console.error(e);
        }
    },

    async fetchBlockParams() {
        try {
            const resp = await fetch('/api/nn-block-params');
            if (!resp.ok) throw new Error('Failed to load block params');
            const json = await resp.json();
            const items = Array.isArray(json) ? json : Object.values(json);
            this.blockParams = {};
            items.forEach(item => { this.blockParams[item.type] = item; });
            this.designBlockTypes = new Set(Object.keys(this.blockParams));
            this.blockParams['nn-template'] = {
                name: 'Шаблон',
                inputs: 0, outputs: 1,
                maxInputs: 1, maxOutputs: 10,
                color: '#f97316',
                defaults: {},
                paramMeta: {},
                displayParams: []
            };
            this.blockParams['nn-settings'] = {
                name: 'Настройка',
                inputs: 0, outputs: 1,
                maxInputs: 1, maxOutputs: 10,
                color: '#8b5cf6',
                defaults: {},
                paramMeta: {},
                displayParams: []
            };


        } catch (e) {
            console.error(e);
            alert('Не удалось загрузить параметры слоёв');
        }

    },

    buildPalette() {
        const container = document.getElementById('nn-palette-items');
        container.innerHTML = '';

        if (this.currentMode === 'design') {
            // Только те типы, что были загружены с сервера
            const items = Object.entries(this.blockParams).filter(([type]) => this.designBlockTypes.has(type));
            items.forEach(([type, cfg]) => {
                const item = document.createElement('div');
                item.className = 'palette-item';
                item.dataset.type = type;
                item.innerHTML = `
                    <svg viewBox="0 0 60 40">
                        <rect x="5" y="8" width="50" height="24" rx="4" fill="#0f3460" stroke="${cfg.color}" stroke-width="2"/>
                        <text x="30" y="24" fill="${cfg.color}" font-size="10" font-weight="bold" text-anchor="middle">${type}</text>
                    </svg>
                    <div class="palette-item-name">${cfg.name}</div>
                `;
                container.appendChild(item);
            });
        } else if (this.currentMode === 'training') {
            const groups = [
                {
                    title: 'ВИЗУАЛЬНЫЕ',
                    items: [{ type: 'group', name: 'Группа', color: '#6b7280' }]
                },
                {
                    title: 'ВХОДЫ',
                    items: [
                        { type: 'input-signal', name: 'Входной сигнал', color: '#4a90d9' },
                        { type: 'table', name: 'Таблица', color: '#60a5fa' }
                    ]
                },
                {
                    title: 'ОБРАБОТКА ДАННЫХ',
                    items: []  // позже заполним
                },
                {
                    title: 'НЕЙРОННАЯ СЕТЬ',
                    items: [
                        { type: 'nn-template', name: 'Шаблон', color: '#f97316' },
                        { type: 'nn-settings', name: 'Настройка', color: '#8b5cf6' }
                    ]
                }
            ];

            groups.forEach(group => {
                const section = document.createElement('div');
                section.className = 'palette-section';
                const titleDiv = document.createElement('div');
                titleDiv.className = 'palette-section-title';
                titleDiv.textContent = group.title;
                section.appendChild(titleDiv);
                group.items.forEach(item => {
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'palette-item';
                    itemDiv.dataset.type = item.type;
                    itemDiv.innerHTML = `
                        <svg viewBox="0 0 60 40">
                            <rect x="5" y="8" width="50" height="24" rx="4" fill="#0f3460" stroke="${item.color}" stroke-width="2"/>
                            <text x="30" y="24" fill="${item.color}" font-size="10" font-weight="bold" text-anchor="middle">${item.type.substring(0,3)}</text>
                        </svg>
                        <div class="palette-item-name">${item.name}</div>
                    `;
                    section.appendChild(itemDiv);
                });
                container.appendChild(section);
            });
        }
    },

    setupPaletteDragDrop() {
        document.querySelectorAll('.palette-item').forEach(item => {
            item.addEventListener('mousedown', (e) => {
                if (e.button !== 0) return;
                e.preventDefault();
                AppState.isDraggingFromPalette = true;
                AppState.dragType = item.dataset.type;
                AppState.dragPreview = document.createElement('div');
                AppState.dragPreview.className = 'drag-preview';
                AppState.dragPreview.textContent = this.blockParams[AppState.dragType]?.name || 'Слой';
                AppState.dragPreview.style.left = `${e.clientX - 40}px`;
                AppState.dragPreview.style.top = `${e.clientY - 20}px`;
                document.body.appendChild(AppState.dragPreview);
            });
        });
    },

    createNNElement(type, x, y, props = {}, elemId = null) {
        const cfg = this.blockParams[type];
        if (!cfg) return null;
        const id = elemId || `${type}_${++AppState.elementCounter}`;
        const inputs = (props.inputCount !== undefined) ? props.inputCount : cfg.inputs;
        const outputs = cfg.outputs;

        // Объединяем дефолтные параметры с переданными
        const mergedProps = { ...cfg.defaults, ...props };

        // Сохраняем элемент в состоянии до вычисления высоты, чтобы метод calculate мог прочитать inputs/outputs
        AppState.elements[id] = {
            id,
            type: 'nn-layer',
            nnType: type,
            x, y,
            width: 150,
            height: 0, // временно
            props: mergedProps,
            inputs: inputs,
            outputs: outputs
        };

        const height = this.calculateElementHeight(id);
        AppState.elements[id].height = height;

        // Подготовка отображаемых параметров
        const displayKeys = cfg.displayParams || [];
        const paramLines = displayKeys.map(key => {
            const val = mergedProps[key];
            let text = val;
            if (Array.isArray(val)) text = JSON.stringify(val);
            else if (val === null) text = 'null';
            else if (val === undefined) text = '';
            else text = String(val);
            return `${key}: ${text}`;
        });

        const elem = document.createElement('div');
        elem.id = id;
        elem.className = 'element nn-element';
        elem.style.left = `${x}px`;
        elem.style.top = `${y}px`;
        elem.style.width = `${AppState.elements[id].width}px`;
        elem.style.height = `${height}px`;

        // HTML с центрированием портов
        elem.innerHTML = `
            <div class="element-header" style="background:${cfg.color};">${cfg.name}</div>
            <div class="element-body" style="display:flex; align-items:center; justify-content:center; height:${height - 30}px;">
                <div class="ports-left" style="display:flex; flex-direction:column; justify-content:center;">
                    ${Array.from({length: inputs}, (_, i) => `<div class="port input any-port" data-port="in-${i}" data-element="${id}" title="Вход ${i+1}"></div>`).join('')}
                </div>
                <div style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; min-width:0;">
                    ${paramLines.length > 0 
                        ? `<div class="element-params" style="font-size:10px; line-height:1.2; padding:2px 4px; color:#eee; word-break:break-word; text-align:center;">${paramLines.join('<br>')}</div>` 
                        : `<div class="element-symbol">${type}</div>`}
                </div>
                <div class="ports-right" style="display:flex; flex-direction:column; justify-content:center;">
                    ${Array.from({length: outputs}, (_, i) => `<div class="port output any-port" data-port="out-${i}" data-element="${id}" title="Выход ${i+1}"></div>`).join('')}
                </div>
            </div>
            <div class="resize-handle handle-se" data-direction="se"></div>
        `;
        document.getElementById('workspace').appendChild(elem);
        Elements.setupElementHandlers(id);
        elem.addEventListener('dblclick', (e) => {
            if (e.target.classList.contains('port')) return;
            NeuralApp.showLayerPropertiesModal(id);
        });
        return id;
    },

    updateElementDisplay(elemId) {
        const elemData = AppState.elements[elemId];
        if (!elemData) return;
        const nnType = elemData.nnType;
        const cfg = this.blockParams[nnType];
        if (!cfg) return;
        const props = elemData.props || {};

        const displayKeys = cfg.displayParams || [];
        const paramLines = displayKeys.map(key => {
            const val = props[key];
            let text = val;
            if (Array.isArray(val)) text = JSON.stringify(val);
            else if (val === null) text = 'null';
            else if (val === undefined) text = '';
            else text = String(val);
            return `${key}: ${text}`;
        });

        const elem = document.getElementById(elemId);
        if (!elem) return;

        let paramsDiv = elem.querySelector('.element-params');
        let symbolDiv = elem.querySelector('.element-symbol');

        if (paramLines.length > 0) {
            if (symbolDiv) symbolDiv.style.display = 'none';
            if (!paramsDiv) {
                const body = elem.querySelector('.element-body');
                const center = body.querySelector('div:last-child'); // центральный flex-элемент
                paramsDiv = document.createElement('div');
                paramsDiv.className = 'element-params';
                paramsDiv.style.fontSize = '10px';
                paramsDiv.style.lineHeight = '1.2';
                paramsDiv.style.padding = '2px 4px';
                paramsDiv.style.color = '#eee';
                paramsDiv.style.wordBreak = 'break-word';
                paramsDiv.style.textAlign = 'center';
                center.appendChild(paramsDiv);
            }
            paramsDiv.innerHTML = paramLines.join('<br>');
        } else {
            if (paramsDiv) paramsDiv.remove();
            if (symbolDiv) symbolDiv.style.display = '';
        }

        // Пересчитываем высоту
        const newHeight = this.calculateElementHeight(elemId);
        elemData.height = newHeight;
        elem.style.height = `${newHeight}px`;
        const body = elem.querySelector('.element-body');
        if (body) body.style.height = `${newHeight - 30}px`;
    },

    // -------- Модальное окно свойств слоя ----------
    showLayerPropertiesModal(elemId) {
        const elemData = AppState.elements[elemId];
        if (!elemData) return;
        const nnType = elemData.nnType || elemData.type;
        const cfg = this.blockParams[nnType];
        if (!cfg) return;

        const modalTitle = document.getElementById('modal-title');
        const modalContent = document.getElementById('modal-content');
        const modalOverlay = document.getElementById('modal-overlay');
        modalTitle.textContent = `Свойства слоя: ${cfg.name}`;

        const currentProps = elemData.props || {};
        const paramMeta = cfg.paramMeta || {};

        let html = '';

        for (const [key, meta] of Object.entries(paramMeta)) {
            const currentVal = currentProps[key] !== undefined ? currentProps[key] : cfg.defaults[key];
            const label = meta.label || key;
            let inputHtml = '';

            switch (meta.type) {
                case 'number':
                    inputHtml = `<input type="number" id="prop-${key}" value="${currentVal ?? 0}" step="${meta.step || 'any'}" min="${meta.min ?? ''}" max="${meta.max ?? ''}">`;
                    break;
                case 'boolean':
                    inputHtml = `<input type="checkbox" id="prop-${key}" ${currentVal ? 'checked' : ''}>`;
                    break;
                case 'select':
                    inputHtml = `<select id="prop-${key}">`;
                    for (const opt of (meta.options || [])) {
                        const val = opt === null ? '' : opt;
                        const selected = (currentVal === val || (opt === null && currentVal === null)) ? 'selected' : '';
                        inputHtml += `<option value="${val}" ${selected}>${opt === null ? 'Нет' : opt}</option>`;
                    }
                    inputHtml += `</select>`;
                    break;
                case 'array':
                default:
                    inputHtml = `<input type="text" id="prop-${key}" value="${JSON.stringify(currentVal)}" placeholder="${meta.placeholder || ''}">`;
                    break;
            }

            html += `<div class="modal-row"><label>${label}:</label>${inputHtml}</div>`;
        }

        if (cfg.maxInputs > 1) {
            const curInputs = elemData.inputs || cfg.inputs;
            html += `<div class="modal-row"><label>Количество входов:</label><input type="number" id="prop-inputCount" value="${curInputs}" min="1" max="${cfg.maxInputs}" step="1"></div>`;
        }

        modalContent.innerHTML = html;
        modalOverlay.dataset.elementId = elemId;
        Modal.showModal('modal-overlay');

        const saveBtn = document.getElementById('modal-save');
        const cancelBtn = document.getElementById('modal-cancel');

        saveBtn.onclick = () => {
            const newProps = {};
            for (const key of Object.keys(paramMeta)) {
                const input = document.getElementById(`prop-${key}`);
                if (!input) continue;
                const meta = paramMeta[key];
                switch (meta.type) {
                    case 'number':
                        newProps[key] = parseFloat(input.value) || 0;
                        break;
                    case 'boolean':
                        newProps[key] = input.checked;
                        break;
                    case 'select':
                        newProps[key] = input.value === '' ? null : input.value;
                        break;
                    case 'array':
                    default:
                        try {
                            newProps[key] = JSON.parse(input.value);
                        } catch (e) {
                            newProps[key] = cfg.defaults[key];
                        }
                        break;
                }
            }
            elemData.props = newProps;

            if (cfg.maxInputs > 1) {
                const inputCount = parseInt(document.getElementById('prop-inputCount').value) || cfg.inputs;
                elemData.inputs = inputCount;
            }

            this.updateElementPorts(elemId);
            this.updateElementDisplay(elemId);
            Modal.hideModal('modal-overlay');
        };
        cancelBtn.onclick = () => Modal.hideModal('modal-overlay');
    },

    calculateElementHeight(elemId) {
        const data = AppState.elements[elemId];
        if (!data) return 60;
        const nnType = data.nnType || data.type;
        const cfg = this.blockParams[nnType];
        if (!cfg) return 60;

        const props = data.props || {};
        const displayKeys = cfg.displayParams || [];
        const inputs = data.inputs !== undefined ? data.inputs : cfg.inputs;
        const outputs = data.outputs !== undefined ? data.outputs : cfg.outputs;
        const maxPorts = Math.max(inputs, outputs);

        // Высота заголовка
        const headerHeight = 30;
        // Отступы
        const padding = 16;
        // Расстояние между портами по вертикали
        const portSpacing = 24;
        const portsHeight = maxPorts * portSpacing;

        // Высота текстовых параметров
        const lineHeight = 18;
        const textHeight = displayKeys.length * lineHeight;

        // Выбираем максимальную из высот текста и портов
        const contentHeight = Math.max(textHeight, portsHeight);

        return headerHeight + contentHeight + padding;
    },

    // Обновить порты элемента без изменения его ID и связей
    updateElementPorts(elemId) {
        const data = AppState.elements[elemId];
        if (!data) return;
        const elem = document.getElementById(elemId);
        if (!elem) return;
        const portsLeft = elem.querySelector('.ports-left');
        const portsRight = elem.querySelector('.ports-right');
        if (portsLeft) {
            portsLeft.innerHTML = Array.from({ length: data.inputs || 1 }, (_, i) =>
                `<div class="port input any-port" data-port="in-${i}" data-element="${elemId}" title="Вход ${i+1}"></div>`
            ).join('');
        }
        if (portsRight) {
            portsRight.innerHTML = Array.from({ length: data.outputs || 1 }, (_, i) =>
                `<div class="port output any-port" data-port="out-${i}" data-element="${elemId}" title="Выход ${i+1}"></div>`
            ).join('');
        }
        Elements.setupElementHandlers(elemId);

        // Пересчитываем высоту и обновляем стиль
        const newHeight = this.calculateElementHeight(elemId);
        data.height = newHeight;
        elem.style.height = `${newHeight}px`;
        // Также корректируем высоту element-body, если изменилось количество портов
        const body = elem.querySelector('.element-body');
        if (body) body.style.height = `${newHeight - 30}px`;

        Connections.drawConnections();
    },

    // -------- Генерация структуры ----------
    generateStructureString() {
        const elements = AppState.elements;
        const connections = AppState.connections;

        // 1. Топологическая сортировка (как раньше)
        const inDegree = {};
        const graph = {};
        for (const id of Object.keys(elements)) {
            inDegree[id] = 0;
            graph[id] = [];
        }
        for (const conn of connections) {
            graph[conn.fromElement].push(conn.toElement);
            inDegree[conn.toElement] = (inDegree[conn.toElement] || 0) + 1;
        }
        const queue = Object.keys(elements).filter(id => inDegree[id] === 0);
        const sorted = [];
        while (queue.length) {
            const id = queue.shift();
            sorted.push(id);
            for (const to of graph[id]) {
                inDegree[to]--;
                if (inDegree[to] === 0) queue.push(to);
            }
        }

        // 2. Сопоставляем id -> позиция в порядке
        const pos = {};
        sorted.forEach((id, idx) => { pos[id] = idx; });

        // 3. Готовим структуры для out и add
        const outIndices = {};   // id элемента → номер out (если есть разветвление)
        let outCounter = 0;
        const parts = [];

        // Вспомогательная функция: все исходящие соединения элемента
        const getOutEdges = (id) => connections.filter(c => c.fromElement === id);

        for (const id of sorted) {
            const elem = elements[id];
            const nnType = elem.nnType || elem.type;

            if (nnType === 'out') {
                // Специальный слой "out" – просто добавляем, увеличиваем счётчик
                outIndices[id] = outCounter++;
                parts.push('out');
                continue;
            }

            if (nnType === 'add') {
                // Собираем индексы skip‑соединений (все входы, кроме in-0)
                const skipIndices = [];
                for (const conn of connections) {
                    if (conn.toElement === id && conn.toPort !== 'in-0') {
                        const srcId = conn.fromElement;
                        if (outIndices[srcId] !== undefined) {
                            skipIndices.push(outIndices[srcId]);
                        }
                    }
                }
                skipIndices.sort((a, b) => a - b);
                parts.push(`add{${skipIndices.join(',')}}`);
                continue;
            }

            // Обычный слой (den, c, re, …)
            const outEdges = getOutEdges(id);
            if (outEdges.length > 1) {
                // Сортируем исходящие по позиции цели – первое будет основным
                outEdges.sort((a, b) => pos[a.toElement] - pos[b.toElement]);
                // Для всех, кроме первого, нужно создать out (если ещё не создан)
                let needOut = false;
                for (let i = 1; i < outEdges.length; i++) {
                    // out создаётся один раз для этого элемента
                    if (!needOut) {
                        outIndices[id] = outCounter++;
                        needOut = true;
                    }
                }
                parts.push(nnType);
                if (needOut) {
                    parts.push('out');
                }
            } else {
                parts.push(nnType);
            }
        }

        return parts.join('_');
    },

    // -------- Базовые операции с элементами ----------
    selectElement(elemId) {
        Elements.deselectAll();
        AppState.selectedElement = elemId;
        AppState.selectedElements = [elemId];
        const elem = document.getElementById(elemId);
        if (elem) elem.classList.add('selected');
    },

    copySelectedElements() {
        const ids = AppState.selectedElements.length > 0 ? [...AppState.selectedElements] : (AppState.selectedElement ? [AppState.selectedElement] : []);
        if (ids.length === 0) return;
        ids.forEach(id => {
            const orig = AppState.elements[id];
            if (!orig) return;
            const nnType = orig.nnType || orig.type;
            const newId = this.createNNElement(nnType, orig.x + 50, orig.y + 50, JSON.parse(JSON.stringify(orig.props)));
            if (newId) AppState.selectedElements.push(newId);
        });
    },

    deleteSelectedElements() {
        const ids = AppState.selectedElements.length > 0 ? [...AppState.selectedElements] : (AppState.selectedElement ? [AppState.selectedElement] : []);
        ids.forEach(id => {
            AppState.connections = AppState.connections.filter(c => c.fromElement !== id && c.toElement !== id);
            const elem = document.getElementById(id);
            if (elem) elem.remove();
            delete AppState.elements[id];
        });
        AppState.selectedElement = null;
        AppState.selectedElements = [];
        Connections.drawConnections();
    },

    newProject() {
        if (Object.keys(AppState.elements).length > 0) {
            if (!confirm('Создать новый проект? Несохранённые изменения будут потеряны.')) return;
        }
        document.getElementById('workspace').innerHTML = '';
        document.getElementById('connections-svg').innerHTML = '';
        
        resetState();
        this.applyMode(this.currentMode);  
        //AppState.project.type = PROJECT_TYPE.NEURAL_TEMPLATE; // <-- добавить
        Viewport.updateTransform();
    },

    async autoLoadFromURL() {
        const params = new URLSearchParams(window.location.search);
        const filename = params.get('load');
        if (!filename) return;
        const config = params.get('config') || '';
        const source = params.get('source') || 'projects';
        if (config) AppState.currentConfig = config;

        try {
            const data = await Settings.loadProject(filename, source);
            document.getElementById('workspace').innerHTML = '';
            document.getElementById('connections-svg').innerHTML = '';
            resetState();
            
            // Восстанавливаем viewport до создания элементов
            if (data.viewport) {
                AppState.viewport.zoom = data.viewport.zoom ?? 1;
                AppState.viewport.panX = data.viewport.panX ?? 0;
                AppState.viewport.panY = data.viewport.panY ?? 0;
            }
            
            if (data.project) {
                 AppState.project = data.project;
                // Определяем режим по типу
                const type = data.project.type;
                if (type === PROJECT_TYPE.NEURAL_TEMPLATE || type === PROJECT_TYPE.NEURAL_NETWORK) {
                    const mode = type === PROJECT_TYPE.NEURAL_TEMPLATE ? 'design' : 'training';
                    this.applyMode(mode);
                    //this.currentMode = mode;
                    //document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
                    //const btn = document.getElementById(`btn-mode-${mode}`);
                    //if (btn) btn.classList.add('active');
                    //this.buildPalette();
                    //this.setupPaletteDragDrop();
                }
            }

            const elements = data.elements || {};
            for (const [id, el] of Object.entries(elements)) {
                const nnType = el.nnType || el.type;
                const cfg = this.blockParams[nnType];
                if (!cfg) continue;
                this.createNNElement(nnType, el.x, el.y, el.props, id);
            }

            AppState.connections = data.connections || [];

            // Обновляем счётчик
            const maxNum = Object.keys(AppState.elements).reduce((max, key) => {
                const match = key.match(/_(\d+)$/);
                return match ? Math.max(max, parseInt(match[1])) : max;
            }, 0);
            AppState.elementCounter = Math.max(AppState.elementCounter, maxNum);

            Viewport.updateTransform();
            Connections.drawConnections();   // теперь только один раз, с полными данными
        } catch (e) {
            console.error('Ошибка загрузки:', e);
        }
    },

async saveProject() {
    if (!AppState.project.code) {
        Modal.showProjectPropertiesModal();
        alert('Пожалуйста, укажите код проекта перед сохранением.');
        return;
    }
    //AppState.project.type = PROJECT_TYPE.NEURAL_TEMPLATE;
    // Сохраняем код проекта как введённый пользователем
    const projectCode = AppState.project.code;
    // Генерируем строку архитектуры
    const architecture = this.generateStructureString();
    // Для файла используем пользовательский код, архитектуру кладём в code
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
        code: architecture   // строка структуры
    };
    const filename = `${projectCode}_neural.json`;   // имя файла
    try {
        await Settings.saveProject(filename, project, 'projects');
        alert(`Проект сохранён как ${filename}`);
    } catch (e) {
        console.error(e);
        alert('Ошибка сохранения: ' + e.message);
    }
},

    // -------- Обработчики ----------
    setupGlobalMouseHandlers() {
        document.addEventListener('mousemove', (e) => {
            if (AppState.isDraggingFromPalette && AppState.dragPreview) {
                AppState.dragPreview.style.left = `${e.clientX - 40}px`;
                AppState.dragPreview.style.top = `${e.clientY - 20}px`;
            }
            if (AppState.resizing) Elements.handleResize(e);
            if (AppState.draggingElement) Elements.handleDrag(e);
            if (AppState.tempLine && AppState.connectingFrom) Connections.drawTempConnection(e);
        });
        document.addEventListener('mouseup', (e) => {
            if (AppState.resizing) { AppState.resizing = null; }
            if (AppState.isDraggingFromPalette) {
                if (AppState.dragPreview) { AppState.dragPreview.remove(); AppState.dragPreview = null; }
                const container = document.getElementById('workspace-container');
                const rect = container.getBoundingClientRect();
                if (e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom) {
                    const pos = screenToCanvas(e.clientX, e.clientY);
                    if (this.currentMode === 'design') {
                        this.createNNElement(AppState.dragType, pos.x - 75, pos.y - 30);
                    } else if (this.currentMode === 'training') {
                        // nn-типы обучения
                        if (AppState.dragType === 'nn-template' || AppState.dragType === 'nn-settings') {
                            this.createNNElement(AppState.dragType, pos.x - 75, pos.y - 30);
                        } else {
                            // Стандартные элементы из ELEMENT_TYPES
                            const config = ELEMENT_TYPES[AppState.dragType];
                            if (config) {
                                const w = config.minWidth || 120;
                                const h = config.minHeight || 60;
                                Elements.addElement(AppState.dragType, pos.x - w/2, pos.y - h/2);
                            }
                        }
                    }
                }
                AppState.isDraggingFromPalette = false;
                AppState.dragType = null;
            }
            if (AppState.draggingElement) { AppState.draggingElement = null; }
            Connections.clearConnectionState();
        });
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;
            if (e.key === 'Delete' && (AppState.selectedElement || AppState.selectedElements.length > 0)) {
                NeuralApp.deleteSelectedElements();
            }
            if (e.key === 'Escape') {
                Elements.deselectAll();
                Connections.clearConnectionState();
                if (AppState.isDraggingFromPalette) {
                    AppState.isDraggingFromPalette = false;
                    if (AppState.dragPreview) { AppState.dragPreview.remove(); AppState.dragPreview = null; }
                }
            }
        });
    },

    setupContextMenu() {
        document.addEventListener('click', (e) => {
            const menu = document.getElementById('context-menu');
            if (!menu.contains(e.target)) menu.style.display = 'none';
        });
        document.getElementById('ctx-properties').addEventListener('click', () => {
            const elemId = document.getElementById('context-menu').dataset.elementId;
            document.getElementById('context-menu').style.display = 'none';
            if (elemId && AppState.elements[elemId]) {
                NeuralApp.showLayerPropertiesModal(elemId);
            }
        });
        document.getElementById('ctx-delete').addEventListener('click', () => {
            document.getElementById('context-menu').style.display = 'none';
            this.deleteSelectedElements();
        });
        document.getElementById('ctx-copy').addEventListener('click', () => {
            document.getElementById('context-menu').style.display = 'none';
            this.copySelectedElements();
        });
    },

    setupWorkspaceClick() {
        document.getElementById('workspace-container').addEventListener('click', (e) => {
            if (AppState.marqueeJustEnded) return;
            if (e.button === 0 && !e.target.closest('.element') && !e.target.closest('.port')) {
                Elements.deselectAll();
            }
        });
    },

    setupMultiSelection() {
        const container = document.getElementById('workspace-container');
        const rectEl = document.getElementById('selection-rect');
        container.addEventListener('mousedown', (e) => {
            if (e.button !== 0 || e.target.closest('.element') || e.target.closest('#minimap')) return;
            const pos = screenToCanvas(e.clientX, e.clientY);
            AppState.multiSelecting = true;
            AppState.selectionRect = { startX: pos.x, startY: pos.y, x: pos.x, y: pos.y, w: 0, h: 0 };
            rectEl.style.left = e.clientX + 'px';
            rectEl.style.top = e.clientY + 'px';
            rectEl.style.width = '0px';
            rectEl.style.height = '0px';
            rectEl.style.display = 'block';
        });
        document.addEventListener('mousemove', (e) => {
            if (!AppState.multiSelecting) return;
            const pos = screenToCanvas(e.clientX, e.clientY);
            const sx = AppState.selectionRect.startX, sy = AppState.selectionRect.startY;
            const x = Math.min(sx, pos.x), y = Math.min(sy, pos.y);
            const w = Math.abs(pos.x - sx), h = Math.abs(pos.y - sy);
            rectEl.style.left = (x * AppState.viewport.zoom + AppState.viewport.panX) + 'px';
            rectEl.style.top = (y * AppState.viewport.zoom + AppState.viewport.panY) + 'px';
            rectEl.style.width = (w * AppState.viewport.zoom) + 'px';
            rectEl.style.height = (h * AppState.viewport.zoom) + 'px';
            const selected = [];
            for (const [id, data] of Object.entries(AppState.elements)) {
                if (data.x >= x && data.x + data.width <= x + w && data.y >= y && data.y + data.height <= y + h) selected.push(id);
            }
            AppState.selectedElements = selected;
            AppState.selectedElement = selected.length > 0 ? selected[selected.length - 1] : null;
            document.querySelectorAll('.element').forEach(el => el.classList.toggle('selected', selected.includes(el.id)));
        });
        document.addEventListener('mouseup', () => {
            if (AppState.multiSelecting) {
                AppState.multiSelecting = false;
                const w = parseInt(rectEl.style.width) || 0, h = parseInt(rectEl.style.height) || 0;
                rectEl.style.display = 'none';
                if (w > 2 || h > 2) {
                    AppState.marqueeJustEnded = true;
                    setTimeout(() => { AppState.marqueeJustEnded = false; }, 50);
                }
            }
        });
    }
};

document.addEventListener('DOMContentLoaded', () => {
    resetState();
    NeuralApp.init().then(() => {
        NeuralApp.autoLoadFromURL();
    });
});