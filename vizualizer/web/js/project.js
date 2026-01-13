/**
 * Модуль управления проектом (сохранение, загрузка)
 */

const Project = {
    /**
     * Инициализация
     */
        /**
     * Инициализация
     */
    init() {
        document.getElementById('btn-new').addEventListener('click', () => this.newProject());
        document.getElementById('btn-save').addEventListener('click', () => this.saveProject());
        // Теперь кнопка Load вызывает функцию напрямую, а не input file
        document.getElementById('btn-load').addEventListener('click', () => this.showProjectList());
        document.getElementById('btn-project-settings').addEventListener('click', () => {
            Modal.showProjectPropertiesModal();
        });

        // Блок document.getElementById('file-input')... удален.
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
     * Запрос имени файла и загрузка с сервера
     */
    async loadProjectPrompt() {
        const filename = window.prompt(
            "Введите имя файла проекта для загрузки (с сервера). Пример: scheme_logic.json", 
            AppState.project.code ? `${AppState.project.code}_${AppState.project.type}.json` : "scheme_type.json"
        );
        
        if (!filename) return; // Отмена

        try {
            // Используем обертку из Settings.js для запроса к /api/project/load
            const data = await Settings.loadProject(filename);
            
            // Если загрузка успешна, вызываем основную функцию обработки данных
            this._processLoadedData(data);
            alert(`Проект "${filename}" успешно загружен с сервера.`);

        } catch (error) {
            console.error('Ошибка загрузки проекта:', error);
            alert(`Ошибка загрузки проекта: ${error.message}`);
        }
    },

    /**
     * Сохранение проекта
     */
        async saveProject() { // !!! Сделать функцию асинхронной (async) !!!
        // 1. Проверяем свойства проекта
        if (!AppState.project.code) {
            Modal.showProjectPropertiesModal();
            alert('Пожалуйста, укажите код проекта перед сохранением.');
            return;
        }

        // Обновляем размеры рамок перед сохранением
        updateFrameChildren();

        // 2. Сборка объекта проекта
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

        const filename = `${AppState.project.code || 'scheme'}_${AppState.project.type}.json`;

        // 3. Сохранение на сервер
        try {
            await Settings.saveProject(filename, project);
            alert(`Проект успешно сохранен на сервере как: ${filename}`);
        } catch (error) {
            console.error('Ошибка сохранения проекта:', error);
            alert(`Ошибка сохранения проекта: ${error.message}`);
        }
    },

    async showProjectList() {
        try {
            const result = await Settings.listProjects(); // нужно реализовать в settings.js
            const list = result.projects || [];

            if (list.length === 0) {
                alert('Проекты в папке не найдены.');
                return;
            }

            const choice = window.prompt(
                'Список проектов:\n' + list.map((p, i) => `${i + 1}. ${p.code || p.filename} — ${p.description}`).join('\n') +
                '\n\nВведите номер проекта для загрузки:',
                '1'
            );
            const index = parseInt(choice, 10) - 1;
            if (isNaN(index) || !list[index]) return;

            await this.loadProjectByFilename(list[index].filename);
        } catch (error) {
            console.error(error);
            alert('Не удалось получить список проектов: ' + error.message);
        }
    },

    async loadProjectByFilename(filename) {
        try {
            const data = await Settings.loadProject(filename);
            this._processLoadedData(data);
            alert(`Проект "${filename}" загружен.`);
        } catch (error) {
            console.error(error);
            alert('Ошибка загрузки проекта: ' + error.message);
        }
    },

    /**
     * Загрузка проекта
     */
    _processLoadedData(data) {
        try {
            // Очищаем
            document.getElementById('workspace').innerHTML = '';
            document.getElementById('connections-svg').innerHTML = '';
            resetState();

            // Загружаем свойства проекта
            if (data.project) {
                AppState.project = { ...AppState.project, ...data.project };
            }
            
            // ... (остальная логика загрузки, которая была в loadProject)

            // Загружаем состояние
            AppState.elementCounter = data.counter || 0;

            // ... (дальше без изменений)
            // ... (загрузка элементов, connections, updateTransform)

        } catch (e) {
            alert('Ошибка обработки данных проекта: ' + e.message);
            console.error(e);
        }
    }
};