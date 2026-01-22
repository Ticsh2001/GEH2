/**
 * Глобальное состояние приложения
 * state.js
 */

const AppState = {
    // Элементы схемы
    elements: {},
    connections: [],
    elementCounter: 0,
    
    // Выделение
    selectedElement: null,
    selectedElements: [],
    
    // Перетаскивание
    draggingElement: null,
    dragOffset: { x: 0, y: 0 },
    isDraggingFromPalette: false,
    dragPreview: null,
    dragType: null,
    
    // Соединения
    connectingFrom: null,
    connectingFromType: null,
    tempLine: null,
    
    // Resize
    resizing: null,
    
    // Viewport (масштабирование и перемещение)
    viewport: {
        zoom: 1,
        panX: 0,
        panY: 0,
        isPanning: false,
        lastMouseX: 0,
        lastMouseY: 0
    },
    
    // Свойства проекта
    project: {
        code: '',
        type: PROJECT_TYPE.PARAMETER,
        description: '',
        dimension: '',
        possibleCause: '',
        guidelines: '',
        visualizer_state: null  // НОВОЕ: состояние визуализатора
    },
    
    // Выходные сигналы (автоматически определяются)
    outputs: {
        logical: [],
        numeric: []
    },
    
    // НОВОЕ: токен текущей сессии визуализатора
    currentVisualizerToken: null
};

/**
 * Сброс состояния
 */
function resetState() {
    AppState.elements = {};
    AppState.connections = [];
    AppState.elementCounter = 0;
    AppState.selectedElement = null;
    AppState.selectedElements = [];
    AppState.draggingElement = null;
    AppState.connectingFrom = null;
    AppState.tempLine = null;
    AppState.resizing = null;
    
    AppState.viewport = {
        zoom: 1,
        panX: 0,
        panY: 0,
        isPanning: false,
        lastMouseX: 0,
        lastMouseY: 0
    };
    
    AppState.project = {
        code: '',
        type: PROJECT_TYPE.PARAMETER,
        description: '',
        dimension: '',
        possibleCause: '',
        guidelines: '',
        visualizer_state: null  // НОВОЕ: сбрасываем состояние визуализатора
    };
    
    AppState.outputs = {
        logical: [],
        numeric: []
    };
    
    // НОВОЕ: сбрасываем токен визуализатора
    AppState.currentVisualizerToken = null;
}