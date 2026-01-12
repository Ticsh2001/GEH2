/**
 * Модуль модальных окон
 */

const Modal = {
    /**
     * Инициализация модальных окон
     */
    init() {
        // Модальное окно свойств элемента
        document.getElementById('modal-save').addEventListener('click', () => {
            this.saveElementProperties();
        });

        document.getElementById('modal-cancel').addEventListener('click', () => {
            this.hideModal('modal-overlay');
        });

        document.getElementById('modal-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'modal-overlay') {
                this.hideModal('modal-overlay');
            }
        });

        // Модальное окно свойств проекта
        document.getElementById('project-modal-save').addEventListener('click', () => {
            this.saveProjectProperties();
        });

        document.getElementById('project-modal-cancel').addEventListener('click', () => {
            this.hideModal('project-modal-overlay');
        });

        document.getElementById('project-modal-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'project-modal-overlay') {
                this.hideModal('project-modal-overlay');
            }
        });
    },

    /**
     * Показать модальное окно
     */
    showModal(modalId) {
        document.getElementById(modalId).style.display = 'flex';
    },

    /**
     * Скрыть модальное окно
     */
    hideModal(modalId) {
        document.getElementById(modalId).style.display = 'none';
    },

    /**
     * Показать свойства элемента
     */
    showPropertiesModal(elemId) {
        const elemData = AppState.elements[elemId];
        const elemType = elemData.type;
        const props = elemData.props;
        const config = ELEMENT_TYPES[elemType];

        const modalOverlay = document.getElementById('modal-overlay');
        const modalTitle = document.getElementById('modal-title');
        const modalContent = document.getElementById('modal-content');

        modalTitle.textContent = `Свойства: ${config.name}`;

        let contentHTML = '';

 if (elemType === 'input-signal') {
  const signalType = props.signalType || SIGNAL_TYPE.NUMBER;

  contentHTML = `
    <div class="modal-row">
      <label>Название сигнала:</label>
      <input type="text" id="prop-name" value="${props.name || ''}" placeholder="Например: 10LBA..." />
      <small style="color:#999;">
        Поиск по маске через * (например: *MAA*CP*)
      </small>
      <div id="signal-filter-results"
           style="max-height:160px; overflow-y:auto; background:#0f3460; border-radius:5px; margin-top:6px; display:none;">
      </div>
    </div>

    <div class="modal-row">
      <label>Описание сигнала:</label>
      <textarea id="prop-description" readonly>${props.description || ''}</textarea>
    </div>

    <div class="modal-row">
      <label>Тип сигнала:</label>
      <select id="prop-signal-type">
        <option value="${SIGNAL_TYPE.NUMBER}" ${signalType === SIGNAL_TYPE.NUMBER ? 'selected' : ''}>Числовой</option>
        <option value="${SIGNAL_TYPE.LOGIC}" ${signalType === SIGNAL_TYPE.LOGIC ? 'selected' : ''}>Логический</option>
      </select>
    </div>
  `;

  // ВАЖНО: обработчики можно навесить только после того, как модалка вставила HTML в DOM.
  // Поэтому ниже мы добавим "хуки" после того, как modalContent.innerHTML применится.
  // (Смотри пункт 2 — небольшая вставка в конце showPropertiesModal)
} else if (elemType === 'if') {
            contentHTML = `
                <div class="modal-row">
                    <label>Оператор сравнения:</label>
                    <select id="prop-operator">
                        <option value="=" ${props.operator === '=' ? 'selected' : ''}>=  (равно)</option>
                        <option value=">" ${props.operator === '>' ? 'selected' : ''}>>  (больше)</option>
                        <option value="<" ${props.operator === '<' ? 'selected' : ''}><  (меньше)</option>
                        <option value=">=" ${props.operator === '>=' ? 'selected' : ''}>= (больше или равно)</option>
                        <option value="<=" ${props.operator === '<=' ? 'selected' : ''}>= (меньше или равно)</option>
                        <option value="!=" ${props.operator === '!=' ? 'selected' : ''}>!= (не равно)</option>
                    </select>
                </div>
            `;
        } else if (elemType === 'and' || elemType === 'or') {
            contentHTML = `
                <div class="modal-row">
                    <label>Количество входов:</label>
                    <input type="number" id="prop-input-count" value="${props.inputCount || 2}" min="2" max="10">
                </div>
                <div class="modal-row">
                    <p style="color: #aaa; font-size: 12px;">
                        Измените количество входных портов для этого логического элемента.
                        Лишние соединения будут автоматически удалены.
                    </p>
                </div>
            `;
        } else if (elemType === 'const') {
            contentHTML = `
                <div class="modal-row">
                    <label>Значение:</label>
                    <input type="number" id="prop-value" value="${props.value ?? 0}" step="any">
                </div>
            `;
        } 
        else if (elemType === 'group') {
            contentHTML = `
            <div class="modal-row">
                <label>Название группы:</label>
                <input type="text" id="prop-title" value="${props.title || 'Группа'}">
            </div>`;
        }   
        
        else if (elemType === 'formula') {
            let signalsHTML = '';
            AppState.connections.forEach(conn => {
                if (conn.toElement === elemId) {
                    const fromElem = AppState.elements[conn.fromElement];
                    if (fromElem) {
                        const signalName = fromElem.props?.name || fromElem.id;
                        signalsHTML += `<div class="signal-item" data-signal="${signalName}">${signalName} (${conn.toPort})</div>`;
                    }
                }
            });

            contentHTML = `
                <div class="modal-row">
                    <label>Количество входов:</label>
                    <input type="number" id="prop-input-count" value="${props.inputCount || 2}" min="1" max="10">
                </div>
                <div class="modal-row">
                    <label>Входные сигналы (двойной клик для вставки):</label>
                    <div class="signal-list" id="signal-list">
                        ${signalsHTML || '<div style="color:#888;padding:5px;">Нет подключённых сигналов</div>'}
                    </div>
                </div>
                <div class="modal-row">
                    <label>Выражение:</label>
                    <textarea id="prop-expression">${props.expression || ''}</textarea>
                </div>
            `;
        } else if (elemType === 'output') {
            contentHTML = `
                <div class="modal-row">
                    <label>Название выхода:</label>
                    <input type="text" id="prop-label" value="${props.label || 'Выход'}">
                </div>
                <div class="modal-row">
                    <label>Группировка (опционально):</label>
                    <input type="text" id="prop-output-group" value="${props.outputGroup || ''}" placeholder="для логической группировки выходов">
                </div>
            `;
        }
        

        modalContent.innerHTML = contentHTML;
        // --- post init handlers (когда DOM модалки уже существует) ---
        if (elemType === 'input-signal') {
        const input = document.getElementById('prop-name');
        const results = document.getElementById('signal-filter-results');
        const descField = document.getElementById('prop-description');

        let timer = null;

        const renderList = (items) => {
            if (!items || items.length === 0) {
            results.innerHTML = '<div style="color:#666;padding:6px;">Нет совпадений</div>';
            results.style.display = 'block';
            return;
            }

            results.innerHTML = items.map(s => `
            <div class="signal-result-item"
                style="padding:6px 8px; cursor:pointer; border-bottom:1px solid rgba(255,255,255,0.08);">
                <div style="font-weight:600;">${s.Tagname}</div>
                <div style="color:#aaa; font-size:11px;">${s.Description || ''}</div>
            </div>
            `).join('');

            results.style.display = 'block';

            results.querySelectorAll('.signal-result-item').forEach((div, i) => {
            div.addEventListener('click', () => {
                const chosen = items[i];
                input.value = chosen.Tagname;
                descField.value = chosen.Description || '';
                results.style.display = 'none';
            });
            });
        };

        const search = async () => {
            const mask = (input.value || '').trim();

            // Показываем список только если пользователь реально использует маску
            if (!mask.includes('*')) {
            results.style.display = 'none';
            return;
            }

            results.innerHTML = '<div style="color:#666;padding:6px;">Поиск...</div>';
            results.style.display = 'block';

            try {
            // В settings.js должен быть метод Settings.fetchSignals(mask, limit)
            const data = await Settings.fetchSignals(mask, 50);
            renderList(data.items || []);
            } catch (e) {
            results.innerHTML = '<div style="color:#666;padding:6px;">Ошибка загрузки сигналов</div>';
            results.style.display = 'block';
            console.error(e);
            }
        };

        input.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(search, 200); // debounce
        });

        // опционально: закрывать список кликом вне
        document.addEventListener('mousedown', (e) => {
            if (!results.contains(e.target) && e.target !== input) {
            results.style.display = 'none';
            }
        }, { once: true });
        }
        modalOverlay.dataset.elementId = elemId;
        this.showModal('modal-overlay');

        // Обработчик вставки сигналов для формулы
        if (elemType === 'formula') {
            document.querySelectorAll('.signal-item').forEach(item => {
                item.addEventListener('dblclick', () => {
                    const signal = item.dataset.signal;
                    const textarea = document.getElementById('prop-expression');
                    textarea.value += signal;
                    textarea.focus();
                });
            });
        }
    },

    /**
     * Сохранить свойства элемента
     */
