/**
 * Модуль работы с элементами схемы
 */

const Elements = {
    /**
     * Генерация HTML для элемента
     */
        createElementHTML(elemType, elemId, x, y, props = {}, width, height) {
            const config = ELEMENT_TYPES[elemType];
            if (!config) throw new Error(`Неизвестный тип элемента: ${elemType}`);

            const safe = (value, fallback = '') => (value === null || value === undefined) ? fallback : String(value);
            const w = width ?? config.minWidth ?? 120;
            const h = height ?? config.minHeight ?? 60;

            const getPortClass = (signalType, direction) => {
                const base = direction === 'output' ? 'port output' : 'port input';
                if (signalType === SIGNAL_TYPE.LOGIC) return `${base} logic-port`;
                if (signalType === SIGNAL_TYPE.NUMBER) return `${base} number-port`;
                return `${base} any-port`;
            };

            // Эта функция buildConditionPort будет вызываться ИНАЧЕ, а не внутри innerHTML
            // Она тут остается, но ее результат не встраивается в HTML-строку напрямую, кроме формулы
            const buildConditionPortHTML = () => {
                return `
                    <div class="condition-port-wrapper">
                        <div class="condition-port-label">условие</div>
                        <div class="port input condition-port"
                            data-port="cond-0"
                            data-element="${elemId}"
                            data-signal-type="${SIGNAL_TYPE.LOGIC}"
                            title="Техническое условие">
                        </div>
                    </div>`;
            };


            const buildInputPorts = (count, types = [], labels = []) => {
                let html = '';
                for (let i = 0; i < count; i++) {
                    const type = types[i] ?? types[types.length - 1] ?? SIGNAL_TYPE.ANY;
                    html += `<div class="${getPortClass(type, 'input')}" data-port="in-${i}" data-element="${elemId}" data-signal-type="${type}" title="${labels[i] || `Вход ${i+1}`}"></div>`;
                }
                return html;
            };

            const buildOutputPorts = (count, types = [], labels = []) => {
                let html = '';
                for (let i = 0; i < count; i++) {
                    const type = types[i] ?? types[types.length - 1] ?? SIGNAL_TYPE.ANY;
                    html += `<div class="${getPortClass(type, 'output')}" data-port="out-${i}" data-element="${elemId}" data-signal-type="${type}" title="${labels[i] || `Выход ${i+1}`}"></div>`;
                }
                return html;
            };

            const resizeHandles = config.resizable ? `<div class="resize-handle handle-se" data-direction="se"></div><div class="resize-handle handle-e" data-direction="e"></div><div class="resize-handle handle-s" data-direction="s"></div>` : '';
            // hasCondClass будет добавляться в addElement
            // const hasCondClass = config.hasConditionPort ? 'has-condition-port' : '';

            let innerHTML = '';

            if (elemType === 'input-signal') {
                const name = safe(props.name, 'Сигнал');
                const type = props.signalType || SIGNAL_TYPE.NUMBER;
                const symbol = type === SIGNAL_TYPE.LOGIC ? '🔀' : '🔢';
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">Источник</div>
                    <div class="element-body">
                        <div class="element-symbol">
                            <span class="input-signal-icon">${symbol}</span>
                            <span class="input-signal-name">${name}</span>
                        </div>
                        <div class="ports-right">
                            ${buildOutputPorts(1, [type], ['Выход'])}
                        </div>
                    </div>`;
            }
            else if (elemType === 'const') {
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">Константа</div>
                    <div class="element-body">
                        <div class="element-symbol">${props.value ?? 0}</div>
                        <div class="ports-right">
                            ${buildOutputPorts(1, [SIGNAL_TYPE.NUMBER], ['Значение'])}
                        </div>
                    </div>`;
            }
            else if (elemType === 'separator') {
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">Сепаратор</div>
                    <div class="element-body">
                        <div class="ports-left">${buildInputPorts(1, config.inputTypes, config.inputLabels)}</div>
                        <div class="element-symbol">✓/✗</div>
                        <div class="ports-right">
                            <div class="port output logic-port true-port" data-port="out-0" data-element="${elemId}" data-signal-type="${SIGNAL_TYPE.TRUE}" title="ИСТИНА"></div>
                            <div class="port output logic-port false-port" data-port="out-1" data-element="${elemId}" data-signal-type="${SIGNAL_TYPE.FALSE}" title="ЛОЖЬ"></div>
                        </div>
                    </div>`;
            }
            else if (elemType === 'and' || elemType === 'or') {
                const gateSymbol = elemType === 'and' ? '∧' : '∨';
                const inputCount = props.inputCount || config.defaultProps?.inputCount || 2;
                
                // Генерируем динамические входы
                let inputsHTML = '';
                for (let i = 0; i < inputCount; i++) {
                    inputsHTML += `<div class="port input logic-port" data-port="in-${i}" data-element="${elemId}" data-signal-type="${SIGNAL_TYPE.LOGIC}" title="Вход ${i+1}"></div>`;
                }
                
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">${config.name}</div>
                    <div class="element-body">
                        <div class="ports-left">
                            ${inputsHTML}
                        </div>
                        <div class="element-symbol">${gateSymbol}</div>
                        <div class="ports-right">
                            <div class="port output logic-port" data-port="out-0" data-element="${elemId}" data-signal-type="${SIGNAL_TYPE.LOGIC}" title="Результат"></div>
                        </div>
                    </div>`;
            }
            else if (elemType === 'if') {
                const op = safe(props.operator, '=');
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">Условие</div>
                    <div class="element-body">
                        <div class="ports-left">${buildInputPorts(2, config.inputTypes, config.inputLabels)}</div>
                        <div class="element-symbol">${op}</div>
                        <div class="ports-right">
                            ${buildOutputPorts(1, [SIGNAL_TYPE.LOGIC], ['результат'])}
                        </div>
                    </div>`;
            }
            else if (elemType === 'not') {
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">НЕ</div>
                    <div class="element-body">
                        <div class="ports-left">${buildInputPorts(1, [SIGNAL_TYPE.LOGIC], ['A'])}</div>
                        <div class="element-symbol">¬</div>
                        <div class="ports-right">
                            ${buildOutputPorts(1, [SIGNAL_TYPE.LOGIC], ['¬A'])}
                        </div>
                    </div>`;
            }
            else if (elemType === 'formula') {
                const inputCount = props.inputCount || config.defaultProps?.inputCount || config.inputs || 2;
                const expression = safe(props.expression);
                const displayExpression = expression
                    ? (expression.length > 12 ? `${expression.slice(0, 12)}…` : expression)
                    : 'f(x)';

                innerHTML = `
                    ${buildConditionPortHTML()}
                    <div class="element-header" style="background:${config.color};">Формула</div>
                    <div class="element-body">
                        <div class="ports-left">${buildInputPorts(inputCount, config.inputTypes, config.inputLabels)}</div>
                        <div class="element-symbol">${displayExpression}</div>
                        <div class="ports-right">
                            ${buildOutputPorts(1, [SIGNAL_TYPE.NUMBER], ['Результат'])}
                        </div>
                    </div>`;
            }
            else if (elemType === 'output') {
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">Выход</div>
                    <div class="element-body">
                        <div class="ports-left">
                            ${buildInputPorts(1, [SIGNAL_TYPE.ANY], ['сигнал'])}
                        </div>
                        <div class="element-symbol">${safe(props.label, 'Выход')}</div>
                        <div class="ports-right"></div>
                    </div>`;
            } else { // Для любых других (fallback)
                innerHTML = `
                    <div class="element-header" style="background:${config.color};">${config.name}</div>
                    <div class="element-body">
                        <div class="ports-left">${buildInputPorts(config.inputs || 0, config.inputTypes, config.inputLabels)}</div>
                        <div class="element-symbol">${config.name}</div>
                        <div class="ports-right">
                            ${buildOutputPorts(config.outputs || 0, config.outputTypes, config.outputLabels)}
                        </div>
                    </div>`;
            }


            const html = `
                <div class="element ${elemType}" id="${elemId}" 
                    style="left:${x}px; top:${y}px; width:${w}px; height:${h}px;" data-type="${elemType}">
                    ${innerHTML}
                    ${resizeHandles}
                </div>`;

            return { html, width: w, height: h };
        },

    /**
     * Добавление элемента
     */
        addElement(elemType, x, y, props = {}, elemId = null, customWidth = null, customHeight = null) {
            const config = ELEMENT_TYPES[elemType];
            if (!config) {
                console.error(`Неизвестный тип элемента: ${elemType}`);
                return null;
            }

            if (!elemId) {
                elemId = `${elemType}-${++AppState.elementCounter}`;
            }

            let width = customWidth;
            let height = customHeight;

            if (width === null || width === undefined) {
                width = config.minWidth || 140;
            }
            if (height === null || height === undefined) {
                height = config.minHeight || 70;
            }

            try {
                const result = this.createElementHTML(elemType, elemId, x, y, props, width, height);
                if (!result || !result.html) {
                    console.error('createElementHTML вернул пустой результат');
                    return null;
                }

                const workspace = document.getElementById('workspace');
                const wrapper = document.createElement('div');
                wrapper.innerHTML = result.html.trim();
                const element = wrapper.firstElementChild;
                if (!element) {
                    console.error('Не удалось создать DOM элемент из HTML');
                    return null;
                }

                // Добавляем класс для отступа
                if (config.hasConditionPort) {
                    element.classList.add('has-condition-port');
                }

                workspace.appendChild(element);

                AppState.elements[elemId] = {
                    id: elemId,
                    type: elemType,
                    x,
                    y,
                    width: result.width || width,
                    height: result.height || height,
                    props: { ...(config.defaultProps || {}), ...(props || {}) }
                };

                // ЕСЛИ У ЭЛЕМЕНТА ЕСТЬ COND-ПОРТ (И ОН НЕ ФОРМУЛА, КОТОРАЯ УЖЕ ИМЕЕТ ЕГО В HTML)
                if (config.hasConditionPort && elemType !== 'formula') {
                    const condPortWrapper = document.createElement('div');
                    condPortWrapper.innerHTML = `
                        <div class="condition-port-wrapper">
                            <div class="condition-port-label">условие</div>
                            <div class="port input condition-port"
                                data-port="cond-0"
                                data-element="${elemId}"
                                data-signal-type="${SIGNAL_TYPE.LOGIC}"
                                title="Техническое условие">
                            </div>
                        </div>`;
                    element.prepend(condPortWrapper.firstElementChild); // Вставляем в самое начало элемента
                }


                this.setupElementHandlers(elemId); // Передаем ID элемента

                // Порты инициализируются внутри setupElementHandlers, нет нужды здесь
                // element.querySelectorAll('.port').forEach(port => {
                //     Connections.setupPortHandlers(port);
                // });

                Connections.drawConnections(); // Перерисовываем соединения, чтобы учесть новые порты
                Viewport.updateMinimap();
                return elemId;
            } catch (err) {
                console.error(`Ошибка при добавлении элемента ${elemType}:`, err);
                return null;
            }
        },

    /**
     * Обновление входов логического элемента (AND, OR)
     */
    updateLogicGateInputs(elemId, inputCount) {
        const elem = document.getElementById(elemId);
        if (!elem) return;

        const portsLeft = elem.querySelector('.ports-left');
        if (!portsLeft) return;

        // Удаляем соединения к портам, которые больше не существуют
        AppState.connections = AppState.connections.filter(c => {
            if (c.toElement === elemId && c.toPort.startsWith('in-')) {
                const portNum = parseInt(c.toPort.split('-')[1], 10);
                return portNum < inputCount;
            }
            return true;
        });

        // Генерируем новые входы
        let inputsHTML = '';
        for (let i = 0; i < inputCount; i++) {
            inputsHTML += `
                <div class="port input logic-port"
                    data-port="in-${i}"
                    data-element="${elemId}"
                    data-signal-type="${SIGNAL_TYPE.LOGIC}"
                    title="Вход ${i+1}">
                </div>
            `;
        }
        portsLeft.innerHTML = inputsHTML;

        // Переподключаем обработчики
        portsLeft.querySelectorAll('.port').forEach(port =>
            Connections.setupPortHandlers(port)
        );

        Connections.drawConnections();
    },

    /**
     * Удаление элемента
     */
    deleteElement(elemId) {
        AppState.connections = AppState.connections.filter(c =>
            c.fromElement !== elemId && c.toElement !== elemId
        );

        const elem = document.getElementById(elemId);
        if (elem) elem.remove();

        delete AppState.elements[elemId];

        if (AppState.selectedElement === elemId) {
            AppState.selectedElement = null;
        }

        Connections.drawConnections();
        Viewport.updateMinimap();
    },

    /**
     * Выделение элемента
     */
    selectElement(elemId) {
        if (AppState.selectedElement) {
            const oldElem = document.getElementById(AppState.selectedElement);
            if (oldElem) oldElem.classList.remove('selected');
        }

        AppState.selectedElement = elemId;
        const elem = document.getElementById(elemId);
        if (elem) elem.classList.add('selected');

        const elemData = AppState.elements[elemId];
        if (elemData) {
            document.getElementById('selection-info').textContent =
                `Выбрано: ${ELEMENT_TYPES[elemData.type]?.name || elemData.type}`;
        }
    },

    /**
     * Снять выделение
     */
    deselectAll() {
        if (AppState.selectedElement) {
            const elem = document.getElementById(AppState.selectedElement);
            if (elem) elem.classList.remove('selected');
            AppState.selectedElement = null;
        }
        document.getElementById('selection-info').textContent = '';
    },

    /**
     * Настройка обработчиков элемента
     */
    setupElementHandlers(elemId) {
        try {
            const elem = document.getElementById(elemId);
            if (!elem) return;

            elem.addEventListener('mousedown', (e) => {
                if (e.target.classList.contains('port')) return;
                if (e.target.classList.contains('resize-handle')) return;

                e.preventDefault();
                e.stopPropagation();

                this.selectElement(elemId);

                AppState.draggingElement = elemId;
                const canvasPos = screenToCanvas(e.clientX, e.clientY);
                const elemData = AppState.elements[elemId];
                AppState.dragOffset.x = canvasPos.x - elemData.x;
                AppState.dragOffset.y = canvasPos.y - elemData.y;
            });

            elem.addEventListener('dblclick', (e) => {
                if (e.target.classList.contains('port')) return;
                const config = ELEMENT_TYPES[AppState.elements[elemId].type];
                if (config?.hasProperties) {
                    Modal.showPropertiesModal(elemId);
                }
            });

            elem.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                this.showContextMenu(e.clientX, e.clientY, elemId);
            });

            const handles = elem.querySelectorAll('.resize-handle');
            handles.forEach(handle => this.setupResizeHandlers(handle, elemId));

            const ports = elem.querySelectorAll('.port');
            ports.forEach(port => Connections.setupPortHandlers(port));

        } catch (err) {
            console.error('setupElementHandlers error for', elemId, err);
        }
    },

    /**
     * Контекстное меню
     */
    showContextMenu(x, y, elemId) {
        const menu = document.getElementById('context-menu');
        menu.style.left = `${x}px`;
        menu.style.top = `${y}px`;
        menu.style.display = 'block';
        menu.dataset.elementId = elemId;
    },

    /**
     * Настройка resize
     */
    setupResizeHandlers(handle, elemId) {
        handle.addEventListener('mousedown', (e) => {
            e.stopPropagation();
            e.preventDefault();

            const elemData = AppState.elements[elemId];

            AppState.resizing = {
                elemId: elemId,
                handle: handle.dataset.direction,
                startX: e.clientX,
                startY: e.clientY,
                startWidth: elemData.width,
                startHeight: elemData.height,
                startLeft: elemData.x,
                startTop: elemData.y
            };
        });
    },

    /**
     * Обработка resize
     */
    handleResize(e) {
        if (!AppState.resizing) return;

        const { elemId, handle, startX, startY, startWidth, startHeight, startLeft, startTop } = AppState.resizing;
        const elem = document.getElementById(elemId);
        const elemData = AppState.elements[elemId];
        const config = ELEMENT_TYPES[elemData.type];

        const dx = (e.clientX - startX) / AppState.viewport.zoom;
        const dy = (e.clientY - startY) / AppState.viewport.zoom;

        let newWidth = startWidth;
        let newHeight = startHeight;
        let newLeft = startLeft;
        let newTop = startTop;

        if (handle.includes('e')) {
            newWidth = Math.max(config.minWidth, startWidth + dx);
        }
        if (handle.includes('w')) {
            newWidth = Math.max(config.minWidth, startWidth - dx);
            newLeft = startLeft + (startWidth - newWidth);
        }
        if (handle.includes('s')) {
            newHeight = Math.max(config.minHeight, startHeight + dy);
        }
        if (handle.includes('n')) {
            newHeight = Math.max(config.minHeight, startHeight - dy);
            newTop = startTop + (startHeight - newHeight);
        }

        elem.style.width = `${newWidth}px`;
        elem.style.height = `${newHeight}px`;
        elem.style.left = `${newLeft}px`;
        elem.style.top = `${newTop}px`;

        elemData.width = newWidth;
        elemData.height = newHeight;
        elemData.x = newLeft;
        elemData.y = newTop;

        Connections.drawConnections();
    },

    /**
     * Обработка перетаскивания элемента
     */
    handleDrag(e) {
        if (!AppState.draggingElement) return;

        const canvasPos = screenToCanvas(e.clientX, e.clientY);
        const x = canvasPos.x - AppState.dragOffset.x;
        const y = canvasPos.y - AppState.dragOffset.y;

        const elemId = AppState.draggingElement;
        const elem = document.getElementById(elemId);
        const elemData = AppState.elements[elemId];

        elem.style.left = `${x}px`;
        elem.style.top = `${y}px`;

        elemData.x = x;
        elemData.y = y;

        Connections.drawConnections();
    },

    /**
     * Обновление входов формулы
     */
    updateFormulaInputs(elemId, inputCount) {
        const elem = document.getElementById(elemId);
        if (!elem) return;

        const portsLeft = elem.querySelector('.ports-left');
        if (!portsLeft) return;

        AppState.connections = AppState.connections.filter(c => {
            if (c.toElement === elemId && c.toPort.startsWith('in-')) {
                const portNum = parseInt(c.toPort.split('-')[1], 10);
                return portNum < inputCount;
            }
            return true;
        });

        let inputsHTML = '';
        for (let i = 0; i < inputCount; i++) {
            inputsHTML += `
                <div class="port input any-port"
                     data-port="in-${i}"
                     data-element="${elemId}"
                     data-signal-type="${SIGNAL_TYPE.ANY}"
                     title="in${i} (Любой)">
                </div>
            `;
        }
        portsLeft.innerHTML = inputsHTML;

        portsLeft.querySelectorAll('.port').forEach(port =>
            Connections.setupPortHandlers(port)
        );

        Connections.drawConnections();
    },

    /**
     * Рассчитать оптимальный размер элемента на основе количества портов
     */
    calculateOptimalHeight(elemId, inputCount, outputCount = 1) {
        const elem = AppState.elements[elemId];
        if (!elem) return null;

        const config = ELEMENT_TYPES[elem.type];
        if (!config || !config.resizable) return null;

        // Базовая высота
        let baseHeight = config.minHeight || 60;
        
        // Каждый порт требует примерно 25-30px высоты
        const portSpacing = 28;
        const maxPorts = Math.max(inputCount, outputCount);
        
        // Добавляем высоту для портов (кроме первого, который уже в baseHeight)
        const additionalHeight = (maxPorts - 1) * portSpacing;
        const newHeight = Math.max(baseHeight, baseHeight + additionalHeight);
        
        return newHeight;
    },

    /**
     * Обновление размера элемента при изменении портов
     */
    updateElementSize(elemId) {
        const elem = document.getElementById(elemId);
        const elemData = AppState.elements[elemId];
        
        if (!elem || !elemData) return;

        const config = ELEMENT_TYPES[elemData.type];
        if (!config || !config.resizable) return;

        let inputCount = 0;
        let outputCount = config.outputs || 1;

        // Определяем количество входов
        if (elemData.type === 'and' || elemData.type === 'or' || elemData.type === 'formula') {
            inputCount = elemData.props.inputCount || config.inputs || 2;
        } else {
            inputCount = config.inputs || 0;
        }

        // Рассчитываем новую высоту
        const newHeight = this.calculateOptimalHeight(elemId, inputCount, outputCount);
        
        if (newHeight && newHeight !== elemData.height) {
            elemData.height = newHeight;
            elem.style.height = `${newHeight}px`;
            
            // Перерисовываем соединения, т.к. изменился размер элемента
            Connections.drawConnections();
            Viewport.updateMinimap();
        }
    }


};