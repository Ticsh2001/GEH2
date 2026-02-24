(function() {
  // --- ПАРСИМ ПАРАМЕТРЫ ИЗ URL ---
  const qs = new URLSearchParams(window.location.search);
  const filename = qs.get('filename') || '';
  const source = qs.get('source') || 'projects';

  // Класс символов, которые считаем частью имени сигнала
  const identClass = 'A-Za-z0-9_\\u0400-\\u04FF§\\.'; 
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  // Замена имён сигналов только по целым токенам
  function replaceTokens(text, mapping) {
    if (!text || typeof text !== 'string') return text;
    let out = text;
    for (const [oldName, newName] of Object.entries(mapping)) {
      if (!oldName || !newName) continue;
      const re = new RegExp(`(^|[^${identClass}])(${esc(oldName)})(?![${identClass}])`, 'g');
      out = out.replace(re, `$1${newName}`);
    }
    return out;
  }

  // Загрузка проекта с бэкенда
  async function loadProject() {
    if (!filename) throw new Error('Не передан filename в URL');
    const url = `/api/project/load/${encodeURIComponent(filename)}?source=${encodeURIComponent(source)}`;
    console.log('[similar] loadProject:', url);
    const resp = await fetch(url);
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      console.error('[similar] loadProject error:', resp.status, text);
      throw new Error(`Не удалось загрузить проект: ${resp.status}`);
    }
    const json = await resp.json();
    console.log('[similar] project loaded, keys:', Object.keys(json));
    return json;
  }

  function buildUI(content) {
    const proj = content.project || {};
    const type = (proj.type || 'parameter').trim();
    document.getElementById('project-type').textContent = type || '—';

    const code = (proj.code || '').trim();
    const newCodeEl = document.getElementById('new-code');
    newCodeEl.value = code ? (code + '_COPY') : '';

    const typeFields = document.getElementById('type-fields');
    typeFields.innerHTML = '';

    const addField = (label, id, value = '') => {
      const row = document.createElement('div');
      row.className = 'row';
      // Простая экранировка двойных кавычек
      const safeVal = String(value || '').replace(/"/g, '&quot;');
      row.innerHTML = `
        <label for="${id}">${label}:</label>
        <input type="text" id="${id}" value="${safeVal}">
      `;
      typeFields.appendChild(row);
    };

    if (type === 'parameter' || type === 'rule') {
      addField('Описание', 'fld-description', proj.description || '');
      addField('Единицы измерения', 'fld-dimension', proj.dimension || '');
      addField('Возможная причина', 'fld-possible', proj.possibleCause || '');
      addField('Рекомендации', 'fld-guidelines', proj.guidelines || '');
    } else if (type === 'template') {
      addField('Описание', 'fld-description', proj.description || '');
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML = `
        <label for="fld-templateArgs">Описание аргументов (JSON):</label>
        <textarea id="fld-templateArgs" rows="4">${JSON.stringify(proj.templateArgs || {}, null, 2)}</textarea>
      `;
      typeFields.appendChild(row);
    }

    // --- Список входных сигналов ---
    const signalsWrap = document.getElementById('signals');
    signalsWrap.innerHTML = '';

    const elements = content.elements || {};
    const inputSignals = [];
    Object.values(elements).forEach(el => {
      if (el && el.type === 'input-signal') {
        const name = (el.props && el.props.name) ? String(el.props.name).trim() : '';
        if (name) inputSignals.push(name);
      }
    });

    console.log('[similar] inputSignals:', inputSignals);

    const seen = new Set();
    const uniqSignals = [];
    for (const s of inputSignals) {
      if (!seen.has(s)) {
        seen.add(s);
        uniqSignals.push(s);
      }
    }

    if (uniqSignals.length === 0) {
      const p = document.createElement('p');
      p.textContent = 'Во входных данных проекта не найдено элементов input-signal.';
      signalsWrap.appendChild(p);
    } else {
      uniqSignals.forEach(sig => {
        const row = document.createElement('div');
        row.className = 'row';
        const inputId = `map-${sig.replace(/[^A-Za-z0-9_\u0400-\u04FF§.]/g, '_')}`;
        row.innerHTML = `
          <label>${sig}</label>
          <input type="text" id="${inputId}" placeholder="Введите KKS для замены">
        `;
        signalsWrap.appendChild(row);
      });
    }

    // --- Обработчики кнопок ---
    document.getElementById('btn-save-similar').onclick = async () => {
      try {
        const newCode = newCodeEl.value.trim();
        if (!newCode) {
          alert('Введите новое имя сигнала (Tagname).');
          return;
        }

        // Составляем mapping: старое имя -> новое
        const mapping = {};
        uniqSignals.forEach(sig => {
          const inputId = `map-${sig.replace(/[^A-Za-z0-9_\u0400-\u04FF§.]/g, '_')}`;
          const v = (document.getElementById(inputId)?.value || '').trim();
          if (v) mapping[sig] = v;
        });

        console.log('[similar] mapping:', mapping);

        // Глубокая копия проекта
        const newContent = JSON.parse(JSON.stringify(content));
        newContent.project = newContent.project || {};
        newContent.project.code = newCode;
        newContent.project.lastModifiedAt = new Date().toISOString();

        if (type === 'parameter' || type === 'rule') {
          newContent.project.description =
            (document.getElementById('fld-description')?.value || '').trim();
          newContent.project.dimension =
            (document.getElementById('fld-dimension')?.value || '').trim();
          newContent.project.possibleCause =
            (document.getElementById('fld-possible')?.value || '').trim();
          newContent.project.guidelines =
            (document.getElementById('fld-guidelines')?.value || '').trim();
        } else if (type === 'template') {
          newContent.project.description =
            (document.getElementById('fld-description')?.value || '').trim();
          try {
            const jsonStr = (document.getElementById('fld-templateArgs')?.value || '').trim();
            newContent.project.templateArgs = jsonStr ? JSON.parse(jsonStr) : {};
          } catch (e) {
            alert('Описание аргументов (JSON) содержит ошибку.');
            return;
          }
        }

        // 1) input-signal: обновляем props.name
        Object.values(newContent.elements || {}).forEach(el => {
          if (el && el.type === 'input-signal') {
            const oldName = (el.props && el.props.name) ? String(el.props.name).trim() : '';
            if (oldName && mapping[oldName]) {
              el.props.name = mapping[oldName];
            }
          }
        });

        // 2) Формулы
        Object.values(newContent.elements || {}).forEach(el => {
          if (el && el.type === 'formula') {
            const expr = (el.props && el.props.expression) ? String(el.props.expression) : '';
            if (expr) el.props.expression = replaceTokens(expr, mapping);
          }
        });

        // 3) switch — строковые поля в props
        Object.values(newContent.elements || {}).forEach(el => {
          if (el && el.type === 'switch') {
            const props = el.props || {};
            Object.keys(props).forEach(k => {
              const val = props[k];
              if (typeof val === 'string') {
                props[k] = replaceTokens(val, mapping);
              }
            });
          }
        });

        // 4) Поле code (готовый код проекта)
        if (typeof newContent.code === 'string' && newContent.code) {
          newContent.code = replaceTokens(newContent.code, mapping);
        }

        const newFilename = `${newCode}_${type}.json`;
        const target = (type === 'template') ? 'templates' : 'projects';

        console.log('[similar] saving new project:', newFilename, 'target:', target);

        const resp = await fetch('/api/project/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: newFilename,
            content: newContent,
            target
          })
        });

        if (!resp.ok) {
          let detail = '';
          try {
            const data = await resp.json();
            detail = data.detail || JSON.stringify(data);
          } catch (e) {
            detail = `HTTP ${resp.status}`;
          }
          throw new Error(detail);
        }

        alert(`Новый проект сохранен: ${newFilename}`);
      } catch (e) {
        console.error('[similar] save error:', e);
        alert('Ошибка сохранения: ' + e.message);
      }
    };

    document.getElementById('btn-cancel').onclick = () => {
      window.close();
    };
  }

  // --- Вход в скрипт ---
  (async function main() {
    try {
      const content = await loadProject();
      buildUI(content);
    } catch (e) {
      console.error('[similar] main error:', e);
      alert('Не удалось загрузить исходный проект: ' + e.message);
    }
  })();
})();