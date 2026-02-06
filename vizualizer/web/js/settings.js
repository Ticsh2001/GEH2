// settings.js — ПОЛНАЯ ИСПРАВЛЕННАЯ ВЕРСИЯ

const Settings = {
  config: null,
  templates: null,
  apiUrl: '',  // ← Добавь это! Пустая строка = относительные пути
  _initPromise: null,   // <-- НОВОЕ

  async init() {
    // Если уже инициализируемся — ждём тот же промис
    if (this._initPromise) return this._initPromise;

    this._initPromise = this._doInit();
    return this._initPromise;
  },

  async _doInit() {
    try {
      const r = await fetch('/api/settings');
      if (r.ok) this.config = await r.json();
    } catch (e) {
      console.warn('Settings load failed:', e);
    }
    try {
      const t = await this.fetchFormulaTemplates();
      this.templates = t.templates || [];
    } catch (e) {
      this.templates = [];
    }
  },

  getTemplatesMap() {
    const map = {};
    (this.templates || []).forEach(t => { if (t?.name) map[t.name] = t; });
    return map;
  },

  // ← ОДНА функция fetchSignals с cache-busting
  async fetchSignals(mask, limit = 50) {
    const timestamp = Date.now();
    const url = `${this.apiUrl}/api/signals?q=${encodeURIComponent(mask || '')}&limit=${limit}&_t=${timestamp}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error('Failed to fetch signals');
    return await r.json();
  },

  async saveProject(filename, projectData, target = 'projects') {
      if (!filename.endsWith('.json')) filename += '.json';
    const r = await fetch(`${this.apiUrl}/api/project/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename,
        target,
        content: projectData
      })
    });
    if (!r.ok) throw new Error('Failed to save project');
    return r.json();
  },
  
  async listProjects() {
    const r = await fetch(`${this.apiUrl}/api/project/list`);
    if (!r.ok) throw new Error('Failed to list projects');
    const data = await r.json();
    this.templates = data.templates || [];  // <-- обновляем кеш
    return data;
  },

  async fetchFormulaTemplates() {
    const r = await fetch(`${this.apiUrl}/api/formula-templates`);
    if (!r.ok) throw new Error('Failed to fetch formula templates');
    return await r.json();
  },

  async loadProject(filename, source = 'projects') {
    if (!filename.endsWith('.json')) {
      filename += '.json';
    }
    const r = await fetch(`${this.apiUrl}/api/project/load/${encodeURIComponent(filename)}?source=${source}`);
    if (!r.ok) {
      if (r.status === 404) {
        throw new Error(`Project "${filename}" not found (404)`);
      }
      throw new Error('Failed to load project');
    }
    return r.json();
  }
};