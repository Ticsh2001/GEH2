/**
 * Модуль работы с соединениями
 * connections.js
 */

const Connections = {
    /**
     * Настройка обработчиков порта
     */
    setupPortHandlers(port) {
        port.addEventListener('mousedown', (e) => {
            e.stopPropagation();

            if (port.classList.contains('output')) {
                const elemId = port.dataset.element;
                const portName = port.dataset.port;
                const signalType = getOutputPortType(elemId, portName);

                AppState.connectingFrom = {
                    element: elemId,
                    port: portName
                };
                AppState.connectingFromType = signalType;

                this.highlightCompatiblePorts(signalType);

                const svg = document.getElementById('connections-svg');
                const startPos = this._getPortCanvasCenter(port);

                AppState.tempLine = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                AppState.tempLine.setAttribute('class', 'temp-connection');
                AppState.tempLine.setAttribute('d', `M ${startPos.x} ${startPos.y} L ${startPos.x} ${startPos.y}`);
                svg.appendChild(AppState.tempLine);
            }
        });

        port.addEventListener('mouseup', (e) => {
            e.stopPropagation();
            e.preventDefault();

            if (AppState.connectingFrom && port.classList.contains('input')) {
                const toElement = port.dataset.element;
                const toPortName = port.dataset.port;
                const inputType = getInputPortType(toElement, toPortName);

                if (!areTypesCompatible(AppState.connectingFromType, inputType)) {
                    this.clearConnectionState();
                    return;
                }

                if (AppState.connectingFrom.element !== toElement) {
                    const targetElem = AppState.elements[toElement];
                    const allowMultipleInputs = targetElem?.type === 'output';

                    const exists = AppState.connections.some(c =>
                        c.toElement === toElement && c.toPort === toPortName
                    );

                    if (!exists || allowMultipleInputs) {
                        AppState.connections.push({
                            fromElement: AppState.connectingFrom.element,
                            fromPort: AppState.connectingFrom.port,
                            toElement,
                            toPort: toPortName,
                            signalType: AppState.connectingFromType
                        });

                        port.classList.add('connected');
                        this.drawConnections();
                        this.clearConnectionState();
                        return;
                    }
                }
            }

            this.clearConnectionState();
        });

        port.addEventListener('mouseenter', () => {
            if (AppState.connectingFrom && port.classList.contains('input')) {
                const toPortName = port.dataset.port;
                const inputType = getInputPortType(port.dataset.element, toPortName);

                if (!areTypesCompatible(AppState.connectingFromType, inputType)) {
                    if (AppState.tempLine) {
                        AppState.tempLine.classList.add('invalid');
                    }
                }
            }
        });

        port.addEventListener('mouseleave', () => {
            if (AppState.tempLine) {
                AppState.tempLine.classList.remove('invalid');
            }
        });
    },

    /**
     * Подсветка совместимых портов
     */
    highlightCompatiblePorts(signalType) {
        document.querySelectorAll('.port.input').forEach(port => {
            const inputType = getInputPortType(port.dataset.element, port.dataset.port);

            if (areTypesCompatible(signalType, inputType)) {
                port.classList.add('compatible-highlight');
            } else {
                port.classList.add('incompatible');
            }
        });
    },

    /**
     * Очистка состояния соединения
     */
    clearConnectionState() {
        if (AppState.tempLine) {
            AppState.tempLine.remove();
            AppState.tempLine = null;
        }
        AppState.connectingFrom = null;
        AppState.connectingFromType = null;

        document.querySelectorAll('.port').forEach(port => {
            port.classList.remove('compatible-highlight', 'incompatible');
        });
    },

    /**
     * Отрисовка временной линии соединения
     */
    drawTempConnection(e) {
    if (!AppState.tempLine || !AppState.connectingFrom) return;

    const fromElem = document.getElementById(AppState.connectingFrom.element);
    if (!fromElem) return;

    const fromPort = fromElem.querySelector(`[data-port="${AppState.connectingFrom.port}"]`);
    if (!fromPort) return;

    const startPos = this._getPortCanvasCenter(fromPort);
    const endPos = screenToCanvas(e.clientX, e.clientY);

    const horizontalDist = Math.abs(endPos.x - startPos.x);
    const controlDist = Math.max(horizontalDist * 0.4, 50);

    // Тянем всегда от выхода (вектор 1, 0)
    const cx1 = startPos.x + controlDist;
    const cy1 = startPos.y;

    // Вторая точка контроля для плавности за курсором
    const cx2 = endPos.x - controlDist;
    const cy2 = endPos.y;

    AppState.tempLine.setAttribute('d', `M ${startPos.x} ${startPos.y} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${endPos.x} ${endPos.y}`);
    AppState.tempLine.setAttribute('fill', 'none');
},

    /**
     * Отрисовка всех соединений
     */
    drawConnections() {
    const svg = document.getElementById('connections-svg');

    // 1. Очистка старых линий
    svg.querySelectorAll('path:not(.temp-connection)').forEach(p => p.remove());

    // 2. Сброс визуального состояния портов
    document.querySelectorAll('.port.connected').forEach(port => {
        port.classList.remove('connected');
    });

    // 3. Перебор всех соединений из AppState
    AppState.connections.forEach(conn => {
        const fromElem = document.getElementById(conn.fromElement);
        const toElem = document.getElementById(conn.toElement);

        if (!fromElem || !toElem) return;

        const fromPort = fromElem.querySelector(`[data-port="${conn.fromPort}"]`);
        const toPort = toElem.querySelector(`[data-port="${conn.toPort}"]`);

        if (!fromPort || !toPort) return;

        fromPort.classList.add('connected');
        toPort.classList.add('connected');

        const startPos = this._getPortCanvasCenter(fromPort);
        const endPos = this._getPortCanvasCenter(toPort);

        if (!startPos || !endPos) return;

        // Расстояние для изгиба кривой
        const horizontalDist = Math.abs(endPos.x - startPos.x);
        const verticalDist = Math.abs(endPos.y - startPos.y);
        const controlDist = Math.max(horizontalDist * 0.4, 50);

        // --- ЛОГИКА ГЕОМЕТРИИ (Вектора касательных) ---
        let d;
        let cx1 = startPos.x;
        let cy1 = startPos.y;
        let cx2 = endPos.x;
        let cy2 = endPos.y;

        // ВЫХОД (Source): Касательная (1, 0) -> Всегда вправо
        cx1 = startPos.x + controlDist;
        cy1 = startPos.y;

        // ВХОД (Target):
        if (conn.toPort === 'cond-0') {
            // Технический порт: Касательная (0, 1) в декартовой (вверх)
            // В экранных координатах Y инвертирован, поэтому отнимаем от Y
            cx2 = endPos.x;
            cy2 = endPos.y - controlDist; // Линия заходит сверху вертикально
        } else {
            // Обычный вход: Касательная (-1, 0) -> Слева направо
            cx2 = endPos.x - controlDist;
            cy2 = endPos.y;
        }

        d = `M ${startPos.x} ${startPos.y} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${endPos.x} ${endPos.y}`;

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', d);
        path.setAttribute('fill', 'none'); // Чтобы не было черных полигонов

        // --- ЛОГИКА ЦВЕТА (Классы) ---
        let cssClass = 'connection';
        const type = conn.signalType;

        // Приоритет новым типам сигналов
        if (type === SIGNAL_TYPE.TRUE) cssClass += ' true-conn';
        else if (type === SIGNAL_TYPE.FALSE) cssClass += ' false-conn';
        else if (type === SIGNAL_TYPE.LOGIC) cssClass += ' logic-conn';
        else if (type === SIGNAL_TYPE.NUMERIC) cssClass += ' numeric-conn';
        else if (type === SIGNAL_TYPE.ANY) cssClass += ' any-conn';

        path.setAttribute('class', cssClass);

        // Обработчики событий
        path.style.pointerEvents = 'stroke';
        path.style.cursor = 'pointer';
        path.addEventListener('click', () => this.handleConnectionClick(conn));

        svg.appendChild(path);
    });

    if (typeof Outputs !== 'undefined' && Outputs.updateOutputStatus) {
        Outputs.updateOutputStatus();
    }
    Viewport.updateMinimap();
},
    /**
     * Обработка клика по соединению (удаление)
     */
    handleConnectionClick(conn) {
        if (confirm('Удалить соединение?')) {
            AppState.connections = AppState.connections.filter(c =>
                !(c.fromElement === conn.fromElement &&
                  c.fromPort === conn.fromPort &&
                  c.toElement === conn.toElement &&
                  c.toPort === conn.toPort)
            );

            this.drawConnections();
        }
    },

    /**
     * Получение центра порта в координатах Canvas
     */
    _getPortCanvasCenter(portEl) {
        if (!portEl) return null;

        const rect = portEl.getBoundingClientRect();
        return screenToCanvas(
            rect.left + rect.width / 2,
            rect.top + rect.height / 2
        );
    }
};