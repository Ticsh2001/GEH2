/**
 * Модуль управления viewport (масштабирование и перемещение)
 * viewport.js
 */

const Viewport = {
    /**
     * Инициализация viewport
     */
    init() {
        this.setupZoomControls();
        this.setupPanning();
        this.setupMouseWheel();
        this.setupMinimap();
        this.setupCursorPosition();
        this.updateTransform();
        const container = document.getElementById('workspace-container');
        const rect = container.getBoundingClientRect();
        AppState.viewport.panX = 100; // немного отступить от левого края
        AppState.viewport.panY = (rect.height / 2) - 2500 * 0.5 * AppState.viewport.zoom;
        this.updateTransform();
    },

    /**
     * Настройка кнопок масштабирования
     */
    setupZoomControls() {
        document.getElementById('btn-zoom-in').addEventListener('click', () => {
            this.setZoom(AppState.viewport.zoom + VIEWPORT_CONFIG.zoomStep);
        });

        document.getElementById('btn-zoom-out').addEventListener('click', () => {
            this.setZoom(AppState.viewport.zoom - VIEWPORT_CONFIG.zoomStep);
        });

        document.getElementById('btn-zoom-reset').addEventListener('click', () => {
            this.setZoom(1);
            this.setPan(0, 0);
        });

        document.getElementById('btn-zoom-fit').addEventListener('click', () => {
            this.fitToContent();
        });
    },

    /**
     * Настройка перемещения (pan)
     */
    setupPanning() {
        const container = document.getElementById('workspace-container');

        container.addEventListener('mousedown', (e) => {
            // Средняя кнопка мыши или пробел + левая кнопка
            if (e.button === 1 || (e.button === 0 && e.target === container)) {
                e.preventDefault();
                AppState.viewport.isPanning = true;
                AppState.viewport.lastMouseX = e.clientX;
                AppState.viewport.lastMouseY = e.clientY;
                container.style.cursor = 'grabbing';
            }
        });

        document.addEventListener('mousemove', (e) => {
            if (AppState.viewport.isPanning) {
                const dx = e.clientX - AppState.viewport.lastMouseX;
                const dy = e.clientY - AppState.viewport.lastMouseY;

                this.setPan(
                    AppState.viewport.panX + dx,
                    AppState.viewport.panY + dy
                );

                AppState.viewport.lastMouseX = e.clientX;
                AppState.viewport.lastMouseY = e.clientY;
            }
        });

        document.addEventListener('mouseup', (e) => {
            if (AppState.viewport.isPanning) {
                AppState.viewport.isPanning = false;
                document.getElementById('workspace-container').style.cursor = '';
            }
        });

        // Клавиша пробел для режима перемещения
        document.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && !e.repeat) {
                document.getElementById('workspace-container').style.cursor = 'grab';
            }
        });

        document.addEventListener('keyup', (e) => {
            if (e.code === 'Space') {
                document.getElementById('workspace-container').style.cursor = '';
            }
        });
    },

    /**
     * Настройка масштабирования колесом мыши
     */
    setupMouseWheel() {
        const container = document.getElementById('workspace-container');

        container.addEventListener('wheel', (e) => {
            e.preventDefault();

            const rect = container.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            const mouseY = e.clientY - rect.top;

            // Позиция мыши на холсте до масштабирования
            const canvasPosBeforeX = (mouseX - AppState.viewport.panX) / AppState.viewport.zoom;
            const canvasPosBeforeY = (mouseY - AppState.viewport.panY) / AppState.viewport.zoom;

            // Новый масштаб
            const delta = e.deltaY > 0 ? -VIEWPORT_CONFIG.zoomStep : VIEWPORT_CONFIG.zoomStep;
            const newZoom = Math.max(
                VIEWPORT_CONFIG.minZoom,
                Math.min(VIEWPORT_CONFIG.maxZoom, AppState.viewport.zoom + delta)
            );

            // Корректируем pan, чтобы точка под курсором осталась на месте
            const newPanX = mouseX - canvasPosBeforeX * newZoom;
            const newPanY = mouseY - canvasPosBeforeY * newZoom;

            AppState.viewport.zoom = newZoom;
            AppState.viewport.panX = newPanX;
            AppState.viewport.panY = newPanY;

            this.updateTransform();
        }, { passive: false });
    },

    /**
     * Установить масштаб
     */
    setZoom(zoom) {
        const container = document.getElementById('workspace-container');
        const rect = container.getBoundingClientRect();

        // Центр экрана
        const centerX = rect.width / 2;
        const centerY = rect.height / 2;

        // Позиция центра на холсте
        const canvasCenterX = (centerX - AppState.viewport.panX) / AppState.viewport.zoom;
        const canvasCenterY = (centerY - AppState.viewport.panY) / AppState.viewport.zoom;

        // Новый масштаб
        const newZoom = Math.max(
            VIEWPORT_CONFIG.minZoom,
            Math.min(VIEWPORT_CONFIG.maxZoom, zoom)
        );

        // Корректируем pan
        AppState.viewport.panX = centerX - canvasCenterX * newZoom;
        AppState.viewport.panY = centerY - canvasCenterY * newZoom;
        AppState.viewport.zoom = newZoom;

        this.updateTransform();
    },

    /**
     * Установить смещение
     */
    setPan(x, y) {
        AppState.viewport.panX = x;
        AppState.viewport.panY = y;
        this.updateTransform();
    },

    /**
     * Вписать содержимое в экран
     */
    fitToContent() {
        const elements = Object.values(AppState.elements);
        if (elements.length === 0) {
            this.setZoom(1);
            this.setPan(0, 0);
            return;
        }

        // Находим границы содержимого
        let minX = Infinity, minY = Infinity;
        let maxX = -Infinity, maxY = -Infinity;

        elements.forEach(elem => {
            minX = Math.min(minX, elem.x);
            minY = Math.min(minY, elem.y);
            maxX = Math.max(maxX, elem.x + elem.width);
            maxY = Math.max(maxY, elem.y + elem.height);
        });

        const contentWidth = maxX - minX;
        const contentHeight = maxY - minY;

        const container = document.getElementById('workspace-container');
        const rect = container.getBoundingClientRect();

        const padding = 50;
        const availableWidth = rect.width - padding * 2;
        const availableHeight = rect.height - padding * 2;

        const zoomX = availableWidth / contentWidth;
        const zoomY = availableHeight / contentHeight;
        const newZoom = Math.min(zoomX, zoomY, 1);

        AppState.viewport.zoom = Math.max(VIEWPORT_CONFIG.minZoom, newZoom);
        AppState.viewport.panX = padding - minX * AppState.viewport.zoom + (availableWidth - contentWidth * AppState.viewport.zoom) / 2;
        AppState.viewport.panY = padding - minY * AppState.viewport.zoom + (availableHeight - contentHeight * AppState.viewport.zoom) / 2;

        this.updateTransform();
    },

    /**
     * Обновить трансформацию
     */
    updateTransform() {
        const workspace = document.getElementById('workspace');
        const svg = document.getElementById('connections-svg');

        const transform = `translate(${AppState.viewport.panX}px, ${AppState.viewport.panY}px) scale(${AppState.viewport.zoom})`;

        workspace.style.transform = transform;
        svg.style.transform = transform;

        // Обновляем отображение масштаба
        document.getElementById('zoom-level').textContent = `${Math.round(AppState.viewport.zoom * 100)}%`;

        // Обновляем мини-карту
        this.updateMinimap();
    },

    /**
     * Настройка мини-карты
     */
    setupMinimap() {
        const minimap = document.getElementById('minimap');
        const canvas = document.getElementById('minimap-canvas');

        canvas.width = MINIMAP_CONFIG.width;
        canvas.height = MINIMAP_CONFIG.height;

        // Клик по мини-карте для перемещения
        minimap.addEventListener('click', (e) => {
            const rect = minimap.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            this.navigateToMinimapPosition(x, y);
        });
    },

    /**
     * Обновить мини-карту
     */
    updateMinimap() {
        const canvas = document.getElementById('minimap-canvas');
        const ctx = canvas.getContext('2d');
        const viewportEl = document.getElementById('minimap-viewport');

        // Очищаем
        ctx.fillStyle = '#0a0a1a';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // Масштаб мини-карты
        const scale = Math.min(
            canvas.width / VIEWPORT_CONFIG.canvasWidth,
            canvas.height / VIEWPORT_CONFIG.canvasHeight
        );

        // Рисуем элементы
        Object.values(AppState.elements).forEach(elem => {
            const x = elem.x * scale;
            const y = elem.y * scale;
            const w = Math.max(elem.width * scale, 2);
            const h = Math.max(elem.height * scale, 2);

            ctx.fillStyle = ELEMENT_TYPES[elem.type]?.color || '#4a90d9';
            ctx.fillRect(x, y, w, h);
        });

        // Рисуем viewport
        const container = document.getElementById('workspace-container');
        const rect = container.getBoundingClientRect();

        const vpX = (-AppState.viewport.panX / AppState.viewport.zoom) * scale;
        const vpY = (-AppState.viewport.panY / AppState.viewport.zoom) * scale;
        const vpW = (rect.width / AppState.viewport.zoom) * scale;
        const vpH = (rect.height / AppState.viewport.zoom) * scale;

        viewportEl.style.left = `${vpX}px`;
        viewportEl.style.top = `${vpY}px`;
        viewportEl.style.width = `${vpW}px`;
        viewportEl.style.height = `${vpH}px`;
    },

    /**
     * Перейти к позиции на мини-карте
     */
    navigateToMinimapPosition(minimapX, minimapY) {
        const canvas = document.getElementById('minimap-canvas');
        const container = document.getElementById('workspace-container');
        const rect = container.getBoundingClientRect();

        const scale = Math.min(
            canvas.width / VIEWPORT_CONFIG.canvasWidth,
            canvas.height / VIEWPORT_CONFIG.canvasHeight
        );

        const canvasX = minimapX / scale;
        const canvasY = minimapY / scale;

        // Центрируем viewport на этой точке
        AppState.viewport.panX = rect.width / 2 - canvasX * AppState.viewport.zoom;
        AppState.viewport.panY = rect.height / 2 - canvasY * AppState.viewport.zoom;

        this.updateTransform();
    },

    /**
     * Отслеживание позиции курсора
     */
    setupCursorPosition() {
        const container = document.getElementById('workspace-container');

        container.addEventListener('mousemove', (e) => {
            const pos = screenToCanvas(e.clientX, e.clientY);
            document.getElementById('cursor-pos').textContent =
                `X: ${Math.round(pos.x)}, Y: ${Math.round(pos.y)}`;
        });
    }
};