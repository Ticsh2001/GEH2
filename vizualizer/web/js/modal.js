/**
 * Модуль модальных окон
 * modal.js
 */

// modal.js

function getSignalNameForPort(elemId, portIndex) {
  // ищем соединение, ведущее в этот вход
  const portName = `in-${portIndex}`;
  const conn = AppState.connections.find(
    c => c.toElement === elemId && c.toPort === portName
  );
  if (!conn) return null;

  const src = AppState.elements[conn.fromElement];
  if (!src) return null;

  // разные типы элементов можно отображать по-разному
  switch (src.type) {
    case 'input-signal':
      return src.props?.name || src.id;
    case 'const':
      return String(src.props?.value ?? 0);
    case 'formula':
      return src.props?.expression || src.id;
    case 'table':
      return src.props?.name || src.id;
    case 'output':
      return src.props?.label || src.id;
    default:
      // логические и прочие — просто имя типа + id
      return ELEMENT_TYPES[src.type]?.name || src.id;
  }
}

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

        async showDependencyTree() {
        const code = AppState.project.code;
        if (!code) {
            alert('Сначала укажите код проекта (сохраните проект)');
            return;
        }
        const type = AppState.project.type || 'parameter';
        const filename = `${code}_${type}.json`;
        const config = AppState.currentConfig || '';

        const previewDiv = document.getElementById('dependency-preview');
        const showLoading = (msg) => {
            if (previewDiv) {
                previewDiv.style.display = 'block';
                previewDiv.textContent = msg;
            }
        };
        const hidePreview = () => {
            if (previewDiv) {
                previewDiv.style.display = 'none';
                previewDiv.textContent = '';
            }
        };

        showLoading('⏳ Сборка промпта и запрос к LLM...');

        try {
            // 1. Загружаем всё необходимое
            const treeUrl = `/api/project/dependency-tree?filename=${encodeURIComponent(filename)}&source=projects&config=${encodeURIComponent(config)}`;
            const [treeResp, structResp, syntaxResp] = await Promise.all([
                fetch(treeUrl),
                fetch('/api/llm/context-structure'),
                fetch('/api/llm/context-syntax')
            ]);

            if (!treeResp.ok) throw new Error('Ошибка загрузки дерева');
            const tree = await treeResp.json();

            let structureMd = '';
            let syntaxMd = '';
            if (structResp.ok) {
                const s = await structResp.json();
                structureMd = s.content || '';
            }
            if (syntaxResp.ok) {
                const s = await syntaxResp.json();
                syntaxMd = s.content || '';
            }

            const projectType = tree.type || 'parameter';
            const projectCode = tree.project || '';
            const desc = tree.description || '';
            const dim = tree.dimension || '';
            const cause = tree.possibleCause || '';
            const guide = tree.guidelines || '';

            // Определяем, есть ли усечённый код где-либо в дереве
            const hasTruncatedCode = (node) => {
                if (node.code_truncated) return true;
                if (node.inputs && node.inputs.length) {
                    return node.inputs.some(child => hasTruncatedCode(child));
                }
                if (node.dependencies && node.dependencies.length) {
                    return node.dependencies.some(child => hasTruncatedCode(child));
                }
                return false;
            };

            const truncated = hasTruncatedCode(tree);

            // 2. Строим системный промпт (по вашему шаблону)
            let systemPrompt = `You are an instrumentation and control (I&C) engineer at a thermal power plant with deep expertise in turbine and boiler equipment. Your task is to fill in the fields of ${projectType === 'rule' ? 'a protection/interlock rule project' : 'a calculated parameter project'} for a diagnostic system.

Below is a JSON dependency tree of the input signals this project depends on, including their descriptions and (for synthetic signals) their calculation code.`;

            if (structureMd) systemPrompt += `\n\n## JSON tree structure reference\n${structureMd}`;
            if (syntaxMd) systemPrompt += `\n\n## Code syntax reference for "code" fields\n${syntaxMd}`;

            systemPrompt += `\n\n## Analysis steps (perform internally before answering; do not show this reasoning in the output — only the final result)
1. Identify which signals in the tree are base signals (type: "base") and which are synthetic (type: "synthetic"); for synthetic signals, parse their code using the syntax reference.
2. For each significant signal, determine its physical meaning from its KKS code prefix: the system group (leading letters, see systems table) and the measurement/control type (letters following the digits, see measurement types table).
3. Trace the full chain: how the final parameter/rule value depends on base signals through intermediate synthetic calculations.
4. Pay attention to constructs like WHEN(condition, ...) and comparisons (>, <, >=, <=) — these are thresholds and triggering logic; reflect them concretely in the answer, not in vague terms.
5. If functions like HISTORYAVG/HISTORYGRADIENT/HISTORYMAX etc. are used, account for the physical meaning of the time window (period in minutes): this is an average, trend, or extremum over an interval, not an instantaneous value.`;

            if (truncated) {
                systemPrompt += `\n6. IMPORTANT: some signals in the tree have "code_truncated": true — their code was cut off due to length. Do not make assumptions about the truncated part; rely only on the visible portion.`;
            }

            systemPrompt += `\n\n## Output style requirements
- Professional engineering style typical of thermal power plant operational documentation.
- Maximum technical specificity: reference actual KKS signal codes from the tree, not just their verbal descriptions.
- If the code contains numeric thresholds (comparisons, constants), state the concrete values and their physical meaning.
- Do not use vague statements without concrete backing (e.g. "this parameter is important for process control" with no explanation of how).
- Do not invent information that is not present in the dependency tree or current descriptions. If some information is insufficient to fully explain something, state this explicitly rather than guessing.
- Do not use markdown formatting (no asterisks, no headers, no "-"/"*" bullet lists) — plain connected text only.
- CRITICAL: Regardless of the fact that this system prompt and the reference materials are in English, your entire response MUST be written in Russian. Do not output any English text in the final answer.

## JSON dependency tree
${JSON.stringify(tree, null, 2)}`;

            // 3. Пользовательский запрос
            let userPrompt = '';
            if (projectType === 'parameter') {
                userPrompt = `Project: "${projectCode}"${desc ? `, current description: "${desc}"` : ''}, dimension: "${dim || 'not specified'}".

Write a detailed technical description of this parameter.

The description must:
- Explain what physical quantity or process characteristic the parameter represents.
- Explicitly name which input signals (with their KKS codes) are used and how they are processed, based on the dependency tree and the parsed code.
- State concrete thresholds/conditions if present in the calculation code.
- Include the dimension (${dim || 'if applicable'}).

Return only the final description text, with no introductory phrases (e.g. do not start with "Here is the description:").

Write your entire answer in Russian.`;
            } else {
                userPrompt = `Rule project: "${projectCode}"${desc ? `, current description: "${desc}"` : ''}${cause ? `, current possible cause: "${cause}"` : ''}${guide ? `, current guidelines: "${guide}"` : ''}.

Based on the dependency tree and the parsed code, write three fields for this rule:

1. DESCRIPTION — what the rule controls, why it exists, and under what condition it triggers (state concrete thresholds from the code, if any).
2. POSSIBLE_CAUSE — the most likely technical reason for the rule to trigger, based on the physical meaning of the involved signals.
3. GUIDELINES — concrete actions for operations personnel when the rule fires. Also include a list of possible consequences if the operator fails to react to the triggered rule (what can happen to the equipment or process).

Return the answer strictly in the following format, each field on its own line, with no markdown formatting:
DESCRIPTION: <text>
POSSIBLE_CAUSE: <text>
GUIDELINES: <text>

Write all text values in Russian. Keep the field labels (DESCRIPTION, POSSIBLE_CAUSE, GUIDELINES) exactly as shown, in English.`;
            }

            const fullPrompt = systemPrompt + '\n\n' + userPrompt;

            // 4. Отправляем в Ollama через наш прокси (без указания model)
            const llmResp = await fetch('/api/llm/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: fullPrompt, options: { temperature: 0.3 }})
            });

            if (!llmResp.ok) throw new Error('Ошибка LLM: ' + (await llmResp.text()));
            const llmData = await llmResp.json();
            const llmText = llmData.response || '(пустой ответ)';

            // 5. Заполняем поля в модалке
            if (projectType === 'parameter') {
                const descField = document.getElementById('project-description');
                if (descField) {
                    const oldText = descField.value.trim();
                    const newText = `------------ПРЕДЛОЖЕНИЕ AI---------------\n${llmText.trim()}`;
                    descField.value = oldText ? `${newText}\n\n(было)\n${oldText}` : newText;
                }
            } else if (projectType === 'rule') {
                // Парсим ответ модели по ожидаемым меткам
                const descMatch = llmText.match(/DESCRIPTION:\s*([\s\S]*?)(?=POSSIBLE_CAUSE:|GUIDELINES:|$)/i);
                const causeMatch = llmText.match(/POSSIBLE_CAUSE:\s*([\s\S]*?)(?=GUIDELINES:|$)/i);
                const guideMatch = llmText.match(/GUIDELINES:\s*([\s\S]*?)$/i);

                const aiDesc = descMatch ? descMatch[1].trim() : llmText.trim();
                const aiCause = causeMatch ? causeMatch[1].trim() : '';
                const aiGuide = guideMatch ? guideMatch[1].trim() : '';

                const descField = document.getElementById('project-rule-description');
                const causeField = document.getElementById('project-possible-cause');
                const guideField = document.getElementById('project-guidelines');

                if (descField) {
                    const old = descField.value.trim();
                    const newText = `------------ПРЕДЛОЖЕНИЕ AI---------------\n${aiDesc}`;
                    descField.value = old ? `${newText}\n\n(было)\n${old}` : newText;
                }
                if (causeField) {
                    const old = causeField.value.trim();
                    const newText = `------------ПРЕДЛОЖЕНИЕ AI---------------\n${aiCause}`;
                    causeField.value = old ? `${newText}\n\n(было)\n${old}` : newText;
                }
                if (guideField) {
                    const old = guideField.value.trim();
                    const newText = `------------ПРЕДЛОЖЕНИЕ AI---------------\n${aiGuide}`;
                    guideField.value = old ? `${newText}\n\n(было)\n${old}` : newText;
                }
            }

            // Успешно – скрываем отладочный блок
            hidePreview();
        } catch (e) {
            showLoading('Ошибка: ' + e.message);
        }
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

         if (modalId === 'project-modal-overlay') {
            const preview = document.getElementById('dependency-preview');
            if (preview) {
                preview.style.display = 'none';
                preview.textContent = '';
            }
        }


        // Скрываем tooltip если он есть
        const tooltip = document.getElementById('template-tooltip');
        if (tooltip) {
            tooltip.classList.remove('visible');
        }
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

    // modal.js в блоке input-signal
    <div class="modal-row">
        <label>Размерность:</label>
        <input type="text" id="prop-dimension" value="${props.dimension || ''}" />
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
        } else if (elemType === 'table') {
            contentHTML = `
            <div class="modal-row">
                <label>Название таблицы:</label>
                <input type="text" id="prop-name" value="${props.name || ''}" placeholder="Например: MyTable или *Plan*" />
                <small style="color:#999;">
                Используйте * для маски (пример: *Plan*)
                </small>
                <div id="table-filter-results"
                    style="max-height:160px; overflow-y:auto; background:#0f3460; border-radius:5px; margin-top:6px; display:none;">
                </div>
            </div>
            <div class="modal-row">
                <label>Комментарий:</label>
                <textarea id="prop-comment" placeholder="Комментарий к элементу...">${props.comment || ''}</textarea>
            </div>
            `;
        }
         
        else if (elemType === 'group') {
            contentHTML = `
            <div class="modal-row">
                <label>Название группы:</label>
                <input type="text" id="prop-title" value="${props.title || 'Группа'}">
            </div>`;
        // modal.js -> showPropertiesModal

        } else if (elemType === 'range') {
        const min = props.minValue ?? 0;
        const max = props.maxValue ?? 1;
        const inclusiveMin = props.inclusiveMin !== false;
        const inclusiveMax = props.inclusiveMax !== false;

        contentHTML = `
            <div class="modal-row">
            <label>Минимальное значение:</label>
            <input type="number" id="prop-min" value="${min}" step="any">
            </div>
            <div class="modal-row">
            <label>Максимальное значение:</label>
            <input type="number" id="prop-max" value="${max}" step="any">
            </div>
            <div class="modal-row">
            <label>Тип границ:</label>
            <div style="display:flex; gap:10px; font-size:13px;">
                <label style="display:flex; align-items:center; gap:4px;">
                <input type="checkbox" id="prop-inc-min" ${inclusiveMin ? 'checked' : ''}>
                Включать минимальное значение ( [ )
                </label>
            </div>
            <div style="display:flex; gap:10px; font-size:13px; margin-top:4px;">
                <label style="display:flex; align-items:center; gap:4px;">
                <input type="checkbox" id="prop-inc-max" ${inclusiveMax ? 'checked' : ''}>
                Включать максимальное значение ( ] )
                </label>
            </div>
            </div>
        `;
        }
        else if (elemType === 'switch') {
            const totalInputs = props.inputCount ?? 3;
            const caseCount = Math.max(0, totalInputs - 2);

            // Список доступных портов для кейсов: in-2..in-(totalInputs-1)
            // Отображаем как "in-2 (case 1)", "in-3 (case 2)" и т.д.
            const availableCasePorts = [];
                for (let i = 2; i < totalInputs; i++) {
                const signalName = getSignalNameForPort(elemId, i);
                const niceName = signalName || `in-${i}`;
                availableCasePorts.push({
                    index: i,
                    signalName: signalName,
                    label: niceName,
                    portLabel: `in-${i}`
                });
                }

            // Нормализуем cases из props
            let propsCases = Array.isArray(props.cases) ? props.cases : [];
            const usedInputIndexes = new Set();
            propsCases.forEach(c => {
            if (Number.isInteger(c.inputIndex) && c.inputIndex >= 2 && c.inputIndex < totalInputs) {
                usedInputIndexes.add(c.inputIndex);
            }
            });
            // обрежем/расширим до caseCount
            if (propsCases.length < caseCount) {
                propsCases = propsCases.concat(
                Array.from({ length: caseCount - propsCases.length }, () => ({
                    op: '=',
                    value: '',
                    inputIndex: null
                }))
                );
            } else if (propsCases.length > caseCount) {
                propsCases = propsCases.slice(0, caseCount);
            }

            // HTML кейсов
            let casesHTML = '';
            propsCases.forEach((c, idx) => {
                const op = c.op || '=';
                const value = (c.value !== undefined) ? c.value : '';
                const inputIndex = Number.isInteger(c.inputIndex) ? c.inputIndex : null;

                // Собираем options для селекта
                let optionsHTML = '<option value="">(не выбрано)</option>';

                // Для текущего кейса считаем, что его выбор "не занят",
                // чтобы он мог видеть свой текущий порт без звёздочки.
                availableCasePorts.forEach(port => {
                const selected = (inputIndex === port.index) ? 'selected' : '';

                const alreadyUsed =
                    usedInputIndexes.has(port.index) && port.index !== inputIndex;

                let baseLabel;
                if (port.signalName) {
                    baseLabel = port.signalName;
                } else {
                    baseLabel = port.portLabel;
                }

                const display = alreadyUsed ? `${baseLabel}*` : baseLabel;

                optionsHTML += `<option value="${port.index}" ${selected}>${display}</option>`;
                });

                const caseIndex = idx + 1;

                casesHTML += `
                <div class="modal-row" style="border:1px solid #1f2937; padding:8px; border-radius:6px; margin-bottom:6px;">
                    <div style="font-size:11px; color:#9ca3af; margin-bottom:4px;">
                    Кейc #${caseIndex}
                    </div>
                    <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center;">
                    <span style="font-size:12px;color:#9ca3af;">Условие для A:</span>
                    <select id="switch-op-${idx}" style="width:80px;">
                        <option value="="  ${op === '='  ? 'selected' : ''}>=</option>
                        <option value=">"  ${op === '>'  ? 'selected' : ''}>&gt;</option>
                        <option value="<"  ${op === '<'  ? 'selected' : ''}>&lt;</option>
                        <option value=">=" ${op === '>=' ? 'selected' : ''}>&gt;=</option>
                        <option value="<=" ${op === '<=' ? 'selected' : ''}>&lt;=</option>
                        <option value="!=" ${op === '!=' ? 'selected' : ''}>!=</option>
                    </select>
                    <input type="text" id="switch-val-${idx}" value="${value}"
                            placeholder="значение для A"
                            style="flex:1; min-width:120px;">
                    </div>
                    <div style="display:flex; gap:8px; align-items:center; margin-top:6px;">
                    <span style="font-size:12px;color:#9ca3af;">Сигнал для этого кейса:</span>
                    <select id="switch-input-${idx}" style="flex:1; min-width:160px;">
                        ${optionsHTML}
                    </select>
                    </div>
                </div>
                `;
            });

            contentHTML = `
                <div class="modal-row">
                <label>Количество входов (вместе с A и default):</label>
                <input type="number" id="prop-input-count"
                        value="${totalInputs}" min="2" max="10">
                <small style="color:#999;">
                    in-0 — A (сравниваемый сигнал), in-1 — default, in-2.. — case входы.
                </small>
                </div>

                <div class="modal-row">
                <label>Кейсы (условия для A):</label>
                <div id="switch-cases-container">
                    ${
                    caseCount > 0
                        ? casesHTML
                        : '<div style="color:#888;font-size:12px;">Нет кейсов — switch вернёт default.</div>'
                    }
                </div>
                <small style="color:#999;">
                    Порядок строк = порядок проверки: сверху вниз генерируются вложенные WHEN.
                </small>
                </div>
            `;
            } else if (elemType === 'multi-if') {
                const totalInputs = props.inputCount || 2;
                const op = props.operator || '>';
                const logic = props.logic || 'AND';
                contentHTML = `
                    <div class="modal-row">
                        <label>Количество входов (включая эталон):</label>
                        <input type="number" id="prop-input-count" value="${totalInputs}" min="2" max="20">
                    </div>
                    <div class="modal-row">
                        <label>Оператор сравнения:</label>
                        <select id="prop-operator">
                            <option value=">" ${op === '>' ? 'selected' : ''}>> (больше)</option>
                            <option value="<" ${op === '<' ? 'selected' : ''}>< (меньше)</option>
                            <option value=">=" ${op === '>=' ? 'selected' : ''}>= (больше или равно)</option>
                            <option value="<=" ${op === '<=' ? 'selected' : ''}>= (меньше или равно)</option>
                            <option value="=" ${op === '=' ? 'selected' : ''}>= (равно)</option>
                            <option value="!=" ${op === '!=' ? 'selected' : ''}>!= (не равно)</option>
                        </select>
                    </div>
                    <div class="modal-row">
                        <label>Логическая операция:</label>
                        <select id="prop-logic">
                            <option value="AND" ${logic === 'AND' ? 'selected' : ''}>И (AND)</option>
                            <option value="OR" ${logic === 'OR' ? 'selected' : ''}>ИЛИ (OR)</option>
                        </select>
                    </div>`;
                    } else if (elemType === 'signal-const') {
                        contentHTML = `
                            <div class="modal-row">
                                <label>Значение:</label>
                                <input type="number" id="prop-value" value="${props.value ?? 0}" step="any">
                            </div>
                            <div class="modal-row">
                                <label>Описание:</label>
                                <textarea id="prop-description">${props.description || ''}</textarea>
                            </div>
                            <div class="modal-row">
                                <label>Размерность:</label>
                                <input type="text" id="prop-dimension" value="${props.dimension || ''}" />
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

            // ... (где-то выше код сбора signalsHTML) ...

            contentHTML = `
                <div class="modal-row">
                    <label>Количество входов:</label>
                    <input type="number" id="prop-input-count" value="${props.inputCount || 2}" min="1" max="10">
                </div>

                <!-- Верхний блок: Две колонки (Сигналы и Шаблоны) -->
                <div style="display: flex; gap: 15px; margin-bottom: 15px; height: 140px;">
                    <!-- Левая колонка: Сигналы -->
                    <div style="flex: 1; display: flex; flex-direction: column;">
                        <label style="margin-bottom: 5px; display:block;">Входные сигналы:</label>
                        <div class="signal-list" id="signal-list" style="flex: 1; overflow-y: auto; background: #0f3460; padding: 5px; border-radius: 4px; border: 1px solid #4a90d9;">
                            ${signalsHTML || '<div style="color:#888;padding:5px;">Нет сигналов</div>'}
                        </div>
                    </div>

                    <!-- Правая колонка: Шаблоны -->
                    <div style="flex: 1; display: flex; flex-direction: column;">
                        <label style="margin-bottom: 5px; display:block;">Шаблоны:</label>
                        <div class="signal-list" id="template-list" style="flex: 1; overflow-y: auto; background: #0f3460; padding: 5px; border-radius: 4px; border: 1px solid #4a90d9;">
                            <div style="color:#888;padding:5px;">Загрузка…</div>
                        </div>
                    </div>
                </div>

                <!-- Нижний блок: Поле формулы (во всю ширину) -->
                <div class="modal-row">
                    <label>Выражение формулы:</label>
                    <textarea id="prop-expression" 
                            style="width: 100%; min-height: 80px; font-family: monospace; font-size: 14px; line-height: 1.4;"
                            spellcheck="false">${props.expression || ''}</textarea>
                    <small style="color:#999; display:block; margin-top:4px;">
                        Двойной клик по сигналу или шаблону вставит его в позицию курсора (или заменит выделенный текст).
                    </small>
                </div>
            `;
        }
        if (!contentHTML) {
            contentHTML = `<div style="color:#aaa; font-size:12px;">Нет специальных свойств.</div>`;
            }
        contentHTML += `
            <div class="modal-row">
                <label>Комментарий:</label>
                <textarea id="prop-comment" placeholder="Комментарий к элементу...">${props.comment || ''}</textarea>
            </div>
            `;
        

        modalContent.innerHTML = contentHTML;

      

        // modal.js — внутри showPropertiesModal, блок if (elemType === 'formula')
        if (elemType === 'formula') {
            const listEl = document.getElementById('template-list');
            
            // Создаём tooltip элемент (один на всю страницу)
            let tooltip = document.getElementById('template-tooltip');
            if (!tooltip) {
                tooltip = document.createElement('div');
                tooltip.id = 'template-tooltip';
                tooltip.className = 'template-tooltip';
                document.body.appendChild(tooltip);
            }

            (async () => {
                try {
                    const data = await Settings.fetchFormulaTemplates();
                    const items = data.templates || [];
                    
                    if (!items.length) {
                        listEl.innerHTML = '<div style="color:#888;padding:5px;">Нет шаблонов</div>';
                        return;
                    }
                    
                    // Новый код
                    listEl.innerHTML = items.map(t => {
                        // --- НАЧАЛО ИЗМЕНЕНИЙ ---
                        let argList = [];
                        
                        if (Array.isArray(t.args)) {
                            // Если пришел старый формат (массив): ["p", "t"]
                            argList = t.args;
                        } else if (t.args && typeof t.args === 'object') {
                            // Если пришел новый формат (объект): {"p": {...}, "t": {...}}
                            // Берем только ключи (имена переменных)
                            argList = Object.keys(t.args);
                        }

                        // Формируем подпись функции: h(p, t)
                        const sig = `${t.name}(${argList.join(', ')})`;
                        // --- КОНЕЦ ИЗМЕНЕНИЙ ---

                        const desc = (t.description || '').replace(/"/g, '&quot;');
                        return `<div class="signal-item template-item" 
                                data-insert="${sig}" 
                                data-name="${t.name}"
                                data-description="${desc}">${sig}</div>`;
                    }).join('');

                    // Обработчики для каждого шаблона
                    listEl.querySelectorAll('.template-item').forEach(div => {
                        // Двойной клик — вставка
                        div.addEventListener('dblclick', () => {
                            const insert = div.dataset.insert;
                            const textarea = document.getElementById('prop-expression');
                            insertAtCursor(textarea, insert);
                        });

                        // Наведение — показать tooltip
                        div.addEventListener('mouseenter', (e) => {
                            const description = div.dataset.description;
                            const name = div.dataset.name;
                            
                            if (!description) return;
                            
                            tooltip.innerHTML = `
                                <div class="template-tooltip-title">${name}</div>
                                <div>${description}</div>
                            `;
                            
                            // Позиционируем tooltip
                            const rect = div.getBoundingClientRect();
                            tooltip.style.left = rect.left + 'px';
                            tooltip.style.top = (rect.bottom + 8) + 'px';
                            tooltip.classList.add('visible');
                        });

                        // Уход мыши — скрыть tooltip
                        div.addEventListener('mouseleave', () => {
                            tooltip.classList.remove('visible');
                        });
                    });
                    
                } catch (e) {
                    console.error(e);
                    listEl.innerHTML = '<div style="color:#888;padding:5px;">Ошибка загрузки</div>';
                }
            })();
        }



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
                    const dimField = document.getElementById('prop-dimension');
                    if (dimField) dimField.value = chosen.EngineeringUnit || chosen.Dimension || '';
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

                if (elemType === 'table') {
            const input = document.getElementById('prop-name');
            const results = document.getElementById('table-filter-results');
            const commentField = document.getElementById('prop-comment');
            let timer = null;

            const renderList = (items) => {
                if (!items || !items.length) {
                    results.innerHTML = '<div style="color:#666;padding:6px;">Нет совпадений</div>';
                    results.style.display = 'block';
                    return;
                }
                results.innerHTML = items.map(t => `
                    <div class="signal-result-item"
                        style="padding:6px 8px; cursor:pointer; border-bottom:1px solid rgba(255,255,255,0.08);">
                        <div style="font-weight:600;">${t.Name}</div>
                        <div style="color:#aaa; font-size:11px;">${t.Description || ''}</div>
                    </div>
                `).join('');
                results.style.display = 'block';

                results.querySelectorAll('.signal-result-item').forEach((div, i) => {
                    div.addEventListener('click', () => {
                        const chosen = items[i];
                        input.value = chosen.Name;
                        if (commentField) commentField.value = chosen.Description || '';
                        results.style.display = 'none';
                    });
                });
            };

            const search = async () => {
                const mask = (input.value || '').trim();
                if (!mask.includes('*')) {
                    results.style.display = 'none';
                    return;
                }
                results.innerHTML = '<div style="color:#666;padding:6px;">Поиск...</div>';
                results.style.display = 'block';
                try {
                    const data = await Settings.fetchTables(mask, 50);
                    renderList(data.items || []);
                } catch (e) {
                    results.innerHTML = '<div style="color:#666;padding:6px;">Ошибка загрузки таблиц</div>';
                    results.style.display = 'block';
                    console.error(e);
                }
            };

            input.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(search, 200);
            });

            document.addEventListener('mousedown', (e) => {
                if (!results.contains(e.target) && e.target !== input) {
                    results.style.display = 'none';
                }
            }, { once: true });
        }




        modalOverlay.dataset.elementId = elemId;
        this.showModal('modal-overlay');

        // Функция для умной вставки текста в позицию курсора
        const insertAtCursor = (field, text) => {
            if (!field) return;
            
            // Получаем позиции выделения
            const startPos = field.selectionStart;
            const endPos = field.selectionEnd;
            const currentValue = field.value;

            // Вставляем текст: (текст до) + (новый текст) + (текст после)
            field.value = currentValue.substring(0, startPos) + 
                        text + 
                        currentValue.substring(endPos, currentValue.length);

            // Возвращаем фокус и ставим курсор сразу после вставленного текста
            field.focus();
            const newCursorPos = startPos + text.length;
            field.setSelectionRange(newCursorPos, newCursorPos);
        };

        // Обработчик вставки сигналов для формулы
        if (elemType === 'formula') {
            document.querySelectorAll('.signal-item').forEach(item => {
            item.addEventListener('dblclick', () => {
                const signal = item.dataset.signal;
                const textarea = document.getElementById('prop-expression');
                
                // БЫЛО: textarea.value += signal;
                // СТАЛО:
                insertAtCursor(textarea, signal);
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
            const elem = document.getElementById(elemId);
            if (!elemData) {
                alert('⚠ Элемент не найден — возможно, он был удалён или переименован.');
                console.warn(`saveElementProperties: элемент ${elemId} не найден.`);
                this.hideModal('modal-overlay');
                return;
            }

            const elemType = elemData.type;

            if (elemType === 'input-signal') {
                const name = document.getElementById('prop-name').value || 'Сигнал';
                const description = document.getElementById('prop-description').value || '';
                const signalType = document.getElementById('prop-signal-type').value;
                const dimension = document.getElementById('prop-dimension').value || '';
                elemData.props.dimension = dimension;

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
            } else if (elemType === 'range') {
                const minVal = parseFloat(document.getElementById('prop-min').value);
                const maxVal = parseFloat(document.getElementById('prop-max').value);
                const incMin = document.getElementById('prop-inc-min').checked;
                const incMax = document.getElementById('prop-inc-max').checked;

                // можно слегка нормализовать: если min > max — поменять местами
                let min = isNaN(minVal) ? 0 : minVal;
                let max = isNaN(maxVal) ? min : maxVal;
                if (min > max) {
                    const t = min;
                    min = max;
                    max = t;
                }

                elemData.props.minValue = min;
                elemData.props.maxValue = max;
                elemData.props.inclusiveMin = incMin;
                elemData.props.inclusiveMax = incMax;

                // обновляем подпись на элементе
                const symbol = elem.querySelector('.element-symbol');
                if (symbol) {
                    const minBracket = incMin ? '[' : '(';
                    const maxBracket = incMax ? ']' : ')';
                    symbol.textContent = `${minBracket}${min}; ${max}${maxBracket}`;
                }
            } else if (elemType === 'switch') {
                const elem = document.getElementById(elemId);

                // 1) inputCount
                let inputCount = parseInt(document.getElementById('prop-input-count').value, 10);
                if (!Number.isFinite(inputCount) || inputCount < 2) inputCount = 2;
                if (inputCount > 10) inputCount = 10;
                elemData.props.inputCount = inputCount;

                const caseCount = Math.max(0, inputCount - 2);

                // 2) читаем кейсы
                const cases = [];
                for (let i = 0; i < caseCount; i++) {
                    const opEl = document.getElementById(`switch-op-${i}`);
                    const valEl = document.getElementById(`switch-val-${i}`);
                    const inEl = document.getElementById(`switch-input-${i}`);

                    const op = opEl ? (opEl.value || '=') : '=';
                    const value = valEl ? (valEl.value || '') : '';
                    let inputIndex = null;

                    if (inEl && inEl.value !== '') {
                    const idx = parseInt(inEl.value, 10);
                    if (Number.isFinite(idx) && idx >= 2 && idx < inputCount) {
                        inputIndex = idx;
                    }
                    }

                    cases.push({ op, value, inputIndex });
                }

                elemData.props.cases = cases;

                // 3) Обновляем входы и размер (динамические порты)
                Elements.updateSwitchInputs(elemId, inputCount);
                Elements.updateElementSize(elemId);

                // 4) Обновляем подпись
                const symbol = elem.querySelector('.element-symbol');
                if (symbol) {
                    const cnt = Math.max(0, inputCount - 2);
                    symbol.textContent = `A → ${cnt} кейс(ов), default`;
                }

            } else if (elemType === 'multi-if') {
                const inputCount = parseInt(document.getElementById('prop-input-count').value) || 2;
                const operator = document.getElementById('prop-operator').value;
                const logic = document.getElementById('prop-logic').value;
                elemData.props.inputCount = inputCount;
                elemData.props.operator = operator;
                elemData.props.logic = logic;
                Elements.updateMultiIfInputs(elemId, inputCount);
                Elements.updateElementSize(elemId);
                const symbol = elem.querySelector('.element-symbol');
                if (symbol) symbol.textContent = `${operator} ${logic}`;

            } else if (elemType === 'signal-const') {
                const value = parseFloat(document.getElementById('prop-value').value) || 0;
                const desc = document.getElementById('prop-description')?.value || '';
                const dimension = document.getElementById('prop-dimension')?.value || '';
                elemData.props.value = value;
                elemData.props.description = desc;
                elemData.props.dimension = dimension;
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
            } else if (elemType === 'table') {
                const name = document.getElementById('prop-name').value || 'Таблица';
                elemData.props.name = name;

                const { html } = Elements.createElementHTML(
                    elemType, elemId, elemData.x, elemData.y, elemData.props, elemData.width, elemData.height
                );
                elem.outerHTML = html;
                Elements.setupElementHandlers(elemId);
                Connections.drawConnections();
            }
            else if (elemType === 'group') {
                const title = document.getElementById('prop-title').value || 'Группа';
                elemData.props.title = title;
                const titleEl = elem.querySelector('.group-title');
                if (titleEl) titleEl.textContent = title;
                }
            const commentEl = document.getElementById('prop-comment');
            if (commentEl) elemData.props.comment = commentEl.value || '';

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
        if (project.type === PROJECT_TYPE.NEURAL_TEMPLATE || project.type === PROJECT_TYPE.NEURAL_NETWORK) {
            const modeText = project.type === PROJECT_TYPE.NEURAL_TEMPLATE ? 'Дизайн' : 'Обучение';
            content.innerHTML = `
                <div class="modal-row">
                    <label>Режим:</label>
                    <span>${modeText}</span>
                </div>
                <div class="modal-row">
                    <label>Код проекта:</label>
                    <input type="text" id="project-code" value="${project.code || ''}" placeholder="Уникальный идентификатор">
                </div>
                <div class="modal-row">
                    <label>Описание:</label>
                    <textarea id="project-description" placeholder="Описание нейросетевой модели">${project.description || ''}</textarea>
                </div>
            `;
            this.showModal('project-modal-overlay');
            return;
        }
        
        content.innerHTML = `
            <div class="modal-row">
                <label>Код проекта:</label>
                <input type="text" id="project-code" value="${project.code || ''}" placeholder="Уникальный идентификатор">
            </div>


            <div class="modal-row">
                <label class="checkbox-inline">
                    <input type="checkbox" id="project-is-draft" ${project.status !== 'ready' ? 'checked' : ''}>
                    <span>Черновик</span>
                </label>
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
                    <div class="project-type-btn ${project.type === PROJECT_TYPE.TEMPLATE ? 'active' : ''}" data-type="${PROJECT_TYPE.TEMPLATE}">
                        <div class="type-icon">🧩</div>
                        <div class="type-name">Шаблон</div>
                        <div class="type-desc">Формула для повторного использования</div>
                    </div>
                </div>
            </div>


            
            <div id="parameter-fields" class="conditional-fields ${project.type === PROJECT_TYPE.PARAMETER ? 'visible' : ''}">
                <div class="modal-row">
                    <label>Описание:</label>
                    <textarea id="project-description" placeholder="Описание сигнала">${project.description || ''}</textarea>
                </div>
                <div class="modal-row">
                    <label>Размерность:</label>
                    <input type="text" id="project-dimension" value="${project.dimension || ''}" placeholder="Например: м/с, кг, °C">
                </div>
            </div>
            
            <div id="rule-fields" class="conditional-fields ${project.type === PROJECT_TYPE.RULE ? 'visible' : ''}">
                 <div class="modal-row">
                    <label>Описание:</label>
                    <textarea id="project-rule-description" placeholder="Описание правила">${project.description || ''}</textarea>
                </div>
                <div class="modal-row">
                    <label>Возможная причина:</label>
                    <textarea id="project-possible-cause" placeholder="Описание возможной причины срабатывания правила">${project.possibleCause || ''}</textarea>
                </div>
                <div class="modal-row">
                    <label>Методические указания:</label>
                    <textarea id="project-guidelines" placeholder="Инструкции и рекомендации при срабатывании правила">${project.guidelines || ''}</textarea>
                </div>
            </div>

            <div id="template-fields" class="conditional-fields ${project.type === PROJECT_TYPE.TEMPLATE ? 'visible' : ''}">
                <div class="modal-row">
                    <label>Описание шаблона:</label>
                    <textarea id="project-template-description"
                            placeholder="Общее описание и контекст применения шаблона">${project.type === PROJECT_TYPE.TEMPLATE ? (project.description || '') : ''}</textarea>
                </div>

                <div class="modal-row">
                    <label>Параметры шаблона:</label>
                    <div id="template-args-container" class="template-args-list"></div>
                    <small style="color:#999;">
                    Заполни описание каждого входного сигнала — оно попадёт в шаблон формулы.
                    </small>
                </div>
            </div>
            
            <div class="modal-row" style="margin-top:12px; padding-top:8px; border-top:1px solid #333;">
                <small style="color:#888;">
                    ${AppState.project.author 
                        ? `Автор: <b>${AppState.project.author}</b>` 
                        : 'Автор: ещё не задан (определится при сохранении)'}
                    ${AppState.project.lastModifiedBy 
                        ? `&nbsp;|&nbsp; Последнее изменение: <b>${AppState.project.lastModifiedBy}</b> (${AppState.project.lastModifiedAt ? new Date(AppState.project.lastModifiedAt).toLocaleString() : '—'})` 
                        : ''}
                </small>
            </div>


            
            ${outputsHtml}
             <div class="modal-buttons" style="margin-top:10px; justify-content:flex-start;">
                <button id="dependency-tree-btn" class="modal-btn" style="background:#542881; color:#000;">
                    ✨ Предложение AI
                </button>
            </div>
        `;

        const depTreeBtn = content.querySelector('#dependency-tree-btn');
        if (depTreeBtn) {
            depTreeBtn.addEventListener('click', () => this.showDependencyTree());
        }

        // Блок для отображения JSON (как и было раньше)
        let previewDiv = document.getElementById('dependency-preview');
        if (!previewDiv) {
            previewDiv = document.createElement('div');
            previewDiv.id = 'dependency-preview';
            previewDiv.style.marginTop = '10px';
            previewDiv.style.maxHeight = '300px';
            previewDiv.style.overflowY = 'auto';
            previewDiv.style.background = '#0a0a1a';
            previewDiv.style.padding = '10px';
            previewDiv.style.borderRadius = '5px';
            previewDiv.style.display = 'none';
            content.appendChild(previewDiv);
        }




        const templateArgsContainer = content.querySelector('#template-args-container');
        if (templateArgsContainer) {
        const inputs = [...new Set(
            Object.values(AppState.elements)
            .filter(el => el?.type === 'input-signal')
            .map(el => el.props?.name?.trim() || el.id)
            .filter(Boolean)
        )];

        if (!inputs.length) {
            templateArgsContainer.innerHTML = '<div style="color:#888;">В проекте пока нет входных сигналов.</div>';
        } else {
            const storedArgs = AppState.project.templateArgs || {};
            templateArgsContainer.innerHTML = inputs.map(name => `
            <div class="template-arg-row" data-template-arg="${name}">
                <span class="template-arg-label">${name}</span>
                <textarea class="template-arg-description" placeholder="Описание для ${name}">${storedArgs[name] || ''}</textarea>
            </div>
            `).join('');
        }
        }
        
        // Обработчики переключения типа
        content.querySelectorAll('.project-type-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                content.querySelectorAll('.project-type-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                
                const type = btn.dataset.type;
                document.getElementById('parameter-fields').classList.toggle('visible', type === PROJECT_TYPE.PARAMETER);
                document.getElementById('rule-fields').classList.toggle('visible', type === PROJECT_TYPE.RULE);
                document.getElementById('template-fields').classList.toggle('visible', type === PROJECT_TYPE.TEMPLATE);
            });
        });
        
        this.showModal('project-modal-overlay');
    },

    /**
     * Сохранить свойства проекта
     */
    saveProjectProperties() {
        let type;
            // Если проект нейросетевой — тип уже известен из AppState
            if (AppState.project.type === PROJECT_TYPE.NEURAL_TEMPLATE ||
                AppState.project.type === PROJECT_TYPE.NEURAL_NETWORK) {
                type = AppState.project.type;
            } else {
                const activeTypeBtn = document.querySelector('.project-type-btn.active');
                type = activeTypeBtn ? activeTypeBtn.dataset.type : PROJECT_TYPE.PARAMETER;
            }

            const isDraft = document.getElementById('project-is-draft')?.checked ?? true;
            AppState.project.status = isDraft ? 'draft' : 'ready';
            AppState.project.code = document.getElementById('project-code').value;
            AppState.project.type = type;

        

        if (type === PROJECT_TYPE.PARAMETER) {
            AppState.project.dimension = document.getElementById('project-dimension').value;
            AppState.project.description = document.getElementById('project-description').value || '';            
            AppState.project.possibleCause = '';
            AppState.project.guidelines = '';
        } else if (type === PROJECT_TYPE.RULE) {
            AppState.project.dimension = '';
            AppState.project.description = document.getElementById('project-rule-description')?.value || '';
            AppState.project.possibleCause = document.getElementById('project-possible-cause').value;
            AppState.project.guidelines = document.getElementById('project-guidelines').value;
        } else if (type === PROJECT_TYPE.TEMPLATE) {
            AppState.project.description = document.getElementById('project-template-description').value || '';
            AppState.project.dimension = '';
            AppState.project.possibleCause = '';
            AppState.project.guidelines = '';

            const argDescriptions = {};
            document.querySelectorAll('[data-template-arg]').forEach(row => {
            const argName = row.dataset.templateArg;
            const textarea = row.querySelector('.template-arg-description');
            argDescriptions[argName] = textarea?.value?.trim() || '';
            });
            AppState.project.templateArgs = argDescriptions;
         } else if (type === PROJECT_TYPE.NEURAL_TEMPLATE || type === PROJECT_TYPE.NEURAL_NETWORK) {
            const descEl = document.getElementById('project-description');
            AppState.project.description = descEl ? descEl.value : '';
            AppState.project.dimension = '';
            AppState.project.possibleCause = '';
            AppState.project.guidelines = '';
            AppState.project.templateArgs = {};
        }

        this.hideModal('project-modal-overlay');
    }
};