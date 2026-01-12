/**
 * Модуль управления выходными сигналами
 */

const Outputs = {
    /**
     * Обновление статуса выходных элементов
     * Вызывается при каждом изменении схемы
     */
    updateOutputStatus() {
        this.clearAllOutputHighlights();
        AppState.outputs.logical = [];
        AppState.outputs.numeric = [];
        updateFrameChildren();

        // Обработка элементов-выходов
        Object.values(AppState.elements).forEach(elem => {
            if (!elem || elem.type !== 'output') return;

            // Проверяем, к чему подключен вход этого выхода
            const inputConns = AppState.connections.filter(c => 
                c.toElement === elem.id && c.toPort === 'in-0'
            );

            // Каждое соединение к выходу — это отдельный выход
            inputConns.forEach((conn, index) => {
                const fromElem = AppState.elements[conn.fromElement];
                if (!fromElem) return;

                const outputType = conn.signalType;
                const outputInfo = {
                    id: `${elem.id}_conn_${index}`,
                    elementId: elem.id,
                    sourceElementId: conn.fromElement,
                    sourcePort: conn.fromPort,
                    portIndex: 0,
                    portId: 'in-0',
                    type: outputType,
                    label: elem.props?.label || 'Выход',
                    elementType: 'output',
                    elementName: elem.props?.label || 'Выход',
                    name: elem.props?.label || 'Выход'
                };

                if (outputType === SIGNAL_TYPE.LOGIC) {
                    AppState.outputs.logical.push(outputInfo);
                } else if (outputType === SIGNAL_TYPE.NUMBER) {
                    AppState.outputs.numeric.push(outputInfo);
                }

                // Подсветим входной порт
                this.highlightOutputPort(elem.id, 0, outputType);
            });
        });

        this.updateOutputCounter();
    },

    /**
     * Очистка всех выделений выходов
     */
    clearAllOutputHighlights() {
        document.querySelectorAll('.port.output-active').forEach(port => {
            port.classList.remove('output-active');
        });

        document.querySelectorAll('.element.has-output').forEach(elem => {
            elem.classList.remove('has-output');
        });

        document.querySelectorAll('.element.output-ambiguous').forEach(el => el.classList.remove('output-ambiguous'));
        document.querySelectorAll('.element.output-missing').forEach(el => el.classList.remove('output-missing'));
    },

    /**
     * Выделение выходного порта
     */
    highlightOutputPort(elemId, portIndex, portType) {
        const elem = document.getElementById(elemId);
        if (!elem) return;

        const port = elem.querySelector(`.port.output[data-port="out-${portIndex}"]`);
        if (port) {
            port.classList.add('output-active');
        }

        // Добавляем класс элементу (даёт общий визуал)
        elem.classList.add('has-output');
    },

    /**
     * Обновление счётчика выходов в меню
     */
    updateOutputCounter() {
        const counter = document.getElementById('output-counter');
        if (counter) {
            const total = AppState.outputs.logical.length + AppState.outputs.numeric.length;
            counter.textContent = total;
            counter.style.display = total > 0 ? 'inline-block' : 'none';
        }
    },

    /**
     * Получить все выходы для сохранения в проект
     */
    getOutputsForSave() {
        // Сохраняем информацию о frame/inner для рамок
        return {
            logical: AppState.outputs.logical.map(o => ({
                id: o.id,
                elementId: o.elementId,
                frameId: o.frameId || null,
                innerElementId: o.innerElementId || null,
                portIndex: o.portIndex ?? o.innerPortIndex ?? null,
                portLabel: o.label
            })),
            numeric: AppState.outputs.numeric.map(o => ({
                id: o.id,
                elementId: o.elementId,
                frameId: o.frameId || null,
                innerElementId: o.innerElementId || null,
                portIndex: o.portIndex ?? o.innerPortIndex ?? null,
                portLabel: o.label
            }))
        };
    },

    /**
     * Подсветить конкретный выход (при наведении в списке)
     */
    highlightOutput(elementId, highlight = true) {
        const elem = document.getElementById(elementId);
        if (elem) {
            if (highlight) {
                elem.classList.add('output-highlighted');
            } else {
                elem.classList.remove('output-highlighted');
            }
        }
    },

    /**
     * Перейти к элементу выхода на схеме (elementId — фокусируемый элемент; для рамок это id рамки)
     */
    navigateToOutput(elementId) {
        const elemData = AppState.elements[elementId];
        if (!elemData) return;

        // Центрируем viewport на элементе
        const container = document.getElementById('workspace-container');
        const rect = container.getBoundingClientRect();

        const centerX = elemData.x + elemData.width / 2;
        const centerY = elemData.y + elemData.height / 2;

        AppState.viewport.panX = rect.width / 2 - centerX * AppState.viewport.zoom;
        AppState.viewport.panY = rect.height / 2 - centerY * AppState.viewport.zoom;

        Viewport.updateTransform();

        // Выделяем элемент
        Elements.selectElement(elementId);

        // Временная подсветка
        this.highlightOutput(elementId, true);
        setTimeout(() => this.highlightOutput(elementId, false), 2000);
    }
};