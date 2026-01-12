const Settings = {
  config: null,

  async init() {
    // тянем настройки (не обязательно, но полезно)
    try {
      const r = await fetch('/api/settings');
      if (r.ok) this.config = await r.json();
    } catch (e) {
      console.warn('Settings load failed:', e);
    }
  },

  async fetchSignals(mask, limit = 50) {
    const url = `/api/signals?q=${encodeURIComponent(mask || '')}&limit=${encodeURIComponent(limit)}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error('Failed to fetch signals');
    return await r.json(); // {items, total}
  }
};