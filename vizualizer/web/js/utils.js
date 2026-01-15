/**
 * Вспомогательные функции
 */

/**
 * Генерация уникального ID
 */
function generateId() {
    AppState.elementCounter++;
    return `elem_${AppState.elementCounter}`;
}

function getInputPortType(elementId, portIdentifier) {
    const element = AppState.elements[elementId];
    if (!element) return SIGNAL_TYPE.ANY;

    const config = ELEMENT_TYPES[element.type];
    if (!config) return SIGNAL_TYPE.ANY;

    let portIndex = portIdentifier;

    // Обработка технического порта условия
    if (typeof portIdentifier === 'string') {
        if (portIdentifier === 'cond-0' && config.hasConditionPort) {
            return config.conditionPortType || SIGNAL_TYPE.LOGIC;
        }

        if (portIdentifier.startsWith('in-')) {
            portIndex = parseInt(portIdentifier.split('-')[1], 10);
        }
    }

    if (Number.isNaN(portIndex) || portIndex === null || portIndex === undefined) {
        portIndex = 0;
    }

    // Динамические входы для AND/OR берут тип из конфига
    if ((element.type === 'and' || element.type === 'or')) {
        return SIGNAL_TYPE.LOGIC;  // Логические элементы всегда ожидают LOGIC на входе
    }

    if (element.type === 'formula') {
        return SIGNAL_TYPE.ANY;
    }

    const types = config.inputTypes || [];
    if (types.length === 0) return SIGNAL_TYPE.ANY;

    if (portIndex < types.length) {
        return types[portIndex] || SIGNAL_TYPE.ANY;
    }

    return types[types.length - 1] || SIGNAL_TYPE.ANY;
}

function getOutputPortType(elementId, portIdentifier) {
    const element = AppState.elements[elementId];
    if (!element) return SIGNAL_TYPE.ANY;

    const config = ELEMENT_TYPES[element.type];
    if (!config) return SIGNAL_TYPE.ANY;

    let portIndex = portIdentifier;

    if (typeof portIdentifier === 'string') {
        if (portIdentifier.startsWith('out-')) {
            portIndex = parseInt(portIdentifier.split('-')[1], 10);
        }
    }

    if (Number.isNaN(portIndex) || portIndex === null || portIndex === undefined) {
        portIndex = 0;
    }

    const types = config.outputTypes || [];
    if (types.length === 0) return SIGNAL_TYPE.ANY;

    if (portIndex < types.length) {
        return types[portIndex] || SIGNAL_TYPE.ANY;
    }

    return types[types.length - 1] || SIGNAL_TYPE.ANY;
}
/**
 * Проверка совместимости типов сигналов
 * 
 * Новая логика:
 * - ANY совместим со всем
 * - TRUE совместим с LOGIC, TRUE, ANY
 * - FALSE совместим с LOGIC, FALSE, ANY
 * - LOGIC совместим с LOGIC, TRUE, FALSE, ANY
 * - NUMERIC совместим с NUMERIC, ANY
 */
function areTypesCompatible(outputType, inputType) {
    // Если один из типов ANY - совместимы
    if (outputType === SIGNAL_TYPE.ANY || inputType === SIGNAL_TYPE.ANY) {
        return true;
    }

    // Если типы одинаковые - совместимы
    if (outputType === inputType) {
        return true;
    }

    // TRUE/FALSE совместимы с LOGIC
    if ((outputType === SIGNAL_TYPE.TRUE || outputType === SIGNAL_TYPE.FALSE) && 
        inputType === SIGNAL_TYPE.LOGIC) {
        return true;
    }

    // LOGIC совместим с TRUE/FALSE (в случае если ожидается конкретный тип)
    if (outputType === SIGNAL_TYPE.LOGIC && 
        (inputType === SIGNAL_TYPE.TRUE || inputType === SIGNAL_TYPE.FALSE)) {
        return true;
    }

    return false;
}

/**
 * Проверка, находится ли элемент внутри рамки
 */
function isInsideFrame(elemId, frameId) {
    const elem = AppState.elements[elemId];
    const frame = AppState.elements[frameId];

    if (!elem || !frame || frame.type !== 'output-frame') return false;

    const elemCenterX = elem.x + elem.width / 2;
    const elemCenterY = elem.y + elem.height / 2;

    return elemCenterX > frame.x &&
           elemCenterX < frame.x + frame.width &&
           elemCenterY > frame.y &&
           elemCenterY < frame.y + frame.height;
}