/**
 * Сохранить свойства элемента
 */
    saveElementProperties() {
        try {
            const modalOverlay = document.getElementById('modal-overlay');
            const elemId = modalOverlay.dataset.elementId;
            const elemData = AppState.elements[elemId];
            const elemType = elemData.type;
            const elem = document.getElementById(elemId);

            if (elemType === 'input-signal') {
                const name = document.getElementById('prop-name').value || 'Сигнал';
                const description = document.getElementById('prop-description').value || '';
                const signalType = document.getElementById('prop-signal-type').value;

                const oldSignalType = elemData.props.signalType;
                elemData.props.name = name;
                elemData.props.description = description;
                elemData.props.signalType = signalType;

                if (oldSignalType !== signalType) {
                    AppState.connections = AppState.connections.filter(conn => {
                    if (conn.fromElement === elemId) {
                        const toPortIndex = parseInt(conn.toPort.split('-')[1]);
                        const inputType = getInputPortType(conn.toElement, toPortIndex);
                        return areTypesCompatible(signalType, inputType);
                    }
                    return true;
                    });
                }

                const { html } = Elements.createElementHTML(
                    elemType, elemId, elemData.x, elemData.y, elemData.props, elemData.width, elemData.height
                );
                elem.outerHTML = html;

                Elements.setupElementHandlers(elemId);
                Connections.drawConnections();
            } else if (elemType === 'if') {
                const operator = document.getElementById('prop-operator').value;
                elemData.props.operator = operator;
                const symbol = elem.querySelector('.element-symbol');
                if (symbol) symbol.textContent = operator;
            
            } else if (elemType === 'const') {
                const value = parseFloat(document.getElementById('prop-value').value) || 0;
                elemData.props.value = value;
                const symbol = elem.querySelector('.element-symbol');
                if (symbol) symbol.textContent = String(value);
            
            } else if (elemType === 'formula') {
                const expression = document.getElementById('prop-expression').value;
                const inputCount = parseInt(document.getElementById('prop-input-count').value) || 2;

                elemData.props.expression = expression;
                elemData.props.inputCount = inputCount;

                const symbol = elem.querySelector('.element-symbol');
                if (symbol) {
                    symbol.textContent = expression.length > 12 ? `${expression.slice(0, 12)}…` : (expression || 'f(x)');
                }

                Elements.updateFormulaInputs(elemId, inputCount);
                Elements.updateElementSize(elemId);  // ← Добавляем это
            } else if (elemType === 'and' || elemType === 'or') {
                const inputCount = parseInt(document.getElementById('prop-input-count').value) || 2;
                elemData.props.inputCount = inputCount;
                
                Elements.updateLogicGateInputs(elemId, inputCount);
                Elements.updateElementSize(elemId);  // ← Добавляем это
                
                const symbol = elem.querySelector('.element-symbol');
                if (symbol) {
                    symbol.textContent = elemType === 'and' ? '∧' : '∨';
                }
            
        } else if (elemType === 'output') {
                const label = document.getElementById('prop-label').value || 'Выход';
                const outputGroup = document.getElementById('prop-output-group').value || '';

                elemData.props.label = label;
                elemData.props.outputGroup = outputGroup;

                const symbol = elem.querySelector('.element-symbol');
                if (symbol) symbol.textContent = label;
            }
            else if (elemType === 'group') {
                const title = document.getElementById('prop-title').value || 'Группа';
                elemData.props.title = title;
                const titleEl = elem.querySelector('.group-title');
                if (titleEl) titleEl.textContent = title;
                }

            this.hideModal('modal-overlay');
            
        } catch (error) {
            console.error('❌ Ошибка при сохранении свойств:', error);
            alert('Ошибка сохранения: ' + error.message);
        }
    },

    /**
     * Показать свойства проекта
     */
    showProjectPropertiesModal() {
        const content = document.getElementById('project-modal-content');
        const project = AppState.project;
        
        // Генерируем HTML для списка выходов только если модуль загружен
        let outputsHtml = '';
        if (typeof Outputs !== 'undefined' && AppState.outputs) {
            const logicalOutputsHtml = AppState.outputs.logical.length > 0
                ? AppState.outputs.logical.map(output => `
                    <div class="output-item" 
                        data-element-id="${output.elementId}"
                        onmouseenter="Outputs.highlightOutput('${output.elementId}', true)"
                        onmouseleave="Outputs.highlightOutput('${output.elementId}', false)"
                        onclick="Outputs.navigateToOutput('${output.elementId}'); Modal.hideModal('project-modal-overlay');">
                        <span class="output-icon">${output.portLabel === 'Да' ? '✅' : '❌'}</span>
                        <span class="output-name">${output.elementName}</span>
                        <span class="output-port">→ ${output.portLabel}</span>
                    </div>
                `).join('')
                : '<div class="no-outputs">Нет логических выходов</div>';
            
            const numericOutputsHtml = AppState.outputs.numeric.length > 0
                ? AppState.outputs.numeric.map(output => `
                    <div class="output-item numeric" 
                        data-element-id="${output.elementId}"
                        onmouseenter="Outputs.highlightOutput('${output.elementId}', true)"
                        onmouseleave="Outputs.highlightOutput('${output.elementId}', false)"
                        onclick="Outputs.navigateToOutput('${output.elementId}'); Modal.hideModal('project-modal-overlay');">
                        <span class="output-icon">🔢</span>
                        <span class="output-name">${output.elementName}</span>
                        <span class="output-port">→ значение</span>
                    </div>
                `).join('')
                : '<div class="no-outputs">Нет числовых выходов</div>';
            
            outputsHtml = `
                <div class="modal-row">
                    <label>Выходные сигналы схемы:</label>
                    <div class="outputs-container">
                        <div class="outputs-section">
                            <div class="outputs-section-title">
                                <span class="section-icon">🔀</span>
                                Логические выходы (${AppState.outputs.logical.length})
                            </div>
                            <div class="outputs-list">
                                ${logicalOutputsHtml}
                            </div>
                        </div>
                        <div class="outputs-section">
                            <div class="outputs-section-title">
                                <span class="section-icon">📐</span>
                                Числовые выходы (${AppState.outputs.numeric.length})
                            </div>
                            <div class="outputs-list">
                                ${numericOutputsHtml}
                            </div>
                        </div>
                    </div>
                    <div class="outputs-hint">
                        💡 Выходами автоматически становятся элементы, чьи выходные порты не подключены к другим элементам.
                        Кликните на выход, чтобы перейти к нему на схеме.
                    </div>
                </div>
            `;
        }
        
        content.innerHTML = `
            <div class="modal-row">
                <label>Код проекта:</label>
                <input type="text" id="project-code" value="${project.code || ''}" placeholder="Уникальный идентификатор">
            </div>
            
            <div class="modal-row">
                <label>Тип проекта:</label>
                <div class="project-type-selector">
                    <div class="project-type-btn ${project.type === PROJECT_TYPE.PARAMETER ? 'active' : ''}" data-type="${PROJECT_TYPE.PARAMETER}">
                        <div class="type-icon">📊</div>
                        <div class="type-name">Параметр</div>
                        <div class="type-desc">Вычисляемое значение</div>
                    </div>
                    <div class="project-type-btn ${project.type === PROJECT_TYPE.RULE ? 'active' : ''}" data-type="${PROJECT_TYPE.RULE}">
                        <div class="type-icon">📋</div>
                        <div class="type-name">Правило</div>
                        <div class="type-desc">Логическое условие</div>
                    </div>
                </div>
            </div>
            
            <div id="parameter-fields" class="conditional-fields ${project.type === PROJECT_TYPE.PARAMETER ? 'visible' : ''}">
                <div class="modal-row">
                    <label>Размерность:</label>
                    <input type="text" id="project-dimension" value="${project.dimension || ''}" placeholder="Например: м/с, кг, °C">
                </div>
            </div>
            
            <div id="rule-fields" class="conditional-fields ${project.type === PROJECT_TYPE.RULE ? 'visible' : ''}">
                <div class="modal-row">
                    <label>Возможная причина:</label>
                    <textarea id="project-possible-cause" placeholder="Описание возможной причины срабатывания правила">${project.possibleCause || ''}</textarea>
                </div>
                <div class="modal-row">
                    <label>Методические указания:</label>
                    <textarea id="project-guidelines" placeholder="Инструкции и рекомендации при срабатывании правила">${project.guidelines || ''}</textarea>
                </div>
            </div>
            
            ${outputsHtml}
        `;
        
        // Обработчики переключения типа
        content.querySelectorAll('.project-type-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                content.querySelectorAll('.project-type-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                
                const type = btn.dataset.type;
                document.getElementById('parameter-fields').classList.toggle('visible', type === PROJECT_TYPE.PARAMETER);
                document.getElementById('rule-fields').classList.toggle('visible', type === PROJECT_TYPE.RULE);
            });
        });
        
        this.showModal('project-modal-overlay');
    },

    /**
     * Сохранить свойства проекта
     */
    saveProjectProperties() {
        const activeTypeBtn = document.querySelector('.project-type-btn.active');
        const type = activeTypeBtn ? activeTypeBtn.dataset.type : PROJECT_TYPE.PARAMETER;

        AppState.project.code = document.getElementById('project-code').value;
        AppState.project.type = type;

        if (type === PROJECT_TYPE.PARAMETER) {
            AppState.project.dimension = document.getElementById('project-dimension').value;
            AppState.project.possibleCause = '';
            AppState.project.guidelines = '';
        } else {
            AppState.project.dimension = '';
            AppState.project.possibleCause = document.getElementById('project-possible-cause').value;
            AppState.project.guidelines = document.getElementById('project-guidelines').value;
        }

        this.hideModal('project-modal-overlay');
    }
};