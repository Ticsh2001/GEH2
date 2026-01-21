/**
 * Главный модуль приложения
 */

const App = {
    /**
     * Инициализация приложения
     */
    init() {
        Settings.init().catch(console.error);
        //Settings.init().then(() => {
        //    // если хочешь — можно обновить UI (например, статус “Сигналы загружены”)
        //    console.log('Settings loaded, signals:', Settings.signals.length);
        //    }).catch(err => console.error(err));
        //console.log('signals loaded:', Settings.signals.slice(0, 5));
        this.setupPaletteDragDrop();
        this.setupGlobalMouseHandlers();
        this.setupContextMenu();
        this.setupWorkspaceClick();
        this.setupOutputCounter();
        this.setupMultiSelection();

        // Инициализация модулей
        Viewport.init();
        Modal.init();
        Project.init();

        // Первоначальное определение выходов (только если модуль загружен)
        if (typeof Outputs !== 'undefined' && Outputs.updateOutputStatus) {
            Outputs.updateOutputStatus();
        }

        console.log('Logic Scheme Editor initialized');
        document.getElementById('btn-generate-code').addEventListener('click', () => {
            const code = CodeGen.generate();
            document.getElementById('code-output').value = code;
            document.getElementById('code-modal-overlay').style.display = 'flex';
        });

        document.getElementById('code-modal-close').addEventListener('click', () => {
            document.getElementById('code-modal-overlay').style.display = 'none';
        });
        document.getElementById('btn-visualize').addEventListener('click', () => {
            App.openSignalVisualizer();
});        
    },

    openSignalVisualizer() {
        try {
            // 1) Собираем входные сигналы
            const signals = Object.values(AppState.elements)
            .filter(e => e && e.type === 'input-signal')
            .map(e => e.props?.name || e.id);
            const uniqSignals = [...new Set(signals)];
            if (uniqSignals.length === 0) {
            alert('Нет входных сигналов в схеме.');
            return;
            }

            // 2) Генерируем код (может быть длинным)
            let codeStr = '';
            if (typeof CodeGen !== 'undefined' && typeof CodeGen.generate === 'function') {
            codeStr = CodeGen.generate() || '';
            }

            // 3) Создаём сессию на backend, чтобы не тащить код в URL
            fetch('/api/visualize/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ signals: uniqSignals, code: codeStr })
            })
            .then(r => {
            if (!r.ok) throw new Error('Failed to create visualize session');
            return r.json();
            })
            .then(data => {
            const token = data.token;
            const apiUrl = window.location.origin; // http://localhost:8000
            const params = new URLSearchParams();
            params.set('session', token);
            params.set('api_url', apiUrl);
            // signals можно не передавать — визуализатор возьмет их из session
            const visualizerUrl = `http://localhost:8501?${params.toString()}`;
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
