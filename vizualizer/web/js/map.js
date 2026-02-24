class ProjectMap {
    constructor() {
        this.canvas = document.getElementById('map-canvas');
        this.ctx = this.canvas.getContext('2d');
        this.loading = document.getElementById('loading');
        
        // Состояние карты
        this.nodes = [];
        this.connections = [];
        this.levels = new Map();
        this.selectedNode = null;
        this.hoveredNode = null;
        
        // Параметры отображения
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.isDragging = false;
        this.lastMouseX = 0;
        this.lastMouseY = 0;
        
        // Параметры визуализации
        this.nodeWidth = 180;
        this.nodeHeight = 65;
        this.levelSpacing = 320;
        this.nodeSpacing = 70;
        
        this.initCanvas();
        this.setupEventListeners();
        this.loadData();
    }

    initCanvas() {
        this.resizeCanvas();
        window.addEventListener('resize', () => this.resizeCanvas());
    }

    resizeCanvas() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width;
        this.canvas.height = rect.height;
        this.draw();
    }

    setupEventListeners() {
        // Управление камерой
        this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        this.canvas.addEventListener('wheel', (e) => this.handleWheel(e));
        this.canvas.addEventListener('contextmenu', (e) => this.handleContextMenu(e));

        // Кнопки управления
        document.getElementById('btn-refresh').addEventListener('click', () => this.loadData());
        document.getElementById('btn-fit').addEventListener('click', () => this.fitAll());
        document.getElementById('btn-reset-zoom').addEventListener('click', () => this.resetZoom());

        // Контекстное меню
        document.getElementById('ctx-open-project').addEventListener('click', () => this.openSelectedProject());
        document.getElementById('ctx-show-details').addEventListener('click', () => this.showNodeDetails());
        
        // Закрытие контекстного меню
        document.addEventListener('click', (e) => {
            if (!document.getElementById('context-menu').contains(e.target)) {
                document.getElementById('context-menu').style.display = 'none';
            }
        });
    }

    async loadData() {
        this.loading.style.display = 'block';
        
        try {
            console.log('[map] Loading projects and signals...');
            
            // Получаем все проекты
            const projectsResponse = await fetch('/api/project/list');
            const projectsData = await projectsResponse.json();
            const allProjects = projectsData.projects || [];
            
            console.log('[map] Projects loaded:', allProjects.length);

            // Получаем базовые сигналы (попробуем получить все без фильтра)
            const signalsResponse = await fetch('/api/signals?limit=10000');
            const signalsData = await signalsResponse.json();
            const baseSignals = new Set((signalsData.items || []).map(s => s.Tagname));
            
            console.log('[map] Base signals loaded:', baseSignals.size);
            
            // Строим граф зависимостей
            await this.buildDependencyGraph(allProjects, baseSignals);
            
            this.loading.style.display = 'none';
            this.updateLevelInfo();
            this.draw();
            
        } catch (error) {
            console.error('[map] Error loading data:', error);
            this.loading.textContent = 'Ошибка загрузки данных: ' + error.message;
        }
    }

    async buildDependencyGraph(projects, baseSignals) {
        console.log('[map] Building dependency graph...');
        
        this.nodes = [];
        this.connections = [];
        this.levels.clear();

        // Создаем узлы для базовых сигналов (уровень 0)
        let nodeId = 0;
        const signalToNode = new Map();
        
        // Собираем все используемые сигналы из проектов
        const usedSignals = new Set();
        for (const project of projects) {
            const inputSignals = await this.getProjectInputSignals(project);
            inputSignals.forEach(sig => usedSignals.add(sig));
        }

        // Создаем узлы только для используемых базовых сигналов
        for (const signal of usedSignals) {
            if (baseSignals.has(signal)) {
                const node = {
                    id: nodeId++,
                    name: signal,
                    type: 'base-signal',
                    level: 0,
                    x: 0,
                    y: 0,
                    project: null
                };
                this.nodes.push(node);
                signalToNode.set(signal, node);
                this.addToLevel(0, node);
            }
        }

        // Создаем узлы для проектов и определяем их уровни
        const projectNodes = new Map();
        for (const project of projects) {
            const node = {
                id: nodeId++,
                name: project.code || 'Unnamed',
                type: 'project',
                level: -1, // будет определен позже
                x: 0,
                y: 0,
                project: project,
                inputSignals: await this.getProjectInputSignals(project)
            };
            this.nodes.push(node);
            projectNodes.set(node.name, node);
            signalToNode.set(node.name, node);
        }

        // Определяем уровни проектов итеративно
        this.calculateProjectLevels(projectNodes, signalToNode, baseSignals);

        // Создаем связи
        for (const [projectName, node] of projectNodes) {
            for (const inputSignal of node.inputSignals) {
                const sourceNode = signalToNode.get(inputSignal);
                if (sourceNode && sourceNode !== node) {
                    this.connections.push({
                        from: sourceNode,
                        to: node
                    });
                }
            }
        }

        // Позиционируем узлы по уровням
        this.positionNodes();
        
        console.log('[map] Graph built:', {
            nodes: this.nodes.length,
            connections: this.connections.length,
            levels: this.levels.size
        });
    }

    async getProjectInputSignals(project) {
        try {
            // Загружаем полные данные проекта
            const response = await fetch(`/api/project/load/${project.filename}?source=${project.source || 'projects'}`);
            if (!response.ok) return [];
            
            const data = await response.json();
            const elements = data.elements || {};
            
            const inputSignals = [];
            Object.values(elements).forEach(el => {
                if (el && el.type === 'input-signal' && el.props?.name) {
                    inputSignals.push(el.props.name.trim());
                }
            });
            
            return [...new Set(inputSignals)]; // уникальные
        } catch (error) {
            console.warn('[map] Error loading project signals for', project.filename, ':', error);
            return [];
        }
    }

    calculateProjectLevels(projectNodes, signalToNode, baseSignals) {
        const maxIterations = 50;
        let changed = true;
        let iteration = 0;

        // Инициализируем уровни проектов
        for (const [name, node] of projectNodes) {
            node.level = 1; // начальный уровень
        }

        while (changed && iteration < maxIterations) {
            changed = false;
            iteration++;

            for (const [name, node] of projectNodes) {
                let maxInputLevel = 0;
                
                for (const inputSignal of node.inputSignals) {
                    const inputNode = signalToNode.get(inputSignal);
                    if (inputNode) {
                        if (inputNode.type === 'base-signal') {
                            maxInputLevel = Math.max(maxInputLevel, 0);
                        } else if (inputNode.type === 'project' && inputNode.level >= 0) {
                            maxInputLevel = Math.max(maxInputLevel, inputNode.level);
                        }
                    }
                }

                const newLevel = maxInputLevel + 1;
                if (newLevel !== node.level) {
                    node.level = newLevel;
                    changed = true;
                }
            }
        }

        // Добавляем узлы к их уровням
        for (const [name, node] of projectNodes) {
            this.addToLevel(node.level, node);
        }

        console.log('[map] Level calculation completed in', iteration, 'iterations');
    }

    addToLevel(level, node) {
        if (!this.levels.has(level)) {
            this.levels.set(level, []);
        }
        this.levels.get(level).push(node);
    }

    positionNodes() {
        const maxLevel = Math.max(...this.levels.keys());
        
        for (let level = 0; level <= maxLevel; level++) {
            const nodes = this.levels.get(level) || [];
            const startY = -(nodes.length * (this.nodeHeight + this.nodeSpacing)) / 2;
            
            nodes.forEach((node, index) => {
                node.x = level * this.levelSpacing;
                node.y = startY + index * (this.nodeHeight + this.nodeSpacing);
            });
        }
    }

    // Добавьте эти методы к классу ProjectMap в web/js/map.js после существующих:

    // === МЕТОДЫ ОТРИСОВКИ ===
    draw() {
        const ctx = this.ctx;
        const canvas = this.canvas;
        
        // Очищаем canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Применяем трансформацию
        ctx.save();
        ctx.translate(this.offsetX, this.offsetY);
        ctx.scale(this.zoom, this.zoom);
        
        // Рисуем сетку
        this.drawGrid();
        
        // Рисуем соединения (под узлами)
        this.connections.forEach(conn => this.drawConnection(conn));
        
        // Рисуем узлы
        this.nodes.forEach(node => this.drawNode(node));
        
        ctx.restore();
        
        // Обновляем индикатор масштаба
        document.getElementById('zoom-indicator').textContent = 
            Math.round(this.zoom * 100) + '%';
    }
    
    drawGrid() {
        const ctx = this.ctx;
        const gridSize = 50;
        const canvasWidth = this.canvas.width / this.zoom;
        const canvasHeight = this.canvas.height / this.zoom;
        const startX = Math.floor(-this.offsetX / this.zoom / gridSize) * gridSize;
        const startY = Math.floor(-this.offsetY / this.zoom / gridSize) * gridSize;
        
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
        ctx.lineWidth = 1;
        
        // Вертикальные линии
        for (let x = startX; x < canvasWidth - this.offsetX / this.zoom; x += gridSize) {
            ctx.beginPath();
            ctx.moveTo(x, startY);
            ctx.lineTo(x, canvasHeight - this.offsetY / this.zoom);
            ctx.stroke();
        }
        
        // Горизонтальные линии
        for (let y = startY; y < canvasHeight - this.offsetY / this.zoom; y += gridSize) {
            ctx.beginPath();
            ctx.moveTo(startX, y);
            ctx.lineTo(canvasWidth - this.offsetX / this.zoom, y);
            ctx.stroke();
        }
    }
    
    drawNode(node) {
    const ctx = this.ctx;
    const x = node.x;
    const y = node.y;
    const w = this.nodeWidth;
    const h = this.nodeHeight;
    
    // Стандартные цвета из редактора
    let fillColor = '#0f3460';  // стандартный фон элементов
    let borderColor = '#4a90d9'; // стандартная рамка
    let headerColor = '#4a90d9'; // цвет заголовка
    
    // Определяем цвет заголовка по типу
    if (node.type === 'base-signal') {
        headerColor = '#4a90d9'; // синий для входных сигналов
    } else {
        switch (node.level) {
            case 1:
                headerColor = '#10b981'; // зелёный
                break;
            case 2:
                headerColor = '#f59e0b'; // оранжевый
                break;
            default:
                headerColor = '#ef4444'; // красный
        }
    }
    
    // Подсветка при выборе/наведении
    const isHighlighted = this.isNodeHighlighted(node);
    if (node === this.selectedNode || isHighlighted) {
        borderColor = '#e94560';
        // Добавим эффект свечения
        ctx.shadowColor = 'rgba(233, 69, 96, 0.5)';
        ctx.shadowBlur = 15;
    } else if (node === this.hoveredNode) {
        borderColor = '#e94560';
    }
    
    if (node.type === 'base-signal') {
        // Рисуем трапецию для базовых сигналов (как input-signal в редакторе)
        this.drawTrapezoid(ctx, x, y, w, h, fillColor, borderColor, headerColor, node);
    } else {
        // Рисуем прямоугольник с закруглёнными углами для проектов
        this.drawRoundedRect(ctx, x, y, w, h, fillColor, borderColor, headerColor, node);
    }
    
    // Сбрасываем эффекты
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
}

