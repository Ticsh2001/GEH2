/**
 * Модуль работы с соединениями
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

        // Расстояния
        const horizontalDist = Math.abs(endPos.x - startPos.x);
        const verticalDist = Math.abs(endPos.y - startPos.y);
        const controlDist = Math.max(horizontalDist * 0.3, 40);

        // Определяем тип выходного порта
        const isFromPortOutput = fromPort.classList.contains('output');
        const isFromPortCondition = AppState.connectingFrom.port === 'cond-0';

        let d;
        if (isFromPortCondition) {
            // Технический порт - вертикальная касательная
            const c1y = startPos.y + verticalDist * 0.3;
            const c2y = startPos.y + verticalDist * 0.6;
            d = `M ${startPos.x} ${startPos.y} C ${startPos.x} ${c1y}, ${endPos.x} ${c2y}, ${endPos.x} ${endPos.y}`;
        } else {
            // Обычный выходной порт - горизонтальная касательная вправо
            const cx1 = startPos.x + controlDist;
            const cx2 = startPos.x + (endPos.x - startPos.x) * 0.5;
            d = `M ${startPos.x} ${startPos.y} C ${cx1} ${startPos.y}, ${cx2} ${endPos.y}, ${endPos.x} ${endPos.y}`;
        }

        AppState.tempLine.setAttribute('d', d);
    },

    /**
     * Отрисовка всех соединений
     */
    drawConnections() {
        const svg = document.getElementById('connections-svg');

        svg.querySelectorAll('path:not(.temp-connection)').forEach(p => p.remove());

        document.querySelectorAll('.port.connected').forEach(port => {
            port.classList.remove('connected');
        });

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

            // ===== НОВАЯ ЛОГИКА КРИВЫХ =====
            let d;
            
            // Определяем, входной это порт или выходной и технический ли это
            const isToPortInput = toPort.classList.contains('input');
            const isToPortCondition = conn.toPort === 'cond-0';
            const isFromPortOutput = fromPort.classList.contains('output');

            // Расстояние для контрольных точек (зависит от расстояния между портами)
            const horizontalDist = Math.abs(endPos.x - startPos.x);
            const verticalDist = Math.abs(endPos.y - startPos.y);
            const controlDist = Math.max(horizontalDist * 0.3, 40);

            if (isToPortCondition) {
                // ТЕХНИЧЕСКИЙ ПОРТ: касательная вертикальна, вектор (0, 1)
                // Линия подходит с отступом сверху (из положительного Y в CSS, т.е. снизу)
                const c1y = startPos.y + verticalDist * 0.3;
                const c2y = endPos.y - verticalDist * 0.3;
                d = `M ${startPos.x} ${startPos.y} C ${startPos.x} ${c1y}, ${endPos.x} ${c2y}, ${endPos.x} ${endPos.y}`;
            } else {
                // ОБЫЧНЫЕ ПОРТЫ: горизонтальные касательные
                let cx1, cx2;

                if (isFromPortOutput) {
                    // Выходной порт: касательная вправо, вектор (1, 0)
                    cx1 = startPos.x + controlDist;
                } else {
                    // Если это случайно не выходной, подстраховка
                    cx1 = startPos.x + controlDist;
                }

                if (isToPortInput) {
                    // Входной порт: касательная влево, вектор (-1, 0)
                    cx2 = endPos.x - controlDist;
                } else {
                    // Если это случайно не входной, подстраховка
                    cx2 = endPos.x - controlDist;
                }

                d = `M ${startPos.x} ${startPos.y} C ${cx1} ${startPos.y}, ${cx2} ${endPos.y}, ${endPos.x} ${endPos.y}`;
            }

            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', d);
            path.setAttribute('fill', 'none');  // ← ДОБАВЛЯЕМ ЭТО

            // ===== ОПРЕДЕЛЯЕМ КЛАСС ПО ТИПУ СИГНАЛА =====
            let cssClass = 'connection';
            const signalType = conn.signalType;

            if (signalType === SIGNAL_TYPE.TRUE) {
                cssClass += ' true-conn';
            } else if (signalType === SIGNAL_TYPE.FALSE) {
                cssClass += ' false-conn';
            } else if (signalType === SIGNAL_TYPE.LOGIC) {
                cssClass += ' logic-conn';
            } else if (signalType === SIGNAL_TYPE.NUMERIC) {
                cssClass += ' numeric-conn';
            } else if (signalType === SIGNAL_TYPE.ANY) {
                cssClass += ' any-conn';
            }

            // Сохраняем старую логику для yes/no из совместимости
            const fromElemType = AppState.elements[conn.fromElement]?.type;
            if (['and', 'or', 'if'].includes(fromElemType)) {
                if (conn.fromPort === 'out-0') cssClass += ' yes-conn';
                else if (conn.fromPort === 'out-1') cssClass += ' no-conn';
            }

            path.setAttribute('class', cssClass);
            path.dataset.fromElem = conn.fromElement;
            path.dataset.toElem = conn.toElement;
            path.dataset.fromPort = conn.fromPort;
            path.dataset.toPort = conn.toPort;

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