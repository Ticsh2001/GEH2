/**
 * Модуль работы с элементами схемы
 * map.js
 */



class ProjectMap {
    constructor() {
        this.canvas = document.getElementById('map-canvas');
        this.ctx = this.canvas.getContext('2d');
        this.loading = document.getElementById('loading');
        this.viewMode = 'deps';
        
        this.nodes = [];
        this.connections = [];
        this.levels = new Map();
        this.selectedNode = null;
        this.hoveredNode = null;
        
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.isDragging = false;
        this.lastMouseX = 0;
        this.lastMouseY = 0;
        
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
        this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        this.canvas.addEventListener('wheel', (e) => this.handleWheel(e));
        this.canvas.addEventListener('contextmenu', (e) => this.handleContextMenu(e));

        document.getElementById('btn-refresh').addEventListener('click', () => this.loadData());
        document.getElementById('btn-fit').addEventListener('click', () => this.fitAll());
        document.getElementById('btn-reset-zoom').addEventListener('click', () => this.resetZoom());

        const btnShowAll = document.getElementById('btn-show-all');
        if (btnShowAll) {
            btnShowAll.addEventListener('click', () => {
                window.location.href = '/map.html';
            });
        }

        document.getElementById('ctx-open-project').addEventListener('click', () => this.openSelectedProject());
        document.getElementById('ctx-show-details').addEventListener('click', () => this.showNodeDetails());
        
        document.addEventListener('click', (e) => {
            if (!document.getElementById('context-menu').contains(e.target)) {
                document.getElementById('context-menu').style.display = 'none';
            }
        });
        document.getElementById('btn-mode-deps')?.addEventListener('click', () => {
            this.viewMode = 'deps';
            this.loadData(); // перезагружаем в новом режиме
        });
        document.getElementById('btn-mode-consumers')?.addEventListener('click', () => {
            this.viewMode = 'consumers';
            this.loadData(); // перезагружаем в новом режиме
        });
    }

    // =========================================================================
    // ЗАГРУЗКА ДАННЫХ — ГЛАВНЫЙ РОУТЕР
    // =========================================================================
    async loadData() {
        this.loading.style.display = 'block';
        try {
            const urlParams = new URLSearchParams(window.location.search);
            const projectFilename = urlParams.get('project');
            const projectSource = urlParams.get('source') || 'projects';
            
            if (projectFilename) {
                // Загружаем сам проект (нам нужен code/inputs)
                const projResp = await fetch(
                    `/api/project/load/${encodeURIComponent(projectFilename)}?source=${encodeURIComponent(projectSource)}`
                );
                if (!projResp.ok) throw new Error(`Проект "${projectFilename}" не найден`);
                const projectData = await projResp.json();
                const currentCode = (projectData.project?.code || '').trim() || 'Unknown';
                this._currentProjectCode = currentCode;

                if (this.viewMode === 'deps') {
                    // как раньше — разворачиваем входы
                    await this.loadSingleProjectUsingResolved(projectData, projectFilename, projectSource);
                    document.querySelector('.header h1').textContent =
                        `🗺️ Карта проекта (входы): ${currentCode}`;
                } else {
                    // новый режим: кто использует текущий
                    await this.loadConsumersGraph(projectData, projectFilename, projectSource);
                    document.querySelector('.header h1').textContent =
                        `🗺️ Карта проекта (кто использует): ${currentCode}`;
                }
            } else {
                await this.loadAllProjects();
                document.querySelector('.header h1').textContent = '🗺️ Карта зависимостей проектов';
            }

            this.loading.style.display = 'none';
            this.updateLevelInfo();
            this.fitAll();
        } catch (error) {
            console.error('[map] Error loading data:', error);
            this.loading.textContent = 'Ошибка загрузки данных: ' + error.message;
        }
    }

        async loadSingleProjectUsingResolved(projectData, projectFilename, projectSource) {
        // 2) Извлекаем входные сигналы текущего проекта
        const inputSignals = [];
        Object.values(projectData.elements || {}).forEach(el => {
            if (el?.type === 'input-signal' && el.props?.name) {
                inputSignals.push(el.props.name.trim());
            }
        });
        const uniqueInputs = [...new Set(inputSignals)];

        // 3) Рекурсивно разворачиваем зависимости через бэкенд
        const resolveResp = await fetch('/api/resolve-signals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ signals: uniqueInputs })
        });
        if (!resolveResp.ok) throw new Error('Не удалось разрешить зависимости сигналов');
        const resolved = await resolveResp.json();

