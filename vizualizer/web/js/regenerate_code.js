// web/js/regenerate_code.js
// Usage: node web/js/regenerate_code.js <project_json_path> <templates_json_path>

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const projectPath = process.argv[2];
const templatesPath = process.argv[3];

if (!projectPath || !templatesPath) {
  console.error('Usage: node web/js/regenerate_code.js <project_json_path> <templates_json_path>');
  process.exit(2);
}

const filesToLoad = [
  path.join(__dirname, 'config.js'),
  path.join(__dirname, 'codegen_optimizer.js'),
  path.join(__dirname, 'codegen_graph.js'),
  path.join(__dirname, 'codegen.js'),
];

const utilsCode = `
(function() {
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

    for (let pass = 0; pass < 10; pass++) {
      let changed = false;

      expr = expr.replace(/([A-Za-z_]\\w*)\\s*\\(([^()]|\\([^()]*\\))*\\)/g, (match, name) => {
        const tpl = templatesMap[name];
        if (!tpl) return match;

        const open = match.indexOf('(');
        const close = match.lastIndexOf(')');
        const inside = match.slice(open + 1, close);
        const callArgs = splitArgsTopLevel(inside);

        let formalArgs = [];
        let argsConfig = {};

        if (Array.isArray(tpl.args)) {
          formalArgs = tpl.args;
        } else if (typeof tpl.args === 'object' && tpl.args !== null) {
          formalArgs = Object.keys(tpl.args);
          argsConfig = tpl.args;
        }

        if (callArgs.length !== formalArgs.length) return match;

        let body = String(tpl.body || '0');
        let conditions = [];

        formalArgs.forEach((fName, i) => {
          const actualVal = callArgs[i];
          const re = new RegExp('\\\\b' + fName + '\\\\b', 'g');
          body = body.replace(re, '(' + actualVal + ')');

          if (argsConfig[fName]) {
            const conf = argsConfig[fName];
            if (conf.min !== undefined && conf.min !== null) {
              conditions.push('(' + actualVal + ' >= ' + conf.min + ')');
            }
            if (conf.max !== undefined && conf.max !== null) {
              conditions.push('(' + actualVal + ' <= ' + conf.max + ')');
            }
          }
        });

        let resultExpr = '(' + body + ')';
        if (conditions.length > 0) {
          const conditionString = conditions.join(' AND ');
          const fallbackValue = tpl.return_value !== undefined ? tpl.return_value : 0;
          resultExpr = 'WHEN(' + conditionString + ', ' + body + ', ' + fallbackValue + ')';
        }

        changed = true;
        return resultExpr;
      });

      if (!changed) break;
    }

    return expr;
  }

  // Прокидываем и в Utils, и как глобальные
  window.Utils = window.Utils || {};
  window.Utils.splitArgsTopLevel = splitArgsTopLevel;
  window.Utils.expandFormulaTemplates = expandFormulaTemplates;

  window.splitArgsTopLevel = splitArgsTopLevel;
  window.expandFormulaTemplates = expandFormulaTemplates;
})();
`;

try {
  const project = JSON.parse(fs.readFileSync(projectPath, 'utf-8'));
  const templates = JSON.parse(fs.readFileSync(templatesPath, 'utf-8'));

  // Логгер в stderr
  const toStderr = (...args) => {
    try { process.stderr.write(args.join(' ') + '\n'); } catch (e) {}
  };

  const sandbox = {
    console: {
      log: toStderr,
      info: toStderr,
      warn: toStderr,
      error: toStderr,
      debug: toStderr,
    },
    AppState: {
      elements: project.elements || {},
      connections: project.connections || [],
      elementCounter: project.counter || 0,
      project: project.project || {}
    },
    Settings: {
      templates: templates.templates || [],
      getTemplatesMap() {
        const map = {};
        (this.templates || []).forEach(t => { if (t?.name) map[t.name] = t; });
        return map;
      }
    },
    document: {},
    performance: { now: () => Date.now() },
    window: {},
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.global = sandbox;

  const context = vm.createContext(sandbox);

  vm.runInContext(utilsCode, context, { filename: 'utilsCode' });

  for (const f of filesToLoad) {
    const code = fs.readFileSync(f, 'utf-8');
    vm.runInContext(code, context, { filename: f });
  }

  const CodeGen = context.window.CodeGen || context.CodeGen;
  if (!CodeGen) {
    throw new Error("CodeGen is undefined after loading scripts");
  }

  const code = CodeGen.generate();
  project.code = code;

  // Только JSON в stdout
  process.stdout.write(JSON.stringify(project, null, 2));
} catch (e) {
  // Ошибки — в stderr, код возврата 1
  console.error(e && e.stack ? e.stack : String(e));
  process.exit(1);
}