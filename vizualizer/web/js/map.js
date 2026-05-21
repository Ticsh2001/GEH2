/**
 * Карта зависимостей проектов (с поддержкой конфигураций) — полная версия
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

        this.nodeMinWidth = 150;
        this.nodeMaxWidth = 400;
        this.nodeHeight = 65;
        this.levelSpacing = 320;
        this.nodeSpacing = 70;

        // Перетаскивание узлов
        this.draggingNode = null;
        this.dragOffsetX = 0;
        this.dragOffsetY = 0;

        // Конфигурация из URL
        const urlParams = new URLSearchParams(window.location.search);
        this.config = urlParams.get('config') || '';

        this._depCacheFor = null;
        this._depCache = null;

        this.initCanvas();
        this.setupEventListeners();
        this.loadData();
    }

    // Вспомогательный метод: добавляет config к URL
    apiUrl(path) {
        if (!this.config) return path;
        const sep = path.includes('?') ? '&' : '?';
        return path + sep + 'config=' + encodeURIComponent(this.config);
    }

    initCanvas() {
        this.resizeCanvas();
        window.addEventListener('resize', () => this.resizeCanvas());
    }

    resizeCanvas() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width;
        this.canvas.height = rect.height;
        if (typeof this.draw === 'function') {
            this.draw();
        }
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

        document.getElementById('ctx-open-project').addEventListener('click', () => this.openSelectedProject());
        document.getElementById('ctx-show-details').addEventListener('click', () => this.showNodeDetails());

        document.addEventListener('click', (e) => {
            const ctx = document.getElementById('context-menu');
            if (ctx && !ctx.contains(e.target)) {
                ctx.style.display = 'none';
            }
        });

        const btnDeps = document.getElementById('btn-mode-deps');
        const btnCons = document.getElementById('btn-mode-consumers');
        if (btnDeps) btnDeps.addEventListener('click', () => { this.viewMode = 'deps'; this.loadData(); });
        if (btnCons) btnCons.addEventListener('click', () => { this.viewMode = 'consumers'; this.loadData(); });

        const btnFilter = document.getElementById('btn-filter-inputs');
        if (btnFilter) {
            btnFilter.addEventListener('click', () => {
                this.onlyDirectInputs = !this.onlyDirectInputs;
                btnFilter.classList.toggle('active', this.onlyDirectInputs);
                this.applyVisibility();
                this.draw();
            });
        }
    }

    applyVisibility() {
    if (!this.onlyDirectInputs) {
        // Показываем все узлы и связи
        this.nodes.forEach(n => n.visible = true);
        this.connections.forEach(c => c.visible = true);
        return;
    }

    // Скрываем все, затем покажем только нужные
    this.nodes.forEach(n => n.visible = false);
    this.connections.forEach(c => c.visible = false);

    // Ищем узел текущего проекта (если открыта карта одного проекта)
    const projectNode = this.nodes.find(n => n.name === this._currentProjectCode);
    if (!projectNode) return;

    projectNode.visible = true;

    // Прямые входы: все соединения, которые идут ОТ какого-то узла К проекту
    this.connections.forEach(conn => {
        if (conn.to === projectNode) {
            conn.from.visible = true;
            conn.visible = true;
        }
    });
}

    // =========================================================================
    // ЗАГРУЗКА ДАННЫХ
    // =========================================================================
    async loadData() {
        this.onlyDirectInputs = false;
        const btnFilter = document.getElementById('btn-filter-inputs');
        if (btnFilter) btnFilter.classList.remove('active');        
        this.loading.style.display = 'block';
        try {
            const urlParams = new URLSearchParams(window.location.search);
            const projectFilename = urlParams.get('project');
            const projectSource = urlParams.get('source') || 'projects';

            if (projectFilename) {
                const loadUrl = this.apiUrl(
                    `/api/project/load/${encodeURIComponent(projectFilename)}?source=${encodeURIComponent(projectSource)}`
                );
                const projResp = await fetch(loadUrl);
                if (!projResp.ok) throw new Error(`Проект "${projectFilename}" не найден`);
                const projectData = await projResp.json();
                const currentCode = (projectData.project?.code || '').trim() || 'Unknown';
                this._currentProjectCode = currentCode;

                if (this.viewMode === 'deps') {
                    await this.loadSingleProjectUsingResolved(projectData, projectFilename, projectSource);
                    document.querySelector('.header h1').textContent =
                        `🗺️ Карта проекта (входы): ${currentCode}`;
                } else {
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
        const inputSignals = [];
        Object.values(projectData.elements || {}).forEach(el => {
            if (el?.type === 'input-signal' && el.props?.name) {
                inputSignals.push(el.props.name.trim());
            }
        });
        const uniqueInputs = [...new Set(inputSignals)];

        const resolveResp = await fetch(this.apiUrl('/api/resolve-signals'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ signals: uniqueInputs })
        });
        if (!resolveResp.ok) throw new Error('Не удалось разрешить зависимости сигналов');
        const resolved = await resolveResp.json();

        const listResp = await fetch(this.apiUrl('/api/project/list'));
        const listData = await listResp.json();
        const projectLookup = new Map();
        for (const p of (listData.projects || [])) {
            if (p.code) projectLookup.set(p.code, p);
        }

        this.buildSingleProjectGraph(projectData, projectFilename, projectSource, resolved, projectLookup);
    }

    async loadConsumersGraph(projectData, projectFilename, projectSource) {
        const currentCode = (projectData.project?.code || '').trim() || 'Unknown';
        const consumersUrl = this.apiUrl(`/api/project/consumers/${encodeURIComponent(currentCode)}`);
        const resp = await fetch(consumersUrl);
        if (!resp.ok) throw new Error('Не удалось получить список потребителей');
        const data = await resp.json();
        const consumers = data.consumers || [];

        this.nodes = [];
        this.connections = [];
        this.levels.clear();
        let nodeId = 0;

        const currentNode = {
            id: nodeId++,
            name: currentCode,
            type: 'project',
            level: 0,
            x: 0, y: 0,
            project: { code: currentCode, filename: projectFilename, source: projectSource },
            inputSignals: []
        };
        this.nodes.push(currentNode);
        this.addToLevel(0, currentNode);

        for (const c of consumers) {
            const node = {
                id: nodeId++,
                name: c.code || c.filename,
                type: 'project',
                level: 1,
                x: 0, y: 0,
                project: { code: c.code || '', filename: c.filename, source: c.source || 'projects' },
                inputSignals: [currentCode]
            };
            this.nodes.push(node);
            this.addToLevel(1, node);
            this.connections.push({ from: currentNode, to: node });
        }
        this.positionNodes();
    }

    async loadAllProjects() {
        console.log('[map] Loading all projects...');
        const projectsResponse = await fetch(this.apiUrl('/api/project/list'));
        const projectsData = await projectsResponse.json();
        const allProjects = projectsData.projects || [];

        const signalsResponse = await fetch(this.apiUrl('/api/signals?limit=10000'));
        const signalsData = await signalsResponse.json();
        const baseSignals = new Set((signalsData.items || []).map(s => s.Tagname));

        console.log('[map] Projects:', allProjects.length, 'Base signals:', baseSignals.size);
        await this.buildDependencyGraph(allProjects, baseSignals);
    }

    // =========================================================================
    // ПОСТРОЕНИЕ ГРАФОВ
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
        const signalLevels = {};

        for (const sig of baseSignalNames) {
            signalLevels[sig] = 0;
        }

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
        signalLevels[currentCode] = maxDep + 1;

        // Базовые сигналы
        for (const signal of baseSignalNames) {
            const node = {
                id: nodeId++, name: signal, type: 'base-signal', level: 0, x: 0, y: 0, project: null
            };
            this.nodes.push(node);
            signalToNode.set(signal, node);
            this.addToLevel(0, node);
        }

        // Синтетические проекты
        for (const name of computationOrder) {
            const data = syntheticSignals[name];
            if (!data) continue;
            const level = signalLevels[name] || 1;
            const projInfo = projectLookup.get(name) || {
                code: name, filename: `${name}_parameter.json`, source: 'projects'
            };
            const node = {
                id: nodeId++, name, type: 'project', level, x: 0, y: 0,
                project: projInfo, inputSignals: data.dependencies || []
            };
            this.nodes.push(node);
            signalToNode.set(name, node);
            this.addToLevel(level, node);
        }

        // Текущий проект
        const currentProjInfo = projectLookup.get(currentCode) || {
            code: currentCode, filename: projectFilename, source: projectSource
        };
        const currentNode = {
            id: nodeId++, name: currentCode, type: 'project', level: signalLevels[currentCode], x: 0, y: 0,
            project: currentProjInfo, inputSignals: uniqueCurrentInputs
        };
        this.nodes.push(currentNode);
        signalToNode.set(currentCode, currentNode);
        this.addToLevel(signalLevels[currentCode], currentNode);

        // Связи
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
        for (const dep of uniqueCurrentInputs) {
            const fromNode = signalToNode.get(dep);
            if (fromNode && fromNode !== currentNode) {
                this.connections.push({ from: fromNode, to: currentNode });
            }
        }
        this.positionNodes();
    }

    async buildDependencyGraph(projects, baseSignals) {
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
                const node = { id: nodeId++, name: signal, type: 'base-signal', level: 0, x: 0, y: 0, project: null };
                this.nodes.push(node);
                signalToNode.set(signal, node);
                this.addToLevel(0, node);
            }
        }

        const projectNodes = new Map();
        for (const project of projects) {
            const node = {
                id: nodeId++, name: project.code || 'Unnamed', type: 'project', level: -1, x: 0, y: 0,
                project: project, inputSignals: await this.getProjectInputSignals(project)
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
    }

    async getProjectInputSignals(project) {
        try {
            if (project.elements) {
                const signals = [];
                Object.values(project.elements).forEach(el => {
                    if (el && el.type === 'input-signal' && el.props?.name) {
                        signals.push(el.props.name.trim());
                    }
                });
                return [...new Set(signals)];
            }
            const loadUrl = this.apiUrl(
                `/api/project/load/${project.filename}?source=${project.source || 'projects'}`
            );
            const response = await fetch(loadUrl);
            if (!response.ok) return [];
            const data = await response.json();
            const signals = [];
            Object.values(data.elements || {}).forEach(el => {
                if (el && el.type === 'input-signal' && el.props?.name) {
                    signals.push(el.props.name.trim());
                }
            });
            return [...new Set(signals)];
        } catch (error) {
            console.warn('[map] Error loading project signals:', error);
            return [];
        }
    }

    calculateProjectLevels(projectNodes, signalToNode, baseSignals) {
        const maxIterations = 50;
        for (const [name, node] of projectNodes) {
            node.level = 1;
        }
        let changed = true;
        let iteration = 0;
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
        // Вычисляем ширину для каждого узла
        this.nodes.forEach(node => {
            node.width = this.calcNodeWidth(node.name);
            node.height = this.nodeHeight;   // фиксированная высота
        });

        // Раскладываем по уровням с учётом индивидуальной ширины
        const maxLevel = Math.max(...this.levels.keys(), 0);
        for (let level = 0; level <= maxLevel; level++) {
            const nodes = this.levels.get(level) || [];
            // Вычисляем суммарную высоту для этого уровня
            const totalHeight = nodes.length * (this.nodeHeight + this.nodeSpacing) - this.nodeSpacing;
            let y = -totalHeight / 2;
            nodes.forEach(node => {
                node.y = y;
                y += this.nodeHeight + this.nodeSpacing;
            });

            // Размещаем по X: отступ от предыдущего уровня с учётом максимальной ширины узлов на уровне
            if (level === 0) {
                nodes.forEach(node => node.x = 0);
            } else {
                const prevLevel = this.levels.get(level - 1) || [];
                const prevMaxRight = prevLevel.length > 0
                    ? Math.max(...prevLevel.map(n => n.x + n.width))
                    : 0;
                nodes.forEach(node => node.x = prevMaxRight + this.levelSpacing);
            }
        }
    }

    // Вычисление ширины узла по тексту
    calcNodeWidth(text) {
        if (!text) return this.nodeMinWidth;
        this.ctx.save();
        this.ctx.font = 'bold 14px "Segoe UI", sans-serif';
        const textWidth = this.ctx.measureText(text).width;
        this.ctx.restore();
        let w = textWidth + 50; // отступы
        w = Math.max(this.nodeMinWidth, w);
        w = Math.min(this.nodeMaxWidth, w);
        return w;
    }

    // =========================================================================
    // ОТРИСОВКА
    // =========================================================================
    draw() {
        const ctx = this.ctx;
        const canvas = this.canvas;
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        ctx.save();
        ctx.translate(this.offsetX, this.offsetY);
        ctx.scale(this.zoom, this.zoom);

        this.drawGrid();
        //this.connections.forEach(conn => this.drawConnection(conn));
        //this.nodes.forEach(node => this.drawNode(node));
        this.connections.filter(c => c.visible !== false).forEach(conn => this.drawConnection(conn));
        this.nodes.filter(n => n.visible !== false).forEach(node => this.drawNode(node));

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
        const w = node.width;
        const h = node.height;

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
        ctx.lineTo(x + w, y + h / 2);
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
        ctx.fillText(node.type === 'project' ? 'Проект' : 'Элемент', x + w / 2, y + headerHeight / 2);

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
        ctx.fillText(icon, x + 25, y + h / 2 - 12);

        ctx.font = 'bold 14px "Segoe UI", sans-serif';
        ctx.fillStyle = textColor;

        //let displayName = node.name;
        //const maxChars = node.type === 'base-signal' ? 16 : 18;
        //if (displayName.length > maxChars) {
        //    displayName = displayName.substring(0, maxChars - 1) + '…';
        //}
        //ctx.fillText(displayName, x + w / 2, y + h / 2 + 5);
        ctx.fillText(node.name, x + w/2, y + h/2 + 5);


        if (node.type === 'project') {
            ctx.font = '10px "Segoe UI", sans-serif';
            ctx.fillStyle = '#aaa';
            ctx.fillText(`Уровень ${node.level}`, x + w / 2, y + h - 8);
        }
    }

    isNodeHighlighted(node) {
        if (!this.selectedNode || this.selectedNode === node) return false;
        const deps = this.getDependencySet(this.selectedNode);
        return deps.has(node);
    }

    getDependencySet(startNode) {
        if (this._depCacheFor === startNode && this._depCache) return this._depCache;
        const visited = new Set();
        const stack = [startNode];
        while (stack.length) {
            const cur = stack.pop();
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

        const fromX = from.x + from.width;
        const fromY = from.y + from.height / 2;
        const toX = to.x;
        const toY = to.y + to.height / 2;

        let strokeColor = '#4a90d9';
        let lineWidth = 2;

        const selected = this.selectedNode;
        if (selected) {
            const deps = this.getDependencySet(selected);
            const isDepEdge = (conn.to === selected && deps.has(conn.from)) ||
                            (deps.has(conn.to) && deps.has(conn.from));
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

    // =========================================================================
    // УПРАВЛЕНИЕ МЫШЬЮ
    // =========================================================================
    handleMouseDown(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const canvasX = (x - this.offsetX) / this.zoom;
        const canvasY = (y - this.offsetY) / this.zoom;

        const clickedNode = this.getNodeAt(canvasX, canvasY);

        if (clickedNode) {
            if (e.button === 0) { // левая кнопка
                this.draggingNode = clickedNode;
                this.dragOffsetX = canvasX - clickedNode.x;
                this.dragOffsetY = canvasY - clickedNode.y;
                this.selectedNode = clickedNode; // можно и выделить
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
                this.draw();
            }
        }
    }

    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        if (this.draggingNode) {
            const canvasX = (x - this.offsetX) / this.zoom;
            const canvasY = (y - this.offsetY) / this.zoom;
            this.draggingNode.x = canvasX - this.dragOffsetX;
            this.draggingNode.y = canvasY - this.dragOffsetY;
            this.draw();
            return;
        }

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
                this.canvas.style.cursor = hoveredNode ? 'move' : 'grab';
                this.draw();
            }
        }
    }

    handleMouseUp(e) {
        this.draggingNode = null;
        this.isDragging = false;
        this.canvas.style.cursor = this.hoveredNode ? 'move' : 'grab';
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
            const w = node.width || this.nodeMinWidth;
            const h = node.height || this.nodeHeight;
            if (x >= node.x && x <= node.x + w &&
                y >= node.y && y <= node.y + h) {
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
            const w = node.width || this.nodeMinWidth;
            const h = node.height || this.nodeHeight;
            minX = Math.min(minX, node.x);
            minY = Math.min(minY, node.y);
            maxX = Math.max(maxX, node.x + w);
            maxY = Math.max(maxY, node.y + h);
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
            const url = `/?load=${encodeURIComponent(project.filename)}&source=${encodeURIComponent(project.source || 'projects')}&config=${encodeURIComponent(this.config || '')}`;
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

    updateSelectionInfo(node) {
        const info = document.getElementById('level-info');
        if (!info) return;
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

    updateLevelInfo() {
        const detailsEl = document.getElementById('level-details');
        if (!detailsEl) return; 

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