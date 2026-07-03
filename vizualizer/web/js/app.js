/**
 * Главный модуль приложения
 * app.js
 */

const App = {
    /**
     * Инициализация приложения
     */
init() {
    // Settings.init() вызовется внутри autoLoadFromURL, если есть ?load=
    this.initUser();
    Settings.init().catch(console.error);
    this.loadConfigurations().catch(console.error);

    this.setupPaletteDragDrop();
    this.setupGlobalMouseHandlers();
    this.setupContextMenu();
    this.setupWorkspaceClick();
    //this.setupOutputCounter();
    this.setupMultiSelection();

    Viewport.init();
    Modal.init();
    Project.init();

    if (typeof Outputs !== 'undefined' && Outputs.updateOutputStatus) {
        Outputs.updateOutputStatus();
    }

    console.log('Logic Scheme Editor initialized');

    document.getElementById('btn-generate-code').addEventListener('click', () => {
        const code = CodeGen.generate();
        document.getElementById('code-output').value = code;
        document.getElementById('code-modal-overlay').style.display = 'flex';

        const srcEl = document.getElementById('code-output');
        const dstEl = document.getElementById('code-output-system');
        const originalCode = (srcEl && typeof srcEl.value === 'string') ? srcEl.value : '';

        const processed = App.prepareCodeForSystem(originalCode);
        if (dstEl) {
            dstEl.value = processed || '';
        } else {
            // fallback: если второе поле не добавили — покажем в исходном
            if (srcEl) srcEl.value = processed || '';
        }
    });

    document.getElementById('constants-modal-overlay').addEventListener('click', (e) => {
        if (e.target.id === 'constants-modal-overlay') App.hideConstantsModal();
    });

    document.getElementById('constants-modal-overlay').addEventListener('click', (e) => {
        if (e.target.id === 'constants-modal-overlay') App.hideConstantsModal();
    });

    document.getElementById('code-modal-close').addEventListener('click', () => {
        document.getElementById('code-modal-overlay').style.display = 'none';
    });

    document.getElementById('btn-visualize').addEventListener('click', () => {
        App.openSignalVisualizer();
    });
    document.getElementById('btn-map').addEventListener('click', () => {
        const proj = AppState.project || {};
        const code = (proj.code || '').trim();
        const type = (proj.type || 'parameter').trim();

        if (!code || !type) {
            alert('Сначала откройте или сохраните проект');
            return;
        }

        // Формируем имя файла так же, как при сохранении!
        const filename = `${code}_${type}.json`;
        const url = `/map.html?project=${encodeURIComponent(filename)}&config=${encodeURIComponent(AppState.currentConfig || '')}`;
        window.open(url, '_blank');
    });

        // Добавить в функцию init() после других обработчиков
    document.getElementById('btn-all-signals').addEventListener('click', () => {
        const currentUser = localStorage.getItem('lse_username') || 'Аноним';
        const config = AppState.currentConfig || '';
        const url = `/all-signals.html?user=${encodeURIComponent(currentUser)}&config=${encodeURIComponent(config)}`;
        window.open(url, '_blank');
    });

        // Обработчики для модалки "Константы"
    document.getElementById('btn-constants').addEventListener('click', () => App.showConstantsModal());
    document.getElementById('constants-cancel').addEventListener('click', () => this.hideConstantsModal());
    document.getElementById('constants-assign').addEventListener('click', () => this.assignConstants());
    document.getElementById('constants-modal-overlay').addEventListener('click', (e) => {
        if (e.target.id === 'constants-modal-overlay') this.hideConstantsModal();
        });

document.getElementById('btn-create-similar').addEventListener('click', async () => {
    try {
        const proj = AppState.project || {};
        const code = (proj.code || '').trim();
        const type = (proj.type || 'parameter').trim();

        if (!code) {
            alert('Сначала укажите код проекта в свойствах и сохраните проект.');
            return;
        }

        const filename = `${code}_${type}.json`;
        const source = (type === 'template') ? 'templates' : 'projects';

        // Проверяем, что проект существует (через Settings.loadProject, которая добавит config)
        await Settings.loadProject(filename, source);

        const config = AppState.currentConfig || '';
        const url = `/similar.html?filename=${encodeURIComponent(filename)}&source=${encodeURIComponent(source)}&config=${encodeURIComponent(config)}`;
        window.open(url, '_blank');
    } catch (e) {
        console.error(e);
        alert('Ошибка при открытии мастера: ' + e.message);
    }
});


    //document.getElementById('code-modal-to-system').addEventListener('click', () => {
    //    const srcEl = document.getElementById('code-output');
    //    const dstEl = document.getElementById('code-output-system');
    //    const originalCode = (srcEl && typeof srcEl.value === 'string') ? srcEl.value : '';
    //
    //    const processed = App.prepareCodeForSystem(originalCode);
    //    if (dstEl) {
    //        dstEl.value = processed || '';
    //    } else {
    //        // fallback: если второе поле не добавили — покажем в исходном
    //        if (srcEl) srcEl.value = processed || '';
    //    }
    //});

    // ===== Автозагрузка проекта из URL =====
    this.autoLoadFromURL();
},



async loadConfigurations() {
    try {
        const resp = await fetch('/api/configurations');
        if (!resp.ok) throw new Error('Failed to fetch configurations');
        const data = await resp.json();
        const configs = data.configurations || [];
        const select = document.getElementById('config-select');
        if (!select) return;
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
        // Восстанавливаем сохранённую конфигурацию или берём первую
        let savedConfig = localStorage.getItem('lse_config');
        if (savedConfig && configs.includes(savedConfig)) {
            select.value = savedConfig;
        } else {
            select.value = configs[0];
        }
        AppState.currentConfig = select.value;
        localStorage.setItem('lse_config', AppState.currentConfig);

        // Обработчик смены
        select.addEventListener('change', () => {
            AppState.currentConfig = select.value;
            localStorage.setItem('lse_config', AppState.currentConfig);
        });
    } catch (e) {
        console.error('Ошибка загрузки конфигураций:', e);
        const select = document.getElementById('config-select');
        select.innerHTML = '<option value="">Ошибка</option>';
        Settings.fetchSignals('*').catch(() => {});
    }
},


    showConstantsModal() {
        const list = document.getElementById('constants-list');
        const signalConsts = Object.values(AppState.elements).filter(e => e.type === 'signal-const');
        if (!signalConsts.length) {
            alert('В проекте нет сигналов-констант.');
            return;
        }

        list.innerHTML = signalConsts.map(el => `
            <div class="modal-row" style="display:flex; gap:12px; align-items:center; margin-bottom:8px;">
                <span style="min-width:200px;">${el.props.description || el.id}</span>
                <input type="text" class="const-kks" data-id="${el.id}" placeholder="KKS код" style="flex:1;">
            </div>
        `).join('');
        document.getElementById('constants-modal-overlay').style.display = 'flex';
    },

    hideConstantsModal() {
        document.getElementById('constants-modal-overlay').style.display = 'none';
    },

    async assignConstants() {
        const inputs = document.querySelectorAll('.const-kks');
        const promises = [];
        const replacements = []; // { elemId, kks }

        for (const inp of inputs) {
            const kks = inp.value.trim();
            if (!kks) continue;
            const elemId = inp.dataset.id;
            const elem = AppState.elements[elemId];
            if (!elem) continue;

            // Создаём проект для константы
            const projectData = {
                version: '1.0',
                project: {
                    code: kks,
                    type: 'parameter',
                    description: elem.props.description || '',
                    dimension: '',
                },
                elements: {
                    const_1: {
                        id: 'const_1',
                        type: 'const',
                        x: 100,
                        y: 100,
                        width: 120,
                        height: 60,
                        props: { value: elem.props.value || 0 }
                    },
                    output_2: {
                        id: 'output_2',
                        type: 'output',
                        x: 300,
                        y: 100,
                        width: 150,
                        height: 60,
                        props: {
                            label: kks,
                            outputGroup: ''
                        }
                    }
                },
                connections: [
                    {
                        fromElement: 'const_1',
                        fromPort: 'out-0',
                        toElement: 'output_2',
                        toPort: 'in-0',
                        signalType: 'numeric'
                    }
                ],
                counter: 2,
                viewport: { zoom: 1, panX: 0, panY: 0 },
                code: String(elem.props.value ?? 0),
                visualizer_state: null
            };
            const filename = `${kks}_parameter.json`;
            promises.push(
                Settings.saveProject(filename, projectData, 'projects')
                    .then(() => ({ success: true, elemId, kks }))
                    .catch(e => ({ success: false, elemId, kks, error: e }))
            );
            replacements.push({ elemId, kks });
        }

        if (promises.length === 0) {
            alert('Не задано ни одного KKS.');
            return;
        }

        const results = await Promise.all(promises);
        const failed = results.filter(r => !r.success);
        if (failed.length) {
            alert('Ошибки при создании проектов:\n' + failed.map(f => `${f.kks}: ${f.error.message}`).join('\n'));
            return;
        }

        // Заменяем signal-const на input-signal в текущем проекте
        for (const { elemId, kks } of replacements) {
            const elemData = AppState.elements[elemId];
            if (!elemData) continue;
            elemData.type = 'input-signal';
            elemData.props = {
                name: kks,
                description: elemData.props.description || '',
                signalType: SIGNAL_TYPE.NUMERIC,
                dimension: '',
                comment: elemData.props.comment || ''
            };

            // Перерисовываем элемент
            const elemDom = document.getElementById(elemId);
            if (elemDom) {
                const { html } = Elements.createElementHTML('input-signal', elemId, elemData.x, elemData.y, elemData.props, elemData.width, elemData.height);
                elemDom.outerHTML = html;
                Elements.setupElementHandlers(elemId);
            }
        }

        Connections.drawConnections();
        this.hideConstantsModal();
        alert('Константы успешно назначены и проекты созданы.');
    },


/**
 * Инициализация пользователя.
 * Если имя есть в localStorage — подхватываем.
 * Если нет — показываем ненавязчивый промпт один раз.
 */
initUser() {
    let username = localStorage.getItem('lse_username');

    if (!username) {
        username = prompt('Представьтесь, пожалуйста (имя будет сохранено в проектах):');
        if (username && username.trim()) {
            username = username.trim();
            localStorage.setItem('lse_username', username);
        } else {
            username = 'Аноним';
            localStorage.setItem('lse_username', username);
        }
    }

    AppState.currentUser = username;
    this.updateUserBadge();

    // Клик по бейджу — смена пользователя
    const badge = document.getElementById('user-badge');
    if (badge) {
        badge.addEventListener('click', () => this.changeUser());
    }
},

/**
 * Смена пользователя
 */
changeUser() {
    const current = AppState.currentUser || '';
    const newName = prompt('Введите имя пользователя:', current);
    if (newName !== null && newName.trim()) {
        const trimmed = newName.trim();
        localStorage.setItem('lse_username', trimmed);
        AppState.currentUser = trimmed;
        this.updateUserBadge();
    }
},

/**
 * Обновление бейджа в UI
 */
updateUserBadge() {
    const el = document.getElementById('user-badge-name');
    if (el) {
        el.textContent = AppState.currentUser || '—';
    }
},

async autoLoadFromURL() {
    const params = new URLSearchParams(window.location.search);
    const filename = params.get('load');
    if (!filename) return;

    const source = params.get('source') || 'projects';
    const configFromUrl = params.get('config') || '';   // <-- добавить
    if (configFromUrl) {
        AppState.currentConfig = configFromUrl;
        localStorage.setItem('lse_config', configFromUrl);       // ← сохраняем

        // если на странице есть селектор, обновим его (опционально)
        const select = document.getElementById('config-select');
        if (select) select.value = configFromUrl;
    }

    console.log(`[autoLoad] Загрузка: ${filename}, source: ${source}, config: ${configFromUrl}`);


    try {
        // Гарантируем что Settings готов
        await Settings.init();
        console.log('[autoLoad] Settings готов');

        const data = await Settings.loadProject(filename, source);
        console.log('[autoLoad] Данные получены:', Object.keys(data));

        Project._processLoadedData(data);
        console.log('[autoLoad] Проект загружен успешно');

        // Чистим URL чтобы F5 не перезагружал повторно
        window.history.replaceState({}, '', window.location.pathname);

    } catch (err) {
        console.error('[autoLoad] Ошибка:', err);
    }
},

openSignalVisualizer() {
    try {
        // 1) Собираем входные сигналы
        const signals = Object.values(AppState.elements)
            .filter(e => e && e.type === 'input-signal')
            .map(e => e.props?.name || e.id);

        const tables = Object.values(AppState.elements)
            .filter(e => e && e.type === 'table')
            .map(e => e.props?.name || e.id);

        const uniqSignals = [...new Set(signals)];
        
        if (uniqSignals.length === 0) {
            alert('Нет входных сигналов в схеме.');
            return;
        }

        // 2) Генерируем код
        let codeStr = '';
        if (typeof CodeGen !== 'undefined' && typeof CodeGen.generate === 'function') {
            codeStr = CodeGen.generate() || '';
        }

        // 3) Определяем URL-ы динамически
        const currentHost = window.location.hostname;
        const apiPort = window.location.port || 8000;
        const visualizerPort = Settings.config?.visualizerPort || 8501;
        
        const apiUrl = `http://${currentHost}:${apiPort}`;
        const visualizerBase = `http://${currentHost}:${visualizerPort}`;

        console.log('API URL:', apiUrl);
        console.log('Visualizer URL:', visualizerBase);

        // 4) Получаем сохранённое состояние визуализатора из проекта
        const visualizerState = AppState.project?.visualizer_state || null;
        
        if (visualizerState) {
            console.log('Передаём сохранённое состояние визуализатора:', visualizerState);
        }

        // 5) Создаём сессию на backend (с передачей состояния)
        fetch('/api/visualize/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                signals: uniqSignals,
                tables: tables,
                code: codeStr,
                visualizer_state: visualizerState  // НОВОЕ: передаём состояние
            })
        })
        .then(r => {
            if (!r.ok) throw new Error('Failed to create visualize session');
            return r.json();
        })
        .then(data => {
            const token = data.token;
            
            // НОВОЕ: сохраняем токен для последующего получения состояния
            AppState.currentVisualizerToken = token;
            
            const params = new URLSearchParams();
            params.set('session', token);
            params.set('api_url', apiUrl);
            params.set('config', AppState.currentConfig || '');
            console.log('Передаём config в визуализатор:', AppState.currentConfig);
            
            const visualizerUrl = `${visualizerBase}/?${params.toString()}`;
            console.log('Opening visualizer:', visualizerUrl);
            window.open(visualizerUrl, '_blank');
        })
        .catch(err => {
            console.error(err);
            alert('Не удалось открыть визуализатор: ' + err.message);
        });

    } catch (e) {
        console.error(e);
        alert('Ошибка при подготовке визуализации: ' + e.message);
    }
},

