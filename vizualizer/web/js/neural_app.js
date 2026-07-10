// neural_app.js — Конструктор нейросетей (полный базовый функционал)
const NeuralApp = {
    blockParams: {},

    async init() {
        this.initUser();
        Settings.init().catch(console.error);
        this.loadConfigurations().catch(console.error);
        await this.fetchBlockParams();
        this.buildPalette();
        this.setupPaletteDragDrop();
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
        } catch (e) {
            console.error(e);
            alert('Не удалось загрузить параметры слоёв');
        }
    },

    buildPalette() {
        const container = document.getElementById('nn-palette-items');
        container.innerHTML = '';
        Object.entries(this.blockParams).forEach(([type, cfg]) => {
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

    createNNElement(type, x, y, props = {}) {
        const cfg = this.blockParams[type];
        if (!cfg) return null;
        const id = `${type}_${++AppState.elementCounter}`;
        // Количество входов: из props, если задано, иначе из конфига
        const inputs = (props.inputCount !== undefined) ? props.inputCount : cfg.inputs;
        const outputs = cfg.outputs;   // не меняем
        const elemData = {
            id, type, x, y,
            width: 150, height: 60,
            props: { ...cfg.defaults, ...props },
            inputs: inputs,
            outputs: outputs
        };
        AppState.elements[id] = elemData;

        const elem = document.createElement('div');
        elem.id = id;
        elem.className = 'element nn-element';
        elem.style.left = `${x}px`;
        elem.style.top = `${y}px`;
        elem.style.width = `${elemData.width}px`;
        elem.style.height = `${elemData.height}px`;
        elem.innerHTML = `
            <div class="element-header" style="background:${cfg.color};">${cfg.name}</div>
            <div class="element-body">
                <div class="ports-left">
                    ${Array.from({length: inputs}, (_, i) => `<div class="port input any-port" data-port="in-${i}" data-element="${id}" title="Вход ${i+1}"></div>`).join('')}
                </div>
                <div class="element-symbol">${type}</div>
                <div class="ports-right">
                    ${Array.from({length: outputs}, (_, i) => `<div class="port output any-port" data-port="out-${i}" data-element="${id}" title="Выход ${i+1}"></div>`).join('')}
                </div>
            </div>
            <div class="resize-handle handle-se" data-direction="se"></div>
        `;
        document.getElementById('workspace').appendChild(elem);
        Elements.setupElementHandlers(id);
        // Двойной клик — открыть свойства
        elem.addEventListener('dblclick', (e) => {
            if (e.target.classList.contains('port')) return;
            NeuralApp.showLayerPropertiesModal(id);
        });
        Connections.drawConnections();
        return id;
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
            const newId = this.createNNElement(orig.type, orig.x + 50, orig.y + 50, JSON.parse(JSON.stringify(orig.props)));
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

    // -------- Генерация структуры ----------
    generateStructureString() {
        const elements = AppState.elements;
        const connections = AppState.connections;
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
        const parts = [];
        const outMarkers = {};
        let outCounter = 0;
        for (const id of sorted) {
            const elem = elements[id];
            const type = elem.type;
            if (type === 'out') {
                const marker = outCounter++;
                outMarkers[id] = marker;
                parts.push('out');
            } else if (type === 'add') {
                const inputs = connections.filter(c => c.toElement === id).map(c => c.fromElement);
                const markers = inputs.map(inId => outMarkers[inId] !== undefined ? outMarkers[inId] : null).filter(m => m !== null);
                parts.push(`add{${markers.join(',')}}`);
            } else {
                parts.push(type);
            }
        }
        return parts.join('_');
    },

    newProject() {
        if (Object.keys(AppState.elements).length > 0) {
            if (!confirm('Создать новый проект? Несохранённые изменения будут потеряны.')) return;
        }
        document.getElementById('workspace').innerHTML = '';
        document.getElementById('connections-svg').innerHTML = '';
        resetState();
        Viewport.updateTransform();
    },

    async saveProject() {
        if (!AppState.project.code) {
            Modal.showProjectPropertiesModal();
            alert('Пожалуйста, укажите код проекта перед сохранением.');
            return;
        }
        AppState.project.type = 'neural';
        AppState.project.code = this.generateStructureString();
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
            code: AppState.project.code
        };
        const filename = `${AppState.project.code}_neural.json`;
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
                    NeuralApp.createNNElement(AppState.dragType, pos.x - 75, pos.y - 30);
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

        // -------- Модальное окно свойств слоя ----------
    // -------- Модальное окно свойств слоя ----------
    showLayerPropertiesModal(elemId) {
        const elemData = AppState.elements[elemId];
        if (!elemData) return;
        const cfg = this.blockParams[elemData.type];
        if (!cfg) return;

        const modalTitle = document.getElementById('modal-title');
        const modalContent = document.getElementById('modal-content');
        const modalOverlay = document.getElementById('modal-overlay');
        modalTitle.textContent = `Свойства слоя: ${cfg.name}`;

        const currentProps = elemData.props || {};
        const paramMeta = cfg.paramMeta || {};

        let html = '';

        // Поля параметров из paramMeta
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

        // Настройка количества входов (если разрешено)
        // Выходы не настраиваем – один выход может быть подключён к нескольким входам
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

            // Обновить количество входов, если задано
            if (cfg.maxInputs > 1) {
                const inputCount = parseInt(document.getElementById('prop-inputCount').value) || cfg.inputs;
                elemData.inputs = inputCount;
            }

            // Безопасно обновить порты (не удаляя элемент)
            this.updateElementPorts(elemId);

            Modal.hideModal('modal-overlay');
        };
        cancelBtn.onclick = () => Modal.hideModal('modal-overlay');
    },

        // Обновить порты элемента без изменения ID
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
        Connections.drawConnections();
    },

    // Пересоздание элемента (например, после изменения количества портов)
    recreateElement(elemId) {
        const data = AppState.elements[elemId];
        if (!data) return;
        const oldElem = document.getElementById(elemId);
        if (oldElem) oldElem.remove();
        // Создаём заново с теми же координатами и обновлёнными props
        this.createNNElement(data.type, data.x, data.y, data.props);
        // Так как createNNElement создаёт новый id, нужно сохранить старый id? 
        // Упростим: удалим старый элемент и создадим новый, но тогда потеряем ссылки в connections.
        // Лучше обновить DOM существующего элемента, не удаляя его из AppState.
        // Поэтому ниже альтернативный подход.
    },

    updateNNPorts(elemId) {
        const elem = document.getElementById(elemId);
        const elemData = AppState.elements[elemId];
        if (!elem || !elemData) return;
        const cfg = this.blockParams[elemData.type];
        if (!cfg) return;

        const maxInputs = cfg.maxInputs || 1;
        const maxOutputs = cfg.maxOutputs || 1;
        const currentInputs = elemData.inputCount || cfg.inputs;
        const currentOutputs = elemData.outputCount || cfg.outputs;

        // Удаляем связи к несуществующим портам
        AppState.connections = AppState.connections.filter(c => {
            if (c.toElement === elemId && c.toPort.startsWith('in-')) {
                const portNum = parseInt(c.toPort.split('-')[1]);
                return portNum < currentInputs;
            }
            if (c.fromElement === elemId && c.fromPort.startsWith('out-')) {
                const portNum = parseInt(c.fromPort.split('-')[1]);
                return portNum < currentOutputs;
            }
            return true;
        });

        // Обновляем DOM портов
        const portsLeft = elem.querySelector('.ports-left');
        const portsRight = elem.querySelector('.ports-right');
        if (portsLeft) {
            portsLeft.innerHTML = '';
            for (let i = 0; i < currentInputs; i++) {
                const port = document.createElement('div');
                port.className = 'port input any-port';
                port.dataset.port = `in-${i}`;
                port.dataset.element = elemId;
                port.title = `Вход ${i+1}`;
                portsLeft.appendChild(port);
            }
        }
        if (portsRight) {
            portsRight.innerHTML = '';
            for (let i = 0; i < currentOutputs; i++) {
                const port = document.createElement('div');
                port.className = 'port output any-port';
                port.dataset.port = `out-${i}`;
                port.dataset.element = elemId;
                port.title = `Выход ${i+1}`;
                portsRight.appendChild(port);
            }
        }

        // Навешиваем обработчики портов
        elem.querySelectorAll('.port').forEach(port => Connections.setupPortHandlers(port));
        Connections.drawConnections();
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
    NeuralApp.init();
});