        // 4) Загружаем список проектов для метаданных (filename, source)
        const listResp = await fetch('/api/project/list');
        const listData = await listResp.json();
        const projectLookup = new Map();
        for (const p of (listData.projects || [])) {
            if (p.code) projectLookup.set(p.code, p);
        }

        // 5) Строим граф (как было)
        this.buildSingleProjectGraph(
            projectData, projectFilename, projectSource,
            resolved, projectLookup
        );
    }

        async loadConsumersGraph(projectData, projectFilename, projectSource) {
        const currentCode = (projectData.project?.code || '').trim() || 'Unknown';
        // 1) Запрашиваем у бэкенда прямых потребителей
        const resp = await fetch(`/api/project/consumers/${encodeURIComponent(currentCode)}`);
        if (!resp.ok) throw new Error('Не удалось получить список потребителей');
        const data = await resp.json();
        const consumers = data.consumers || [];

        // 2) Строим упрощённый граф «звезды»: центр — текущий, лучи — потребители
        this.nodes = [];
        this.connections = [];
        this.levels.clear();

        let nodeId = 0;

        // Текущий проект — уровень 0
        const currentNode = {
            id: nodeId++,
            name: currentCode,
            type: 'project',
            level: 0,       // уровень 0 как корень в этом режиме
            x: 0, y: 0,
            project: {
                code: currentCode,
                filename: projectFilename,
                source: projectSource
            },
            inputSignals: [] // не важно тут
        };
        this.nodes.push(currentNode);
        this.addToLevel(0, currentNode);

        // Потребители — уровень 1
        for (const c of consumers) {
            const node = {
                id: nodeId++,
                name: c.code || c.filename,
                type: 'project',
                level: 1,
                x: 0, y: 0,
                project: {
                    code: c.code || '',
                    filename: c.filename,
                    source: c.source || 'projects'
                },
                inputSignals: [currentCode] // минимально, для деталей
            };
            this.nodes.push(node);
            this.addToLevel(1, node);

            // Связь от текущего к потребителю
            this.connections.push({ from: currentNode, to: node });
        }

        // Раскладываем красиво
        this.positionNodes();
    }

    // =========================================================================
    // РЕЖИМ: ВСЕ ПРОЕКТЫ (старая логика)
    // =========================================================================
    async loadAllProjects() {
        console.log('[map] Loading all projects...');
        
        const projectsResponse = await fetch('/api/project/list');
        const projectsData = await projectsResponse.json();
        const allProjects = projectsData.projects || [];
        
        const signalsResponse = await fetch('/api/signals?limit=10000');
        const signalsData = await signalsResponse.json();
        const baseSignals = new Set((signalsData.items || []).map(s => s.Tagname));
        
        console.log('[map] Projects:', allProjects.length, 'Base signals:', baseSignals.size);
        
        await this.buildDependencyGraph(allProjects, baseSignals);
    }

    // =========================================================================
    // РЕЖИМ: ОДИН ПРОЕКТ — рекурсивная иерархия
    // =========================================================================
    async loadSingleProject(projectFilename, projectSource) {
        console.log(`[map] Loading single project: ${projectFilename} (${projectSource})`);
        
        // 1) Загружаем текущий проект
        const projResp = await fetch(
            `/api/project/load/${encodeURIComponent(projectFilename)}?source=${encodeURIComponent(projectSource)}`
        );
        if (!projResp.ok) throw new Error(`Проект "${projectFilename}" не найден`);
        const projectData = await projResp.json();
        
        const currentCode = (projectData.project?.code || '').trim() || 'Unknown';
        this._currentProjectCode = currentCode;
        
        // 2) Извлекаем входные сигналы текущего проекта
        const inputSignals = [];
        Object.values(projectData.elements || {}).forEach(el => {
            if (el?.type === 'input-signal' && el.props?.name) {
                inputSignals.push(el.props.name.trim());
            }
        });
        const uniqueInputs = [...new Set(inputSignals)];
        
        console.log(`[map] Project "${currentCode}" inputs:`, uniqueInputs);
        
        // 3) Рекурсивно разворачиваем зависимости через бэкенд
        const resolveResp = await fetch('/api/resolve-signals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ signals: uniqueInputs })
        });
        if (!resolveResp.ok) throw new Error('Не удалось разрешить зависимости сигналов');
        const resolved = await resolveResp.json();
        
        console.log('[map] Resolved:', {
            base: resolved.base_signals?.length,
            synthetic: Object.keys(resolved.synthetic_signals || {}).length,
            order: resolved.computation_order
        });
        
        // 4) Загружаем список проектов для метаданных (filename, source)
        const listResp = await fetch('/api/project/list');
        const listData = await listResp.json();
        const projectLookup = new Map();
        for (const p of (listData.projects || [])) {
            if (p.code) projectLookup.set(p.code, p);
        }
        
        // 5) Строим граф
        this.buildSingleProjectGraph(
            projectData, projectFilename, projectSource,
            resolved, projectLookup
        );
    }

    // =========================================================================
    // ПОСТРОЕНИЕ ГРАФА ДЛЯ ОДНОГО ПРОЕКТА
    // =========================================================================
    buildSingleProjectGraph(projectData, projectFilename, projectSource, resolved, projectLookup) {
        this.nodes = [];
        this.connections = [];
        this.levels.clear();
        
        let nodeId = 0;
        const signalToNode = new Map();
        
        const baseSignalNames = new Set(resolved.base_signals || []);
        const syntheticSignals = resolved.synthetic_signals || {};
        const computationOrder = resolved.computation_order || [];
        
        // ---- Вычисляем уровни ----
        const signalLevels = {};
        
        // Уровень 0: базовые сигналы
        for (const sig of baseSignalNames) {
            signalLevels[sig] = 0;
        }
        
        // Уровни синтетических сигналов (через топологический порядок)
        for (const name of computationOrder) {
            const data = syntheticSignals[name];
            if (!data) continue;
            
            let maxDep = 0;
            for (const dep of (data.dependencies || [])) {
                if (dep in signalLevels) {
                    maxDep = Math.max(maxDep, signalLevels[dep]);
                }
            }
            signalLevels[name] = maxDep + 1;
        }
        
        // Уровень текущего проекта
        const currentCode = (projectData.project?.code || '').trim() || 'Current';
        const currentInputs = [];
        Object.values(projectData.elements || {}).forEach(el => {
            if (el?.type === 'input-signal' && el.props?.name) {
                currentInputs.push(el.props.name.trim());
            }
        });
        const uniqueCurrentInputs = [...new Set(currentInputs)];
        
        let maxDep = 0;
        for (const dep of uniqueCurrentInputs) {
            if (dep in signalLevels) {
                maxDep = Math.max(maxDep, signalLevels[dep]);
            }
        }
        const currentLevel = maxDep + 1;
        signalLevels[currentCode] = currentLevel;
        
        // ---- Создаём узлы ----
        
        // Базовые сигналы (уровень 0)
        for (const signal of baseSignalNames) {
            const node = {
                id: nodeId++,
                name: signal,
                type: 'base-signal',
                level: 0,
                x: 0, y: 0,
                project: null
            };
            this.nodes.push(node);
            signalToNode.set(signal, node);
            this.addToLevel(0, node);
        }
        
        // Промежуточные проекты (синтетические сигналы)
        for (const name of computationOrder) {
            const data = syntheticSignals[name];
            if (!data) continue;
            
            const level = signalLevels[name] || 1;
            const projInfo = projectLookup.get(name);
            
            const node = {
                id: nodeId++,
                name: name,
                type: 'project',
                level: level,
                x: 0, y: 0,
                project: projInfo || {
                    code: name,
                    filename: `${name}_parameter.json`,
                    source: 'projects'
                },
                inputSignals: data.dependencies || []
            };
            this.nodes.push(node);
            signalToNode.set(name, node);
            this.addToLevel(level, node);
        }
        
        // Текущий проект (верхний уровень)
        const currentProjInfo = projectLookup.get(currentCode) || {
            code: currentCode,
            filename: projectFilename,
            source: projectSource
        };
        // Гарантируем наличие filename
        if (!currentProjInfo.filename) {
            currentProjInfo.filename = projectFilename;
        }
        
        const currentNode = {
            id: nodeId++,
            name: currentCode,
            type: 'project',
            level: currentLevel,
            x: 0, y: 0,
            project: currentProjInfo,
            inputSignals: uniqueCurrentInputs
        };
        this.nodes.push(currentNode);
        signalToNode.set(currentCode, currentNode);
        this.addToLevel(currentLevel, currentNode);
        
        // ---- Создаём связи ----
        
        // Связи для промежуточных проектов
        for (const name of computationOrder) {
            const data = syntheticSignals[name];
            if (!data) continue;
            
            const toNode = signalToNode.get(name);
            if (!toNode) continue;
            
            for (const dep of (data.dependencies || [])) {
                const fromNode = signalToNode.get(dep);
                if (fromNode && fromNode !== toNode) {
                    this.connections.push({ from: fromNode, to: toNode });
                }
            }
        }
        
        // Связи для текущего проекта
        for (const dep of uniqueCurrentInputs) {
            const fromNode = signalToNode.get(dep);
            if (fromNode && fromNode !== currentNode) {
                this.connections.push({ from: fromNode, to: currentNode });
            }
        }
        
        // ---- Позиционируем ----
        this.positionNodes();
        
        console.log('[map] Single project graph built:', {
            nodes: this.nodes.length,
            connections: this.connections.length,
            levels: this.levels.size
        });
    }

    // =========================================================================
    // СТАРАЯ ЛОГИКА — граф ВСЕХ проектов (без изменений)
    // =========================================================================
    async buildDependencyGraph(projects, baseSignals) {
        console.log('[map] Building dependency graph...');
        
        this.nodes = [];
        this.connections = [];
        this.levels.clear();

        let nodeId = 0;
        const signalToNode = new Map();
        
        const usedSignals = new Set();
        for (const project of projects) {
            const inputSignals = await this.getProjectInputSignals(project);
            inputSignals.forEach(sig => usedSignals.add(sig));
        }

        for (const signal of usedSignals) {
            if (baseSignals.has(signal)) {
                const node = {
                    id: nodeId++,
                    name: signal,
                    type: 'base-signal',
                    level: 0,
                    x: 0, y: 0,
                    project: null
                };
                this.nodes.push(node);
                signalToNode.set(signal, node);
                this.addToLevel(0, node);
            }
        }

        const projectNodes = new Map();
        for (const project of projects) {
            const node = {
                id: nodeId++,
                name: project.code || 'Unnamed',
                type: 'project',
                level: -1,
                x: 0, y: 0,
                project: project,
                inputSignals: await this.getProjectInputSignals(project)
            };
            this.nodes.push(node);
            projectNodes.set(node.name, node);
            signalToNode.set(node.name, node);
        }

        this.calculateProjectLevels(projectNodes, signalToNode, baseSignals);

        for (const [projectName, node] of projectNodes) {
            for (const inputSignal of node.inputSignals) {
                const sourceNode = signalToNode.get(inputSignal);
                if (sourceNode && sourceNode !== node) {
                    this.connections.push({ from: sourceNode, to: node });
                }
            }
        }

        this.positionNodes();
        
        console.log('[map] Graph built:', {
            nodes: this.nodes.length,
            connections: this.connections.length,
            levels: this.levels.size
        });
    }

    async getProjectInputSignals(project) {
        try {
            if (project.elements) {
                const inputSignals = [];
                Object.values(project.elements).forEach(el => {
                    if (el && el.type === 'input-signal' && el.props?.name) {
                        inputSignals.push(el.props.name.trim());
                    }
                });
                return [...new Set(inputSignals)];
            }
            
            const response = await fetch(
                `/api/project/load/${project.filename}?source=${project.source || 'projects'}`
            );
            if (!response.ok) return [];
            
            const data = await response.json();
            const elements = data.elements || {};
            
            const inputSignals = [];
            Object.values(elements).forEach(el => {
                if (el && el.type === 'input-signal' && el.props?.name) {
                    inputSignals.push(el.props.name.trim());
                }
            });
            
            return [...new Set(inputSignals)];
        } catch (error) {
            console.warn('[map] Error loading project signals:', error);
            return [];
        }
    }

    calculateProjectLevels(projectNodes, signalToNode, baseSignals) {
        const maxIterations = 50;
        let changed = true;
        let iteration = 0;

        for (const [name, node] of projectNodes) {
            node.level = 1;
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

        for (const [name, node] of projectNodes) {
            this.addToLevel(node.level, node);
        }
    }

    addToLevel(level, node) {
        if (!this.levels.has(level)) {
            this.levels.set(level, []);
        }
        this.levels.get(level).push(node);
    }

    positionNodes() {
        const maxLevel = Math.max(...this.levels.keys(), 0);
        
        for (let level = 0; level <= maxLevel; level++) {
            const nodes = this.levels.get(level) || [];
            const startY = -(nodes.length * (this.nodeHeight + this.nodeSpacing)) / 2;
            
            nodes.forEach((node, index) => {
                node.x = level * this.levelSpacing;
                node.y = startY + index * (this.nodeHeight + this.nodeSpacing);
            });
        }
    }

    // =========================================================================
    // ВСЕ ОСТАЛЬНЫЕ МЕТОДЫ ОТРИСОВКИ — БЕЗ ИЗМЕНЕНИЙ
    // (draw, drawGrid, drawNode, drawConnection, и т.д.)
    // =========================================================================

    draw() {
        const ctx = this.ctx;
        const canvas = this.canvas;
        
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        ctx.save();
        ctx.translate(this.offsetX, this.offsetY);
        ctx.scale(this.zoom, this.zoom);
        
        this.drawGrid();
        this.connections.forEach(conn => this.drawConnection(conn));
        this.nodes.forEach(node => this.drawNode(node));
        
        ctx.restore();
        
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
        
        for (let x = startX; x < canvasWidth - this.offsetX / this.zoom; x += gridSize) {
            ctx.beginPath();
            ctx.moveTo(x, startY);
            ctx.lineTo(x, canvasHeight - this.offsetY / this.zoom);
            ctx.stroke();
        }
        
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
        
        let fillColor = '#0f3460';
        let borderColor = '#4a90d9';
        let headerColor = '#4a90d9';
        
        if (node.type === 'base-signal') {
            headerColor = '#4a90d9';
        } else {
            switch (node.level) {
                case 1: headerColor = '#10b981'; break;
                case 2: headerColor = '#f59e0b'; break;
                default: headerColor = '#ef4444'; break;
            }
        }
        
        const isHighlighted = this.isNodeHighlighted(node);
        if (node === this.selectedNode || isHighlighted) {
            borderColor = '#e94560';
            ctx.shadowColor = 'rgba(233, 69, 96, 0.5)';
            ctx.shadowBlur = 15;
        } else if (node === this.hoveredNode) {
            borderColor = '#e94560';
        }
        
        if (node.type === 'base-signal') {
            this.drawTrapezoid(ctx, x, y, w, h, fillColor, borderColor, headerColor, node);
        } else {
            this.drawRoundedRect(ctx, x, y, w, h, fillColor, borderColor, headerColor, node);
        }
        
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
    }

    drawTrapezoid(ctx, x, y, w, h, fillColor, borderColor, headerColor, node) {
        const trapezoidWidth = w * 0.8;
        
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + trapezoidWidth, y);
        ctx.lineTo(x + w, y + h/2);
        ctx.lineTo(x + trapezoidWidth, y + h);
        ctx.lineTo(x, y + h);
        ctx.closePath();
        
        ctx.fillStyle = fillColor;
        ctx.fill();
        ctx.strokeStyle = borderColor;
        ctx.lineWidth = 2;
        ctx.stroke();
        
        this.drawNodeContent(ctx, x, y, w, h, node, '#eee');
    }

    drawRoundedRect(ctx, x, y, w, h, fillColor, borderColor, headerColor, node) {
        const radius = 8;
        
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
        
        ctx.fillStyle = fillColor;
        ctx.fill();
        ctx.strokeStyle = borderColor;
        ctx.lineWidth = 2;
        ctx.stroke();
        
        const headerHeight = 26;
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
        
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 12px "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        
        const headerText = node.type === 'project' ? 'Проект' : 'Элемент';
        ctx.fillText(headerText, x + w/2, y + headerHeight/2);
        
        this.drawNodeContent(ctx, x, y + headerHeight, w, h - headerHeight, node, '#eee');
    }

    drawNodeContent(ctx, x, y, w, h, node, textColor) {
        ctx.fillStyle = textColor;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        
        let icon = '';
        if (node.type === 'base-signal') {
            icon = '🔢';
        } else {
            switch (node.level) {
                case 1: icon = '📊'; break;
                case 2: icon = '📋'; break; 
                default: icon = '🧩'; break;
            }
        }
        
        ctx.font = '18px "Segoe UI", sans-serif';
        ctx.fillText(icon, x + 25, y + h/2 - 12);
        
        ctx.font = 'bold 14px "Segoe UI", sans-serif';
        ctx.fillStyle = textColor;
        
        let displayName = node.name;
        const maxChars = node.type === 'base-signal' ? 16 : 18;
        if (displayName.length > maxChars) {
            displayName = displayName.substring(0, maxChars - 1) + '…';
        }
        
        ctx.fillText(displayName, x + w/2, y + h/2 + 5);
        
        if (node.type === 'project') {
            ctx.font = '10px "Segoe UI", sans-serif';
            ctx.fillStyle = '#aaa';
            ctx.fillText(`Уровень ${node.level}`, x + w/2, y + h - 8);
        }
    }

    isNodeHighlighted(node) {
        if (!this.selectedNode || this.selectedNode === node) return false;

        // Подсвечиваем только зависимости выбранного узла:
        // идём "назад" по графу: selectedNode <- dep <- dep <- ...
        const deps = this.getDependencySet(this.selectedNode);
        return deps.has(node);
    }

    getDependencySet(startNode) {
    // Кэш, чтобы не пересчитывать на каждый узел в draw()
    if (this._depCacheFor === startNode && this._depCache) return this._depCache;

    const visited = new Set();
    const stack = [startNode];

    while (stack.length) {
        const cur = stack.pop();

        // Ищем входящие связи: from -> cur, значит from = dependency
        for (const conn of this.connections) {
            if (conn.to === cur) {
                const dep = conn.from;
                if (!visited.has(dep)) {
                    visited.add(dep);
                    stack.push(dep);
                }
            }
        }
    }

    this._depCacheFor = startNode;
    this._depCache = visited;
    return visited;
}



    drawConnection(conn) {
        const ctx = this.ctx;
        const from = conn.from;
        const to = conn.to;
        
        let fromX = from.x + this.nodeWidth;
        let fromY = from.y + this.nodeHeight / 2;
        let toX = to.x;
        let toY = to.y + this.nodeHeight / 2;
        
        let strokeColor = '#4a90d9';
        let lineWidth = 2;
        
        //if (this.selectedNode === from || this.selectedNode === to || 
        //    this.isNodeHighlighted(from) || this.isNodeHighlighted(to)) {
        //    strokeColor = '#e94560';
        //    lineWidth = 3;
        //}
        const selected = this.selectedNode;

        if (selected) {
            const deps = this.getDependencySet(selected);
            const isDepEdge = (conn.to === selected && deps.has(conn.from)) ||
                            (deps.has(conn.to) && deps.has(conn.from)); 
            // ↑ второй вариант подсветит все рёбра внутри “поддерева” зависимостей

            if (isDepEdge) {
                strokeColor = '#e94560';
                lineWidth = 3;
            }
        }
        
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = lineWidth;
        ctx.beginPath();
        
        const controlOffset = Math.min(80, Math.abs(toX - fromX) / 3);
        ctx.moveTo(fromX, fromY);
        ctx.bezierCurveTo(
            fromX + controlOffset, fromY,
            toX - controlOffset, toY,
            toX, toY
        );
        ctx.stroke();
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
            info.innerHTML = '<strong>Карта зависимостей</strong><br>Кликните на узел для просмотра связей';
            return;
        }
        
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

    handleMouseDown(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        const canvasX = (x - this.offsetX) / this.zoom;
        const canvasY = (y - this.offsetY) / this.zoom;
        
        const clickedNode = this.getNodeAt(canvasX, canvasY);
        
        if (clickedNode) {
            if (e.button === 0) {
                this.selectedNode = this.selectedNode === clickedNode ? null : clickedNode;

                // сброс кэша подсветки зависимостей
                this._depCacheFor = null;
                this._depCache = null;

                this.draw();
                this.updateSelectionInfo(this.selectedNode);
            }
        } else {
            if (e.button === 0) {
                this.isDragging = true;
                this.lastMouseX = x;
                this.lastMouseY = y;
                this.canvas.style.cursor = 'grabbing';
                //this.selectedNode = null;
                //this.updateSelectionInfo(null);
                this.draw();
            }
        }
    }
    
    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        if (this.isDragging) {
            const dx = x - this.lastMouseX;
            const dy = y - this.lastMouseY;
            
            this.offsetX += dx;
            this.offsetY += dy;
            
            this.lastMouseX = x;
            this.lastMouseY = y;
            
            this.draw();
        } else {
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
        
        const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
        const newZoom = Math.max(0.1, Math.min(5, this.zoom * zoomFactor));
        
        if (newZoom !== this.zoom) {
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
    
    fitAll() {
        if (this.nodes.length === 0) return;
        
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
        
        const scaleX = this.canvas.width / contentWidth;
        const scaleY = this.canvas.height / contentHeight;
        const newZoom = Math.min(scaleX, scaleY, 1);
        
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

document.addEventListener('DOMContentLoaded', () => {
    new ProjectMap();
});