prepareCodeForSystem(codeStr) {
    if (!codeStr || typeof codeStr !== 'string') return codeStr;

    let out = codeStr;

    // 1) Заменяем логические операторы
    out = out.replace(/\bAND\b/g, '&&')
             .replace(/\bOR\b/g, '||')
             .replace(/\bNOT\b/g, '!');

    // 2) § → _
    out = out.replace(/§/g, '_');

    // 3) Заменяем одиночное '=' на '==', но не трогаем '>=', '<=', '!=', '=='
    out = out.replace(/(?<![<>=!])=(?![=])/g, '==');

    // 4) Добавляем 'P' перед именами input-signal, начинающимися с цифры
    try {
        const usedSignals = Object.values(AppState.elements || {})
            .filter(e => e && e.type === 'input-signal')
            .map(e => (e.props?.name || e.id || '').trim())
            .filter(name => !!name)
            .map(name => name.replace(/§/g, '_')); // Приводим имена к формату после замены § на _

        const unique = Array.from(new Set(usedSignals));
        const startsWithDigit = unique.filter(name => /^\d/.test(name));
        const identClass = 'A-Za-z0-9_\\u0400-\\u04FF_\\.'; 
        const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

        for (const sig of startsWithDigit) {
            const re = new RegExp(`(^|[^${identClass}])(${esc(sig)})(?![${identClass}])`, 'g');
            out = out.replace(re, `$1P$2`);
        }

    } catch (e) {
        console.warn('prepareCodeForSystem: не удалось обработать список сигналов', e);
    }

    // 5) Для спецфункций — не добавляем P и оборачиваем первый аргумент в кавычки
    const fnList = [
        'PREV', 'GETPOINT', 'INTERPOLATE',
        'HISTORYAVG', 'HISTORYCOUNT', 'HISTORYSUM',
        'HISTORYMAX', 'HISTORYMIN', 'HISTORYDIFF', 'HISTORYGRADIENT'
    ];

    for (const fn of fnList) {
        const re = new RegExp(`\\b${fn}\\s*\\(\\s*([^,\\)]+)`, 'g');

        out = out.replace(re, (match, p1) => {
            if (/^['"]/.test(p1.trim())) return match;
            let arg = p1.trim().replace(/^P(?=\d)/, '');
            return `${fn}('${arg}'`;
        });
    }

    // 6) 🆕 Заменяем унарные минусы на умножение на -1
    // Унарный минус может стоять после: начала строки, (, операторов, запятой, пробелов
    
    // Заменяем унарный минус перед сигналами, начинающимися с P и цифры
    out = out.replace(/(^|[(+*\/%,\s!-]|==|!=|<=|>=|<|>|&&|\|\|)-(?=P\d)/g, '$1-1 * ');
    
    // Заменяем унарный минус перед выражениями в скобках
    out = out.replace(/(^|[(+*\/%,\s!-]|==|!=|<=|>=|<|>|&&|\|\|)-(?=\()/g, '$1-1 * ');

    return out;
},

/**
 * Получает состояние визуализатора с сервера
 * Вызывается перед сохранением проекта
 */
async fetchVisualizerState() {
    if (!AppState.currentVisualizerToken) {
        console.log('Нет активной сессии визуализатора');
        return null;
    }
    
    try {
        const response = await fetch(`/api/visualize/get-state/${AppState.currentVisualizerToken}`);
        
        if (!response.ok) {
            console.warn('Не удалось получить состояние визуализатора:', response.status);
            return null;
        }
        
        const result = await response.json();
        
        if (result.success && result.state) {
            console.log('Получено состояние визуализатора:', result.state);
            return result.state;
        }
        
        return null;
    } catch (error) {
        console.error('Ошибка получения состояния визуализатора:', error);
        return null;
    }
},

    /**
     * Отмена состояния drag из палитры (helper)
     */
    cancelPaletteDrag() {
        if (AppState.dragPreview) {
            try { AppState.dragPreview.remove(); } catch (e) { /* ignore */ }
            AppState.dragPreview = null;
        }
        AppState.isDraggingFromPalette = false;
        AppState.dragType = null;
    },

    /**
     * Настройка счётчика выходов в меню
     */
    setupOutputCounter() {
        // Не создавать повторно, если уже есть
        if (document.getElementById('btn-outputs')) return;

        const menu = document.getElementById('menu');

        // Создаём кнопку с счётчиком выходов
        const outputBtn = document.createElement('button');
        outputBtn.className = 'menu-btn output-btn';
        outputBtn.id = 'btn-outputs';
        outputBtn.innerHTML = `
            📤 Выходы
            <span id="output-counter" class="output-counter">0</span>
        `;

        // Вставляем после кнопки свойств проекта
        const projectBtn = document.getElementById('btn-project-settings');
        if (projectBtn) {
            projectBtn.after(outputBtn);
        } else {
            menu.appendChild(outputBtn);
        }

        outputBtn.addEventListener('click', () => {
            Modal.showProjectPropertiesModal();
        });
    },

    /**
     * Настройка drag & drop из палитры
     */
    setupPaletteDragDrop() {
        document.querySelectorAll('.palette-item').forEach(item => {
            item.addEventListener('mousedown', (e) => {
                // Только левая кнопка мыши должна запускать drag из палитры
                if (e.button !== 0) return;
                e.preventDefault();

                AppState.isDraggingFromPalette = true;
                AppState.dragType = item.dataset.type;

                AppState.dragPreview = document.createElement('div');
                AppState.dragPreview.className = 'drag-preview';
                AppState.dragPreview.textContent = ELEMENT_TYPES[AppState.dragType]?.name || 'Элемент';
                AppState.dragPreview.style.left = `${e.clientX - 40}px`;
                AppState.dragPreview.style.top = `${e.clientY - 20}px`;
                document.body.appendChild(AppState.dragPreview);
            });
        });
    },

    /**
     * Глобальные обработчики мыши
     */
/**
 * Глобальные обработчики мыши
 */
setupGlobalMouseHandlers() {
    document.addEventListener('mousemove', (e) => {
        if (AppState.isDraggingFromPalette && AppState.dragPreview) {
            AppState.dragPreview.style.left = `${e.clientX - 40}px`;
            AppState.dragPreview.style.top = `${e.clientY - 20}px`;
        }
        if (AppState.resizing) {
            Elements.handleResize(e);
            return;
        }
        if (AppState.draggingElement) {
            Elements.handleDrag(e);
        }
        if (AppState.tempLine && AppState.connectingFrom) {
            Connections.drawTempConnection(e);
        }
    });

    document.addEventListener('mouseup', (e) => {
        if (AppState.resizing) {
            AppState.resizing = null;
            if (typeof Outputs !== 'undefined') Outputs.updateOutputStatus();
        }

        if (AppState.isDraggingFromPalette) {
            try {
                if (AppState.dragPreview) {
                    AppState.dragPreview.remove();
                    AppState.dragPreview = null;
                }

                const container = document.getElementById('workspace-container');
                const rect = container.getBoundingClientRect();

                if (e.clientX >= rect.left && e.clientX <= rect.right &&
                    e.clientY >= rect.top && e.clientY <= rect.bottom) {

                    const canvasPos = screenToCanvas(e.clientX, e.clientY);
                    const config = ELEMENT_TYPES[AppState.dragType];
                    if (config) {
                        const defaultWidth = config.minWidth || 120;
                        const defaultHeight = config.minHeight || 60;
                        
                        // ИСПРАВЛЕНО: addElement возвращает DOM-элемент, его надо обработать
                        const newElement = Elements.addElement(
                            AppState.dragType,
                            canvasPos.x - defaultWidth / 2,
                            canvasPos.y - defaultHeight / 2
                        );
                        
                        if (newElement && typeof Outputs !== 'undefined') {
                            Outputs.updateOutputStatus();
                        }
                    } else {
                        console.error('Неизвестный тип элемента при drop:', AppState.dragType);
                    }
                }
            } finally {
                App.cancelPaletteDrag();
            }
        }

        if (AppState.draggingElement) {
            AppState.draggingElement = null;
        }

        Connections.clearConnectionState();
    });

    document.addEventListener('keydown', (e) => {
    // 1. Проверяем, не печатает ли пользователь текст
        const target = e.target;
        const isInput = target.tagName === 'INPUT' || 
                        target.tagName === 'TEXTAREA' || 
                        target.isContentEditable;

        if (isInput) return; // Если печатаем - игнорируем глобальные хоткеи

        // 2. Проверяем, не открыто ли модальное окно
        const modal = document.getElementById('modal-overlay');
        const projectModal = document.getElementById('project-modal-overlay');
        const isModalOpen = (modal && modal.style.display !== 'none') || 
                            (projectModal && projectModal.style.display !== 'none');

        if (isModalOpen) return; // Если открыто окно - игнорируем

        // --- Дальше старая логика ---

        if (e.key === 'Delete' && AppState.selectedElement) {
            Elements.deleteElement(AppState.selectedElement);
            if (typeof Outputs !== 'undefined') Outputs.updateOutputStatus();
        }
        
        if (e.key === 'Escape') {
            Elements.deselectAll();
            Connections.clearConnectionState();
            if (AppState.isDraggingFromPalette) App.cancelPaletteDrag();
        }
    });
},

    /**
     * Настройка контекстного меню
     */
    setupContextMenu() {
        document.addEventListener('click', (e) => {
            const menu = document.getElementById('context-menu');
            if (!menu.contains(e.target)) {
                menu.style.display = 'none';
            }
        });

        document.getElementById('ctx-properties').addEventListener('click', () => {
            const elemId = document.getElementById('context-menu').dataset.elementId;
            document.getElementById('context-menu').style.display = 'none';
            const config = ELEMENT_TYPES[AppState.elements[elemId]?.type];
            if (config?.hasProperties) {
                Modal.showPropertiesModal(elemId);
            }
        });

        document.getElementById('ctx-delete').addEventListener('click', () => {
            document.getElementById('context-menu').style.display = 'none';
            
            // Используем новую функцию для удаления всех выделенных
            Elements.deleteSelectedElements();
            
            if (typeof Outputs !== 'undefined' && Outputs.updateOutputStatus) {
                Outputs.updateOutputStatus();
            }
        });
        document.getElementById('ctx-copy').addEventListener('click', () => {
            document.getElementById('context-menu').style.display = 'none';
            Elements.copySelectedElements();
        });
        // ===== НОВОЕ: Открыть проект сигнала =====
        document.getElementById('ctx-open-project').addEventListener('click', () => {
            const elemId = document.getElementById('context-menu').dataset.elementId;
            document.getElementById('context-menu').style.display = 'none';
            const elem = AppState.elements[elemId];
            if (elem && elem.type === 'input-signal' && elem.props?.name) {
                openSignalProject(elem.props.name.trim());
            }
        });
    },

    /**
     * Клик по рабочей области
     */
    // app.js
    // app.js
    setupWorkspaceClick() {
        const container = document.getElementById('workspace-container');

        container.addEventListener('click', (e) => {
            // Если мы только что закончили тянуть РАМКУ (реальное выделение), не сбрасываем
            if (AppState.marqueeJustEnded) return;

            // Если кликнули ЛЕВОЙ кнопкой мыши НЕ по элементу и НЕ по порту
            if (e.button === 0 && !e.target.closest('.element') && !e.target.closest('.port')) {
                Elements.deselectAll();
            }
        });
    },
    /**
 * --- Выделение рамкой и множественное перемещение ---
 */
    // app.js
    setupMultiSelection() {
        const container = document.getElementById('workspace-container');
        const rectEl = document.getElementById('selection-rect');

        container.addEventListener('mousedown', (e) => {
            // РАМКА: только ЛЕВАЯ кнопка (0) и клик НЕ по элементу
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
            const sx = AppState.selectionRect.startX;
            const sy = AppState.selectionRect.startY;
            
            const x = Math.min(sx, pos.x);
            const y = Math.min(sy, pos.y);
            const w = Math.abs(pos.x - sx);
            const h = Math.abs(pos.y - sy);

            // Обновляем визуальную рамку
            rectEl.style.left = (x * AppState.viewport.zoom + AppState.viewport.panX) + 'px';
            rectEl.style.top = (y * AppState.viewport.zoom + AppState.viewport.panY) + 'px';
            rectEl.style.width = (w * AppState.viewport.zoom) + 'px';
            rectEl.style.height = (h * AppState.viewport.zoom) + 'px';

            // Ищем элементы внутри
            const selected = [];
            for (const [id, elData] of Object.entries(AppState.elements)) {
                if (!elData || elData.type === 'output-frame') continue;
                if (elData.x >= x && elData.x + elData.width <= x + w &&
                    elData.y >= y && elData.y + elData.height <= y + h) {
                    selected.push(id);
                }
            }

            AppState.selectedElements = selected;
            AppState.selectedElement = selected.length > 0 ? selected[selected.length - 1] : null;

            document.querySelectorAll('.element').forEach(el => {
                el.classList.toggle('selected', selected.includes(el.id));
            });
        });

        document.addEventListener('mouseup', () => {
            if (AppState.multiSelecting) {
                AppState.multiSelecting = false;
                const rectEl = document.getElementById('selection-rect');
                const w = parseInt(rectEl.style.width) || 0;
                const h = parseInt(rectEl.style.height) || 0;
                rectEl.style.display = 'none';
                
                // Флаг, чтобы setupWorkspaceClick не сбросил выделение сразу
                if (w > 2 || h > 2) {
                    AppState.marqueeJustEnded = true;
                    setTimeout(() => { AppState.marqueeJustEnded = false; }, 50);
                }
            }
        });
    },
};

// Запуск приложения при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