/**
 * Обновить принадлежность элементов к рамкам
 */
function updateFrameChildren() {
    // Сначала очистим children у рамок и parentFrame у всех элементов
    Object.values(AppState.elements).forEach(elem => {
        if (elem.type === 'output-frame') {
            elem.children = [];
        } else {
            // удаляем parentFrame по умолчанию (пересчитаем ниже)
            if (elem.parentFrame) delete elem.parentFrame;
        }
    });

    // Назначаем принадлежность: для каждого элемента ищем рамку, в которую он попадает
    Object.values(AppState.elements).forEach(elem => {
        if (!elem || elem.type === 'output-frame') return;

        Object.values(AppState.elements).forEach(frame => {
            if (!frame || frame.type !== 'output-frame') return;

            if (isInsideFrame(elem.id, frame.id)) {
                // добавляем в массив детей рамки
                frame.children.push(elem.id);
                // отмечаем у элемента родительскую рамку
                if (AppState.elements[elem.id]) {
                    AppState.elements[elem.id].parentFrame = frame.id;
                }
            }
        });
    });
}

/**
 * Преобразование координат экрана в координаты холста
 */
function screenToCanvas(screenX, screenY) {
    const container = document.getElementById('workspace-container');
    const rect = container.getBoundingClientRect();

    const x = (screenX - rect.left - AppState.viewport.panX) / AppState.viewport.zoom;
    const y = (screenY - rect.top - AppState.viewport.panY) / AppState.viewport.zoom;

    return { x, y };
}

/**
 * Преобразование координат холста в координаты экрана
 */
function canvasToScreen(canvasX, canvasY) {
    const container = document.getElementById('workspace-container');
    const rect = container.getBoundingClientRect();

    const x = canvasX * AppState.viewport.zoom + AppState.viewport.panX + rect.left;
    const y = canvasY * AppState.viewport.zoom + AppState.viewport.panY + rect.top;

    return { x, y };
}

/**
 * Проверка, является ли порт выходным (не подключен к другим элементам)
 */
function isOutputPort(elemId, portIndex) {
    const portKey = `out-${portIndex}`;
    
    // Проверяем, есть ли соединения от этого порта
    const hasConnection = AppState.connections.some(conn => 
        conn.fromElement === elemId && conn.fromPort === portKey
    );
    
    return !hasConnection;
}

/**
 * Получить информацию о выходном порте
 */
function getOutputPortInfo(elemId, portIndex) {
    const elem = AppState.elements[elemId];
    if (!elem) return null;
    
    const config = ELEMENT_TYPES[elem.type];
    if (!config) return null;
    
    return {
        elementId: elemId,
        elementType: elem.type,
        elementName: config.name,
        portIndex: portIndex,
        portLabel: config.outputLabels?.[portIndex] || `out${portIndex}`,
        portType: config.outputTypes?.[portIndex] || SIGNAL_TYPE.ANY,
        // Дополнительная информация для идентификации
        displayName: `${config.name} → ${config.outputLabels?.[portIndex] || `out${portIndex}`}`
    };
}

function splitArgsTopLevel(argStr) {
  const out = [];
  let cur = '';
  let depth = 0;
  for (let i = 0; i < argStr.length; i++) {
    const ch = argStr[i];
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === ',' && depth === 0) {
      out.push(cur.trim());
      cur = '';
    } else {
      cur += ch;
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function expandFormulaTemplates(expr, templatesMap) {
  if (!expr) return expr;
  if (!templatesMap) return expr;

  // несколько проходов на случай вложенных шаблонов
  for (let pass = 0; pass < 10; pass++) {
    let changed = false;

    expr = expr.replace(/([A-Za-z_]\w*)\s*\(([^()]|\([^()]*\))*\)/g, (match, name) => {
      const tpl = templatesMap[name];
      if (!tpl) return match;

      // вытащим аргументы вручную: name(....)
      const open = match.indexOf('(');
      const close = match.lastIndexOf(')');
      const inside = match.slice(open + 1, close);

      const args = splitArgsTopLevel(inside);
      const formal = tpl.args || [];
      let body = String(tpl.body || '0');

      // если количество аргументов не совпало — не трогаем (лучше так, чем сломать)
      if (args.length !== formal.length) return match;

      formal.forEach((f, i) => {
        const re = new RegExp(`\\b${f}\\b`, 'g');
        body = body.replace(re, `(${args[i]})`);
      });

      changed = true;
      return `(${body})`;
    });

    if (!changed) break;
  }

  return expr;
}