drawTrapezoid(ctx, x, y, w, h, fillColor, borderColor, headerColor, node) {
    // Создаём трапецию как в редакторе: polygon(0 0, 80% 0, 100% 50%, 80% 100%, 0 100%)
    const trapezoidWidth = w * 0.8; // 80% ширины до скоса
    const tipWidth = w * 0.2;       // 20% на заострённый кончик
    
    ctx.beginPath();
    ctx.moveTo(x, y);                           // левый верх
    ctx.lineTo(x + trapezoidWidth, y);          // правый верх (80%)
    ctx.lineTo(x + w, y + h/2);                 // кончик (100%, середина)
    ctx.lineTo(x + trapezoidWidth, y + h);      // правый низ (80%)
    ctx.lineTo(x, y + h);                       // левый низ
    ctx.closePath();
    
    // Заливка
    ctx.fillStyle = fillColor;
    ctx.fill();
    
    // Рамка
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 2;
    ctx.stroke();
    
    // Рисуем иконку и текст
    this.drawNodeContent(ctx, x, y, w, h, node, '#eee');
}

drawRoundedRect(ctx, x, y, w, h, fillColor, borderColor, headerColor, node) {
    const radius = 8; // как в CSS border-radius: 8px
    
    // Рисуем закруглённый прямоугольник
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + w - radius, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
    ctx.lineTo(x + w, y + h - radius);
    ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    ctx.lineTo(x + radius, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
    
    // Заливка основного блока
    ctx.fillStyle = fillColor;
    ctx.fill();
    
    // Рамка основного блока
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 2;
    ctx.stroke();
    
    // Рисуем заголовок (увеличим высоту заголовка)
    const headerHeight = 26; // увеличили с 22 до 26
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + w - radius, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
    ctx.lineTo(x + w, y + headerHeight);
    ctx.lineTo(x, y + headerHeight);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
    
    ctx.fillStyle = headerColor;
    ctx.fill();
    
    // Текст заголовка
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 12px "Segoe UI", sans-serif'; // немного увеличили шрифт заголовка
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    
    const headerText = node.type === 'project' ? 'Проект' : 'Элемент';
    ctx.fillText(headerText, x + w/2, y + headerHeight/2);
    
    // Содержимое элемента (больше места под текст)
    this.drawNodeContent(ctx, x, y + headerHeight, w, h - headerHeight, node, '#eee');
}

drawNodeContent(ctx, x, y, w, h, node, textColor) {
    ctx.fillStyle = textColor;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    
    // Иконка и имя
    let icon = '';
    if (node.type === 'base-signal') {
        icon = '🔢'; // для базовых сигналов
    } else {
        // Иконки для проектов по уровням
        switch (node.level) {
            case 1: icon = '📊'; break;
            case 2: icon = '📋'; break; 
            default: icon = '🧩'; break;
        }
    }
    
    // Рисуем иконку (немного сдвинем вверх)
    ctx.font = '18px "Segoe UI", sans-serif';
    ctx.fillText(icon, x + 25, y + h/2 - 12);
    
    // Рисуем имя проекта/сигнала (УВЕЛИЧЕННЫЙ ШРИФТ)
    ctx.font = 'bold 14px "Segoe UI", sans-serif'; // увеличили с 11px до 14px и сделали жирным
    ctx.fillStyle = textColor;
    
    let displayName = node.name;
    const maxChars = node.type === 'base-signal' ? 16 : 18; // увеличили лимит символов
    if (displayName.length > maxChars) {
        displayName = displayName.substring(0, maxChars - 1) + '…';
    }
    
    ctx.fillText(displayName, x + w/2, y + h/2 + 5); // центрируем по вертикали
    
    // Показываем уровень для проектов (сдвинем ниже)
    if (node.type === 'project') {
        ctx.font = '10px "Segoe UI", sans-serif';
        ctx.fillStyle = '#aaa';
        ctx.fillText(`Уровень ${node.level}`, x + w/2, y + h - 8); // разместим внизу карточки
    }
}

isNodeHighlighted(node) {
    if (!this.selectedNode || this.selectedNode === node) return false;
    
    // Подсвечиваем узлы, связанные с выбранным
    for (const conn of this.connections) {
        if ((conn.from === this.selectedNode && conn.to === node) ||
            (conn.to === this.selectedNode && conn.from === node)) {
            return true;
        }
    }
    return false;
}


    
drawConnection(conn) {
    const ctx = this.ctx;
    const from = conn.from;
    const to = conn.to;
    
    // Определяем точки соединения
    let fromX, fromY, toX, toY;
    
    if (from.type === 'base-signal') {
        // Для трапеции - выход справа (кончик)
        fromX = from.x + this.nodeWidth;
        fromY = from.y + this.nodeHeight / 2;
    } else {
        // Для обычных элементов - выход справа
        fromX = from.x + this.nodeWidth;
        fromY = from.y + this.nodeHeight / 2;
    }
    
    // Вход всегда слева
    toX = to.x;
    toY = to.y + this.nodeHeight / 2;
    
    // Цвет и толщина линии
    let strokeColor = '#4a90d9';
    let lineWidth = 2;
    
    // Подсветка если один из узлов выбран или связанных
    if (this.selectedNode === from || this.selectedNode === to || 
        this.isNodeHighlighted(from) || this.isNodeHighlighted(to)) {
        strokeColor = '#e94560';
        lineWidth = 3;
    }
    
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    
    // Рисуем кривую Безье (как в редакторе)
    const controlOffset = Math.min(80, Math.abs(toX - fromX) / 3);
    ctx.moveTo(fromX, fromY);
    ctx.bezierCurveTo(
        fromX + controlOffset, fromY,    // контрольная точка 1
        toX - controlOffset, toY,        // контрольная точка 2
        toX, toY                         // конечная точка
    );
    ctx.stroke();
    
    // Рисуем стрелку на входе
    //this.drawArrow(ctx, toX, toY, strokeColor);
}
    
drawArrow(ctx, x, y, color) {
    const size = 6;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - size*2, y - size);
    ctx.lineTo(x - size*2, y + size);
    ctx.closePath();
    ctx.fill();
}

