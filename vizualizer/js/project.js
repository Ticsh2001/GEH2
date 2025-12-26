/**
 * Модуль управления проектом (сохранение, загрузка)
 */

const Project = {
    /**
     * Инициализация
     */
    init() {
        document.getElementById('btn-new').addEventListener('click', () => this.newProject());
        document.getElementById('btn-save').addEventListener('click', () => this.saveProject());
        document.getElementById('btn-load').addEventListener('click', () => {
            document.getElementById('file-input').click();
        });
        document.getElementById('btn-project-settings').addEventListener('click', () => {
            Modal.showProjectPropertiesModal();
        });

        document.getElementById('file-input').addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = (ev) => this.loadProject(ev.target.result);
                reader.readAsText(file);
            }
            e.target.value = '';
        });
    },

    /**
     * Новый проект
     */
    newProject() {
        if (Object.keys(AppState.elements).length > 0) {
            if (!confirm('Создать новый проект? Несохранённые изменения будут потеряны.')) {
                return;
            }
        }

        document.getElementById('workspace').innerHTML = '';
        document.getElementById('connections-svg').innerHTML = '';

        resetState();
        Viewport.updateTransform();
    },

    /**
     * Сохранение проекта
     */
    saveProject() {
        // Проверяем, заполнены ли свойства проекта
        if (!AppState.project.code) {
            Modal.showProjectPropertiesModal();
            alert('Пожалуйста, укажите код проекта перед сохранением.');
            return;
        }

        updateFrameChildren();

        const project = {
            version: '1.0',
            project: AppState.project,
            elements: AppState.elements,
            connections: AppState.connections,
            counter: AppState.elementCounter,
            viewport: {
                zoom: AppState.viewport.zoom,
                panX: AppState.viewport.panX,
                panY: AppState.viewport.panY
            }
        };

        const jsonStr = JSON.stringify(project, null, 2);
        const blob = new Blob([jsonStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);

        const filename = `${AppState.project.code || 'scheme'}_${AppState.project.type}.json`;

        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();

        URL.revokeObjectURL(url);
    },

    /**
     * Загрузка проекта
     */
    loadProject(jsonStr) {
        try {
            const data = JSON.parse(jsonStr);

            // Очищаем
            document.getElementById('workspace').innerHTML = '';
            document.getElementById('connections-svg').innerHTML = '';
            resetState();

            // Загружаем свойства проекта
            if (data.project) {
                AppState.project = { ...AppState.project, ...data.project };
            }

            // Загружаем состояние
            AppState.elementCounter = data.counter || 0;

            // Загружаем viewport
            if (data.viewport) {
                AppState.viewport.zoom = data.viewport.zoom || 1;
                AppState.viewport.panX = data.viewport.panX || 0;
                AppState.viewport.panY = data.viewport.panY || 0;
            }

            // Сначала загружаем рамки
            Object.values(data.elements || {})
                .filter(e => e.type === 'output-frame')
                .forEach(elemData => {
                    Elements.addElement(
                        elemData.type,
                        elemData.x,
                        elemData.y,
                        elemData.props,
                        elemData.id,
                        elemData.width,
                        elemData.height
                    );
                });

            // Затем остальные элементы
            Object.values(data.elements || {})
                .filter(e => e.type !== 'output-frame')
                .forEach(elemData => {
                    Elements.addElement(
                        elemData.type,
                        elemData.x,
                        elemData.y,
                        elemData.props,
                        elemData.id,
                        elemData.width,
                        elemData.height
                    );
                });

            AppState.connections = data.connections || [];

            Viewport.updateTransform();
            Connections.drawConnections();

        } catch (e) {
            alert('Ошибка загрузки: ' + e.message);
            console.error(e);
        }
    }
};