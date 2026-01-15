const Settings = {
  config: null,
  templates: null,

  async init() {
    // тянем настройки (не обязательно, но полезно)
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

  async fetchSignals(mask, limit = 50) {
    const url = `/api/signals?q=${encodeURIComponent(mask || '')}&limit=${encodeURIComponent(limit)}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error('Failed to fetch signals');
    return await r.json(); // {items, total}
  },
  // ... в объекте Settings

  async saveProject(filename, projectData) {
    if (!filename.endsWith('.json')) {
      filename += '.json';
    }
    const r = await fetch('/api/project/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: filename,
        content: projectData
      })
    });
    if (!r.ok) throw new Error('Failed to save project');
    return r.json();
  },
  
  async listProjects() {
    const r = await fetch('/api/project/list');
    if (!r.ok) throw new Error('Failed to list projects');
    return r.json();
  },

  async fetchFormulaTemplates() {
      const r = await fetch('/api/formula-templates');
      if (!r.ok) throw new Error('Failed to fetch formula templates');
      return await r.json(); // {templates:[...]}
  },

  async loadProject(filename) {
    if (!filename.endsWith('.json')) {
      filename += '.json';
    }
    const r = await fetch(`/api/project/load/${encodeURIComponent(filename)}`);
    if (!r.ok) {
        if (r.status === 404) {
             throw new Error(`Project "${filename}" not found (404)`);
        }
        throw new Error('Failed to load project');
    }
    return r.json();
  }

// ...
};