updateSelectionInfo(node) {
    const info = document.getElementById('level-info');
    if (!node) {
        info.innerHTML = '<strong>Карта зависимостей проектов</strong><br>Кликните на узел для просмотра связей';
        return;
    }
    
    // Считаем связи
    const incoming = this.connections.filter(c => c.to === node);
    const outgoing = this.connections.filter(c => c.from === node);
    
    let details = `<strong>${node.name}</strong><br>`;
    details += `Тип: ${node.type === 'base-signal' ? 'Базовый сигнал' : 'Проект'}<br>`;
    if (node.type === 'project') {
        details += `Уровень: ${node.level}<br>`;
    }
    details += `Входящих связей: ${incoming.length}<br>`;
    details += `Исходящих связей: ${outgoing.length}`;
    
    info.innerHTML = details;
}
    
// Также обновите handleMouseDown для лучшего выделения связанных узлов:
handleMouseDown(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    
    // Преобразуем в координаты canvas
    const canvasX = (x - this.offsetX) / this.zoom;
    const canvasY = (y - this.offsetY) / this.zoom;
    
    // Проверяем клик по узлу
    const clickedNode = this.getNodeAt(canvasX, canvasY);
    
    if (clickedNode) {
        if (e.button === 0) { // левая кнопка
            // Переключаем выделение
            this.selectedNode = this.selectedNode === clickedNode ? null : clickedNode;
            this.draw();
            
            // Обновляем информацию о выбранном узле
            this.updateSelectionInfo(clickedNode);
        }
    } else {
        // Начинаем перетаскивание карты
        if (e.button === 0) {
            this.isDragging = true;
            this.lastMouseX = x;
            this.lastMouseY = y;
            this.canvas.style.cursor = 'grabbing';
            this.selectedNode = null;
            this.updateSelectionInfo(null);
            this.draw();
        }
    }
}
    
    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        if (this.isDragging) {
            // Перетаскивание карты
            const dx = x - this.lastMouseX;
            const dy = y - this.lastMouseY;
            
            this.offsetX += dx;
            this.offsetY += dy;
            
            this.lastMouseX = x;
            this.lastMouseY = y;
            
            this.draw();
        } else {
            // Проверяем наведение на узлы
            const canvasX = (x - this.offsetX) / this.zoom;
            const canvasY = (y - this.offsetY) / this.zoom;
            const hoveredNode = this.getNodeAt(canvasX, canvasY);
            
            if (hoveredNode !== this.hoveredNode) {
                this.hoveredNode = hoveredNode;
                this.canvas.style.cursor = hoveredNode ? 'pointer' : 'grab';
                this.draw();
            }
        }
    }
    
    handleMouseUp(e) {
        this.isDragging = false;
        this.canvas.style.cursor = this.hoveredNode ? 'pointer' : 'grab';
    }
    
    handleWheel(e) {
        e.preventDefault();
        
        const rect = this.canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        
        // Zoom к позиции курсора
        const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
        const newZoom = Math.max(0.1, Math.min(5, this.zoom * zoomFactor));
        
        if (newZoom !== this.zoom) {
            // Корректируем смещение чтобы зум был к курсору
            this.offsetX = mouseX - (mouseX - this.offsetX) * (newZoom / this.zoom);
            this.offsetY = mouseY - (mouseY - this.offsetY) * (newZoom / this.zoom);
            this.zoom = newZoom;
            
            this.draw();
        }
    }
    
    handleContextMenu(e) {
        e.preventDefault();
        
        const rect = this.canvas.getBoundingClientRect();
        const canvasX = (e.clientX - rect.left - this.offsetX) / this.zoom;
        const canvasY = (e.clientY - rect.top - this.offsetY) / this.zoom;
        
        const clickedNode = this.getNodeAt(canvasX, canvasY);
        
        if (clickedNode && clickedNode.type === 'project') {
            this.selectedNode = clickedNode;
            this.showContextMenu(e.clientX, e.clientY);
            this.draw();
        }
    }
    
    getNodeAt(x, y) {
        for (const node of this.nodes) {
            if (x >= node.x && x <= node.x + this.nodeWidth &&
                y >= node.y && y <= node.y + this.nodeHeight) {
                return node;
            }
        }
        return null;
    }
    
    showContextMenu(x, y) {
        const menu = document.getElementById('context-menu');
        menu.style.left = `${x}px`;
        menu.style.top = `${y}px`;
        menu.style.display = 'block';
    }
    
    // === НАВИГАЦИЯ ===
    fitAll() {
        if (this.nodes.length === 0) return;
        
        // Находим границы всех узлов
        let minX = Infinity, minY = Infinity;
        let maxX = -Infinity, maxY = -Infinity;
        
        for (const node of this.nodes) {
            minX = Math.min(minX, node.x);
            minY = Math.min(minY, node.y);
            maxX = Math.max(maxX, node.x + this.nodeWidth);
            maxY = Math.max(maxY, node.y + this.nodeHeight);
        }
        
        const padding = 50;
        const contentWidth = maxX - minX + padding * 2;
        const contentHeight = maxY - minY + padding * 2;
        
        // Рассчитываем масштаб
        const scaleX = this.canvas.width / contentWidth;
        const scaleY = this.canvas.height / contentHeight;
        const newZoom = Math.min(scaleX, scaleY, 1);
        
        // Центрируем
        const centerX = (minX + maxX) / 2;
        const centerY = (minY + maxY) / 2;
        
        this.zoom = newZoom;
        this.offsetX = this.canvas.width / 2 - centerX * newZoom;
        this.offsetY = this.canvas.height / 2 - centerY * newZoom;
        
        this.draw();
    }
    
    resetZoom() {
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.draw();
    }
    
    // === КОНТЕКСТНОЕ МЕНЮ ===
    openSelectedProject() {
        if (this.selectedNode && this.selectedNode.project) {
            const project = this.selectedNode.project;
            const url = `/?load=${encodeURIComponent(project.filename)}&source=${encodeURIComponent(project.source || 'projects')}`;
            window.open(url, '_blank');
        }
        document.getElementById('context-menu').style.display = 'none';
    }
    
    showNodeDetails() {
        if (this.selectedNode) {
            const node = this.selectedNode;
            let details = `Узел: ${node.name}\n`;
            details += `Тип: ${node.type === 'base-signal' ? 'Базовый сигнал' : 'Проект'}\n`;
            details += `Уровень: ${node.level}\n`;
            
            if (node.inputSignals && node.inputSignals.length > 0) {
                details += `Входные сигналы:\n${node.inputSignals.map(s => `  • ${s}`).join('\n')}`;
            }
            
            alert(details);
        }
        document.getElementById('context-menu').style.display = 'none';
    }
    
    // === ОБНОВЛЕНИЕ ИНФОРМАЦИИ ===
    updateLevelInfo() {
        const levelCounts = new Map();
        
        for (const node of this.nodes) {
            const level = node.level;
            levelCounts.set(level, (levelCounts.get(level) || 0) + 1);
        }
        
        const levels = Array.from(levelCounts.keys()).sort((a, b) => a - b);
        const details = levels.map(level => {
            const count = levelCounts.get(level);
            const type = level === 0 ? 'базовых сигналов' : `проектов уровня ${level}`;
            return `Уровень ${level}: ${count} ${type}`;
        }).join('<br>');
        
        document.getElementById('level-details').innerHTML = details;
    }
}

// === ИНИЦИАЛИЗАЦИЯ ===
document.addEventListener('DOMContentLoaded', () => {
    new ProjectMap();
});