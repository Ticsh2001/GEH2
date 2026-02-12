/**
 * Конфигурация приложения
 * config.js
 */

// Типы сигналов
const SIGNAL_TYPE = {
    NUMERIC: 'numeric',   // Числовой сигнал
    LOGIC: 'logic',       // Логический (может быть TRUE или FALSE)
    TRUE: 'true',         // Явно ИСТИНА
    FALSE: 'false',       // Явно ЛОЖЬ
    ANY: 'any'            // Любой тип
};

// Типы проекта
const PROJECT_TYPE = {
    PARAMETER: 'parameter',
    RULE: 'rule',
    TEMPLATE: 'template'
};

// Конфигурация элементов
const ELEMENT_TYPES = {
    'input-signal': {
        name: 'Вход',
        inputs: 0,
        outputs: 1,
        outputLabels: ['out'],
        outputTypes: [SIGNAL_TYPE.NUMERIC],
        color: '#4a90d9',
        hasProperties: true,
        defaultProps: { name: 'Сигнал', signalType: SIGNAL_TYPE.NUMERIC },
        resizable: true,
        minWidth: 150,
        minHeight: 50
    },
    'switch': {
        name: 'Switch',
        inputs: 3,                     // особый + default + первый case
        outputs: 1,
        // ЛОГИКА ВХОДОВ:
        // in-0: особый (A)
        // in-1: default (B)
        // in-2..in-N: case входы (C, D, ...)
        inputLabels: ['A', 'default', 'case'],
        inputTypes: [SIGNAL_TYPE.ANY, SIGNAL_TYPE.ANY, SIGNAL_TYPE.ANY],
        outputLabels: ['результат'],
        outputTypes: [SIGNAL_TYPE.ANY],
        color: '#ec4899',              // розовый, чтобы отличался
        hasProperties: true,
        resizable: true,
        minWidth: 160,
        minHeight: 90,
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC,
        defaultProps: {
        inputCount: 3,               // всего входов
        cases: [
            // массив объектов вида:
            // { op: '=', value: '0' }
        ]
        }
    },
    
    'range': {
        name: 'Диапазон',
        inputs: 1,
        outputs: 1,
        inputLabels: ['сигнал'],
        inputTypes: [SIGNAL_TYPE.ANY],      // любой числовой/логический (по сути ожидаем NUMERIC)
        outputLabels: ['в диапазоне'],
        outputTypes: [SIGNAL_TYPE.LOGIC],   // логический выход
        color: '#f97316',                   // можешь выбрать любой
        hasProperties: true,
        resizable: true,
        minWidth: 140,
        minHeight: 70,
        hasConditionPort: true,             // как у IF / formula
        conditionPortType: SIGNAL_TYPE.LOGIC,
        defaultProps: {
        minValue: 0,
        maxValue: 1,
        inclusiveMin: true,               // [min
        inclusiveMax: true,               // max]
        inputCount: 1                     // чтоб не ломало общую логику
        }
    },
    'and': {
        name: 'И',
        inputs: 2,  // По умолчанию 2, но может быть изменено
        outputs: 1,
        inputLabels: ['A', 'B'],
        inputTypes: [SIGNAL_TYPE.LOGIC, SIGNAL_TYPE.LOGIC],
        outputLabels: ['результат'],
        outputTypes: [SIGNAL_TYPE.LOGIC],
        color: '#a855f7',
        hasProperties: true,  // ← Теперь есть свойства (для изменения количества входов)
        resizable: true,
        minWidth: 120,
        minHeight: 80,
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC,
        defaultProps: {
            inputCount: 2  // ← Новое свойство
        }
    },
    'or': {
        name: 'ИЛИ',
        inputs: 2,  // По умолчанию 2
        outputs: 1,
        inputLabels: ['A', 'B'],
        inputTypes: [SIGNAL_TYPE.LOGIC, SIGNAL_TYPE.LOGIC],
        outputLabels: ['результат'],
        outputTypes: [SIGNAL_TYPE.LOGIC],
        color: '#a855f7',
        hasProperties: true,  // ← Теперь есть свойства
        resizable: true,
        minWidth: 120,
        minHeight: 80,
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC,
        defaultProps: {
            inputCount: 2  // ← Новое свойство
        }
    },
    'not': {
        name: 'НЕ',
        inputs: 1,
        outputs: 1,
        inputLabels: ['A'],
        inputTypes: [SIGNAL_TYPE.LOGIC],
        outputLabels: ['¬A'],
        outputTypes: [SIGNAL_TYPE.LOGIC],
        color: '#a855f7',
        hasProperties: true,
        resizable: true,
        minWidth: 100,
        minHeight: 60,
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC
    },
    'if': {
        name: 'ЕСЛИ',
        inputs: 2,
        outputs: 1,  // ← Только один выход!
        inputLabels: ['A', 'B'],
        inputTypes: [SIGNAL_TYPE.ANY, SIGNAL_TYPE.ANY],
        outputLabels: ['результат'],  // ← Просто результат
        outputTypes: [SIGNAL_TYPE.LOGIC],  // ← Выход типа LOGIC
        color: '#e94560',
        hasProperties: true,
        defaultProps: { operator: '=' },
        resizable: true,
        minWidth: 120,
        minHeight: 80,
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC
    },
    'separator': {  // ← НОВЫЙ ЭЛЕМЕНТ
        name: 'Сепаратор',
        inputs: 1,
        outputs: 2,
        inputLabels: ['сигнал'],
        inputTypes: [SIGNAL_TYPE.LOGIC],
        outputLabels: ['ИСТИНА', 'ЛОЖЬ'],
        outputTypes: [SIGNAL_TYPE.TRUE, SIGNAL_TYPE.FALSE],  // ← TRUE и FALSE
        color: '#f59e0b',
        hasProperties: true,
        resizable: true,
        minWidth: 120,
        minHeight: 80,
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC
    },
    'const': {
        name: 'Константа',
        inputs: 0,
        outputs: 1,
        outputLabels: ['out'],
        outputTypes: [SIGNAL_TYPE.NUMERIC],
        color: '#3b82f6',
        hasProperties: true,
        defaultProps: { value: 0 },
        resizable: true,
        minWidth: 120,
        minHeight: 60,
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC
    },
    'formula': {
        name: 'Формула',
        inputs: 2,
        outputs: 1,
        inputLabels: ['in₁', 'in₂'],
        inputTypes: [SIGNAL_TYPE.ANY, SIGNAL_TYPE.ANY],
        outputLabels: ['результат'],
        outputTypes: [SIGNAL_TYPE.NUMERIC],
        color: '#f59e0b',
        hasProperties: true,
        resizable: true,
        minWidth: 140,
        minHeight: 80,
        defaultProps: {
            expression: '',
            inputCount: 2
        },
        hasConditionPort: true,
        conditionPortType: SIGNAL_TYPE.LOGIC
    },
    'output': {
        name: 'Выход',
        inputs: 1,
        outputs: 0,
        inputLabels: ['сигнал'],
        inputTypes: [SIGNAL_TYPE.ANY],
        color: '#10b981',
        hasProperties: true,
        defaultProps: { label: 'Выход', outputGroup: '' },
        resizable: true,
        minWidth: 150,
        minHeight: 60,
    },  // ← важно, если предыдущий элемент не заканчивается запятой
    'table': {
        name: 'Таблица',
        inputs: 0,
        outputs: 1,
        outputLabels: ['out'],
        outputTypes: [SIGNAL_TYPE.NUMERIC],
        color: '#60a5fa',
        hasProperties: true,
        defaultProps: { name: 'Таблица' },
        resizable: true,
        minWidth: 190,
        minHeight: 120


    },

    'group': {
        name: 'Группа',
        inputs: 0,
        outputs: 0,
        color: '#6b7280',
        resizable: true,
        minWidth: 200,
        minHeight: 120,
        hasProperties: true,
        defaultProps: { title: 'Группа' }
    }
};

const VIEWPORT_CONFIG = {
    minZoom: 0.1,
    maxZoom: 3,
    zoomStep: 0.1,
    panSpeed: 1,
    canvasWidth: 5000,
    canvasHeight: 5000
};

const MINIMAP_CONFIG = {
    width: 200,
    height: 150,
    padding: 10
};