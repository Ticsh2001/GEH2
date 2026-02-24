(function() {
  const qs = new URLSearchParams(window.location.search);
  const filename = qs.get('filename') || '';
  const source = qs.get('source') || 'projects';

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

    // --- НОВОЕ: Разные поля в зависимости от типа проекта ---
    if (type === 'parameter') {
      typeFields.innerHTML = `
        <div class="row">
          <label for="fld-description">Описание:</label>
          <textarea id="fld-description">${proj.description || ''}</textarea>
        </div>
        <div class="row">
          <label for="fld-dimension">Размерность:</label>
          <input type="text" id="fld-dimension" value="${(proj.dimension || '').replace(/"/g, '&quot;')}">
        </div>
      `;
    } else if (type === 'rule') {
      typeFields.innerHTML = `
        <div class="row">
          <label for="fld-possible">Возможная причина:</label>
          <textarea id="fld-possible">${proj.possibleCause || ''}</textarea>
        </div>
        <div class="row">
          <label for="fld-guidelines">Методические указания:</label>
          <textarea id="fld-guidelines">${proj.guidelines || ''}</textarea>
        </div>
      `;
    } else if (type === 'template') {
      typeFields.innerHTML = `
        <div class="row">
          <label for="fld-description">Описание шаблона:</label>
          <textarea id="fld-description">${proj.description || ''}</textarea>
        </div>
        <div class="row">
          <label for="fld-templateArgs">Описание аргументов (JSON):</label>
          <textarea id="fld-templateArgs" rows="4">${JSON.stringify(proj.templateArgs || {}, null, 2)}</textarea>
        </div>
      `;
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
          <div style="position:relative; flex:1;">
            <input type="text" id="${inputId}" placeholder="Введите KKS для замены или используйте * для поиска">
            <div id="results-${inputId}" 
                 style="position:absolute; top:100%; left:0; right:0; max-height:160px; overflow-y:auto; 
                        background:#0f3460; border-radius:5px; margin-top:2px; display:none; z-index:1000;
                        border:1px solid #4a90d9;">
            </div>
          </div>
        `;
        signalsWrap.appendChild(row);
      });
    }

    // --- НОВОЕ: Автокомплит для нового кода сигнала ---
    setupNewCodeAutocomplete();

    // --- НОВОЕ: Автокомплит для замены сигналов ---
    setupSignalMappingAutocomplete(uniqSignals);

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

        // --- НОВОЕ: Сохраняем поля в зависимости от типа ---
        if (type === 'parameter') {
          newContent.project.description = (document.getElementById('fld-description')?.value || '').trim();
          newContent.project.dimension = (document.getElementById('fld-dimension')?.value || '').trim();
          // Сбрасываем поля других типов
          newContent.project.possibleCause = '';
          newContent.project.guidelines = '';
        } else if (type === 'rule') {
          newContent.project.possibleCause = (document.getElementById('fld-possible')?.value || '').trim();
          newContent.project.guidelines = (document.getElementById('fld-guidelines')?.value || '').trim();
          // Сбрасываем поля других типов
          newContent.project.description = '';
          newContent.project.dimension = '';
        } else if (type === 'template') {
          newContent.project.description = (document.getElementById('fld-description')?.value || '').trim();
          try {
            const jsonStr = (document.getElementById('fld-templateArgs')?.value || '').trim();
            newContent.project.templateArgs = jsonStr ? JSON.parse(jsonStr) : {};
          } catch (e) {
            alert('Описание аргументов (JSON) содержит ошибку.');
            return;
          }
          // Сбрасываем поля других типов
          newContent.project.possibleCause = '';
          newContent.project.guidelines = '';
          newContent.project.dimension = '';
        }

        // 1) input-signal: обновляем props.name + НОВОЕ: автозаполнение свойств
        Object.values(newContent.elements || {}).forEach(el => {
          if (el && el.type === 'input-signal') {
            const oldName = (el.props && el.props.name) ? String(el.props.name).trim() : '';
            if (oldName && mapping[oldName]) {
              const newName = mapping[oldName];
              el.props.name = newName;
              
              // НОВОЕ: Ищем данные выбранного сигнала и обновляем свойства элемента
              const selectedSignalData = window.selectedSignalsData?.[newName];
              if (selectedSignalData) {
                el.props.description = selectedSignalData.Description || '';
                el.props.dimension = selectedSignalData.EngineeringUnit || selectedSignalData.Dimension || '';
                // Можно добавить другие поля по необходимости
              }
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

  // --- НОВАЯ ФУНКЦИЯ: Автокомплит для нового кода сигнала ---
  function setupNewCodeAutocomplete() {
    const input = document.getElementById('new-code');
    if (!input) return;

    const resultsDiv = document.createElement('div');
    resultsDiv.id = 'new-code-results';
    resultsDiv.className = 'autocomplete-results';
    resultsDiv.id = 'new-code-results';
    resultsDiv.style.cssText = `
      position:absolute; top:100%; left:0; right:0; max-height:160px; overflow-y:auto; 
      background:#0f3460; border-radius:5px; margin-top:2px; display:none; z-index:1000;
      border:1px solid #4a90d9;
    `;
    
    // Обёртываем input в relative контейнер
    const wrapper = document.createElement('div');
    wrapper.style.position = 'relative';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    wrapper.appendChild(resultsDiv);

    let timer = null;
    // НОВОЕ: Глобальное хранилище данных выбранных сигналов
    window.selectedSignalsData = window.selectedSignalsData || {};

    const renderResults = (items) => {
      if (!items || items.length === 0) {
        resultsDiv.innerHTML = '<div style="color:#666;padding:6px;">Нет совпадений</div>';
        resultsDiv.style.display = 'block';
        return;
      }

      resultsDiv.innerHTML = items.map(s => `
        <div class="signal-result-item"
            style="padding:6px 8px; cursor:pointer; border-bottom:1px solid rgba(255,255,255,0.08);">
            <div style="font-weight:600;">${s.Tagname}</div>
            <div style="color:#aaa; font-size:11px;">${s.Description || ''}</div>
        </div>
      `).join('');

      resultsDiv.style.display = 'block';

      resultsDiv.querySelectorAll('.signal-result-item').forEach((div, i) => {
        div.addEventListener('click', () => {
          const chosen = items[i];
          input.value = chosen.Tagname;
          
          // НОВОЕ: Сохраняем данные выбранного сигнала для автозаполнения полей проекта
          window.selectedSignalsData[chosen.Tagname] = chosen;
          
          // НОВОЕ: Автозаполняем поля проекта в зависимости от типа
          const type = document.getElementById('project-type').textContent.trim();
          if (type === 'parameter') {
            const descField = document.getElementById('fld-description');
            const dimField = document.getElementById('fld-dimension');
            if (descField) descField.value = chosen.Description || '';
            if (dimField) dimField.value = chosen.EngineeringUnit || chosen.Dimension || '';
          }
          
          resultsDiv.style.display = 'none';
        });
      });
    };

    const search = async () => {
      const mask = input.value.trim();

      if (!mask.includes('*')) {
        resultsDiv.style.display = 'none';
        return;
      }

      resultsDiv.innerHTML = '<div style="color:#666;padding:6px;">Поиск...</div>';
      resultsDiv.style.display = 'block';

      try {
        // Используем тот же API что и в основной модалке
        const response = await fetch(`/api/signals?q=${encodeURIComponent(mask)}&limit=50`);
        const data = await response.json();
        renderResults(data.items || []);
      } catch (e) {
        resultsDiv.innerHTML = '<div style="color:#666;padding:6px;">Ошибка загрузки сигналов</div>';
        resultsDiv.style.display = 'block';
        console.error(e);
      }
    };

    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(search, 200);
    });

    // Закрытие списка при клике вне
    document.addEventListener('click', (e) => {
      if (!wrapper.contains(e.target)) {
        resultsDiv.style.display = 'none';
      }
    });
  }

  function setupSignalMappingAutocomplete(uniqSignals) {
  window.selectedSignalsData = window.selectedSignalsData || {};

  uniqSignals.forEach(sig => {
    const inputId = `map-${sig.replace(/[^A-Za-z0-9_\u0400-\u04FF§.]/g, '_')}`;
    const input = document.getElementById(inputId);
    const resultsDiv = document.getElementById(`results-${inputId}`);

    if (!input || !resultsDiv) return;

    let timer = null;

    const renderResults = (items) => {
      if (!items || items.length === 0) {
        resultsDiv.innerHTML = '<div style="color:#666;padding:6px;">Нет совпадений</div>';
        resultsDiv.style.display = 'block';
        return;
      }

      resultsDiv.innerHTML = items.map(s => `
        <div class="signal-result-item">
          <div style="font-weight:600;">${s.Tagname}</div>
          <div style="color:#aaa;font-size:11px;">${s.Description || ''}</div>
        </div>
      `).join('');

      resultsDiv.style.display = 'block';

      resultsDiv.querySelectorAll('.signal-result-item').forEach((div, i) => {
        div.addEventListener('click', () => {
          const chosen = items[i];
          input.value = chosen.Tagname;
          window.selectedSignalsData[chosen.Tagname] = chosen;
          resultsDiv.style.display = 'none';
        });
      });
    };

    const search = async () => {
      const mask = input.value.trim();

      if (!mask.includes('*')) {
        resultsDiv.style.display = 'none';
        return;
      }

      resultsDiv.innerHTML = '<div style="color:#666;padding:6px;">Поиск...</div>';
      resultsDiv.style.display = 'block';

      try {
        const response = await fetch(`/api/signals?q=${encodeURIComponent(mask)}&limit=50`);
        const data = await response.json();
        renderResults(data.items || []);
      } catch (e) {
        console.error(e);
        resultsDiv.innerHTML = '<div style="color:#666;padding:6px;">Ошибка загрузки</div>';
      }
    };

    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(search, 200);
    });

    document.addEventListener('click', (e) => {
      if (!input.parentNode.contains(e.target)) {
        resultsDiv.style.display = 'none';
      }
    });
  });
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