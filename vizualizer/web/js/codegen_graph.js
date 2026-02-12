// js/codegen_graph.js


function toSimpleExpr(valueStr) {
  const s = String(valueStr).trim();
  if (s === '0') return Optimizer.Const(0);
  const num = parseFloat(s.replace(',', '.'));
  if (!isNaN(num) && String(num) === s) {
    return Optimizer.Const(num);
  }
  return Optimizer.Var(s);
}


const CodeGenGraph = {
        /**
     * Собрать все условия вверх по цепочке cond‑портов (до корня).
     * Возвращает null или объединённое через AND условие.
     */
/**
 * Собрать ВСЕ условия: и через cond-порты, и через контекст обычных входов
 */
    collectAllCond(graph) {
        if (!graph) return null;
        
        let c = null;
        const elem = graph.elem;

        // 1. Собираем условия через cond-порт (как было)
        if (graph.condInput) {
            const condConn = graph.condInput.conn;
            const fromGraph = graph.condInput.fromGraph;
            const oneCond = this.evalConditionFromPort(fromGraph, condConn.fromPort);
            c = oneCond;

            // Рекурсивно идём вверх по cond-цепочке
            const upCond = this.collectAllCond(fromGraph);
            if (upCond) {
                c = c ? Optimizer.And(c, upCond) : upCond;
            }
        }

        // 2. НОВОЕ: если это separator — учитываем контекст его входа
        if (elem.type === 'separator' && graph.inputs.length > 0) {
            const inputGraph = graph.inputs[0].fromGraph;
            const inputContext = this.collectAllCond(inputGraph);
            if (inputContext) {
                c = c ? Optimizer.And(c, inputContext) : inputContext;
            }
        }

        return c;
    },

    isInlineableType(type) {
        return type === 'switch';
    },

    expandElementRefs(exprStr, currentElemId) {
        if (!exprStr) return exprStr;

        const tokenRegex = /\b[a-zA-Z_]\w*\b/g;
        const matches = exprStr.match(tokenRegex);
        if (!matches) return exprStr;

        const seen = new Set();
        let result = exprStr;

        for (const token of matches) {
            if (seen.has(token)) continue;
            seen.add(token);

            if (token === currentElemId) continue;

            const elem = AppState.elements[token];
            if (!elem || !this.isInlineableType(elem.type)) continue;

            const refGraph = this.buildDependencyGraph(token);
            if (!refGraph) continue;

            const { cond, expr } = this.evalGraphValue(refGraph);
            if (!expr) continue;

            let embedded = expr;
            if (cond) {
                embedded = Optimizer.When(cond, embedded, Optimizer.Const(0));
            }

            const printed = Optimizer.printExpr(Optimizer.simplifyExpr(embedded));
            const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            result = result.replace(new RegExp(`\\b${escaped}\\b`, 'g'), `(${printed})`);
        }

        return result;
    },

    buildSwitchExpr(graph) {
        if (!graph) return Optimizer.Const(0);

        const elem = graph.elem;
        const cases = Array.isArray(elem.props?.cases) ? elem.props.cases : [];

        // === ИСПРАВЛЕНИЕ: Получаем A как значение ИЛИ условие ===
        const aGraph = graph.inputs.find(inp => inp.conn.toPort === 'in-0')?.fromGraph;
        let aExpr, isLogicA = false;
        
        if (aGraph) {
            // Сначала пробуем получить как значение
            aExpr = this.evalValue(aGraph);
            
            // Если элемент логический (range, and, or, not, if) - помечаем флаг
            const logicalTypes = ['range', 'and', 'or', 'not', 'if'];
            if (logicalTypes.includes(aGraph.elem.type)) {
                isLogicA = true;
            }
        } else {
            aExpr = Optimizer.Const(0);
        }
        // ======================================================

        const defaultGraph = graph.inputs.find(inp => inp.conn.toPort === 'in-1')?.fromGraph;
        const defaultVal = defaultGraph ? this.evalValue(defaultGraph) : Optimizer.Const(0);

        let expr = defaultVal;

        for (let i = cases.length - 1; i >= 0; i--) {
            const cfg = cases[i] || {};
            const op = cfg.op || '=';
            const valueStr = (cfg.value !== undefined) ? String(cfg.value) : '0';

            const inputIndex = Number.isInteger(cfg.inputIndex) ? cfg.inputIndex : null;
            if (inputIndex === null) continue;

            const portName = `in-${inputIndex}`;
            const inGraph = graph.inputs.find(inp => inp.conn.toPort === portName)?.fromGraph;
            if (!inGraph) continue;

            const caseExpr = this.evalValue(inGraph);
            
            // === ИСПРАВЛЕНИЕ: Специальная обработка логического A ===
            let cond;
            if (isLogicA) {
                // Для логических элементов: сравниваем с 1/0
                if (valueStr === '1') {
                    // Если A - условие, и ищем 1 → используем само условие
                    cond = this.evalLogic(aGraph);
                } else if (valueStr === '0') {
                    // Если ищем 0 → отрицаем условие
                    cond = Optimizer.Not(this.evalLogic(aGraph));
                } else {
                    // Для других значений (2,3...) → всегда false
                    cond = Optimizer.FalseCond;
                }
            } else {
                // Для числовых значений - стандартное сравнение
                const aName = this.exprToStr(aExpr);
                cond = Optimizer.Cmp(aName, op, valueStr);
            }
            // ====================================================

            expr = Optimizer.When(cond, caseExpr, expr);
        }

        return expr;
    },


    buildDependencyGraph(elementId) {
        const graph = {
            nodeId: elementId,
            elem: AppState.elements[elementId],
            inputs: [],
            condInput: null,
        };

        if (!graph.elem) return null;

        const inConns = AppState.connections
        .filter(c => c.toElement === elementId && c.toPort.startsWith('in-'))
        .sort((a, b) => {
            const ai = parseInt(a.toPort.split('-')[1] || '0', 10);
            const bi = parseInt(b.toPort.split('-')[1] || '0', 10);
            return ai - bi;
        });

        inConns.forEach(conn => {
        graph.inputs.push({
            conn,
            fromGraph: this.buildDependencyGraph(conn.fromElement)
        });
        });

        const condConn = AppState.connections.find(c =>
            c.toElement === elementId && c.toPort === 'cond-0'
        );
        if (condConn) {
            graph.condInput = {
                conn: condConn,
                fromGraph: this.buildDependencyGraph(condConn.fromElement)
            };
        }

        return graph;
    },

    /**
     * Получить ЛОГИКУ из графа (для IF/AND/OR/NOT/SEPARATOR)
     */
    evalLogic(graph) {
        if (!graph) return Optimizer.TrueCond;
        const elem = graph.elem;

        switch (elem.type) {
            case 'if': {
                const left = graph.inputs[0]?.fromGraph;
                const right = graph.inputs[1]?.fromGraph;

                const leftVal = left ? this.evalValue(left) : Optimizer.Const(0);
                const rightVal = right ? this.evalValue(right) : Optimizer.Const(0);

                const op = elem.props.operator || '=';
                return this.buildIfLogic(leftVal, op, rightVal);
            }

            case 'and': {
                let result = null;
                for (const inp of graph.inputs) {
                    const inLogic = this.evalLogic(inp.fromGraph);
                    result = result ? Optimizer.And(result, inLogic) : inLogic;
                }
                return result || Optimizer.TrueCond;
            }
            // codegen_graph.js -> evalLogic(graph)

            case 'range': {
                const inputGraph = graph.inputs[0]?.fromGraph;
                if (!inputGraph) return Optimizer.TrueCond; // нет входа — считаем TRUE, либо FALSE, по вкусу

                const inputVal = this.evalValue(inputGraph); // Optimizer.Expr для A
                //const name = (inputVal.type === 'var') ? inputVal.name : String(inputVal.n);
                const name = this.exprToStr(inputVal);

                const minVal = graph.elem.props?.minValue;
                const maxVal = graph.elem.props?.maxValue;
                const incMin = graph.elem.props?.inclusiveMin !== false;
                const incMax = graph.elem.props?.inclusiveMax !== false;

                const minStr = String(minVal ?? 0);
                const maxStr = String(maxVal ?? minStr);

                const conds = [];

                // A >= min  или  A > min
                if (minVal !== undefined && minVal !== null) {
                    const opMin = incMin ? '>=' : '>';
                    conds.push(Optimizer.Cmp(name, opMin, minStr));
                }

                // A <= max  или  A < max
                if (maxVal !== undefined && maxVal !== null) {
                    const opMax = incMax ? '<=' : '<';
                    conds.push(Optimizer.Cmp(name, opMax, maxStr));
                }

                if (conds.length === 0) {
                    return Optimizer.TrueCond; // диапазон без границ → всегда истина
                } else if (conds.length === 1) {
                    return conds[0];
                } else {
                    return Optimizer.And(conds[0], conds[1]);
                }
            }

            case 'or': {
                let result = null;
                for (const inp of graph.inputs) {
                    const inLogic = this.evalLogic(inp.fromGraph);
                    result = result ? Optimizer.Or(result, inLogic) : inLogic;
                }
                return result || Optimizer.FalseCond;
            }

            case 'not': {
                const inLogic = this.evalLogic(graph.inputs[0]?.fromGraph);
                return Optimizer.Not(inLogic);
            }

            case 'separator': {
                return this.evalLogic(graph.inputs[0]?.fromGraph);
            }

            default:
                return Optimizer.TrueCond;
        }
    },

    /**
     * Получить ЗНАЧЕНИЕ из графа (для INPUT/CONST/FORMULA)
     */
    evalValue(graph) {
        if (!graph) return Optimizer.Const(0);
        const elem = graph.elem;

        switch (elem.type) {
            case 'input-signal':
                return Optimizer.Var(elem.props.name || graph.nodeId);
            case 'table':
                return Optimizer.Var(elem.props.name || graph.nodeId);

            case 'const':
                return Optimizer.Const(Number(elem.props.value) || 0);

            case 'formula': {
                const expr = this.buildFormulaExpr(elem);
                return Optimizer.Var(expr);
            }

            case 'range':
            case 'and':
            case 'or':
            case 'not':
            case 'if': {
                // Для логических элементов и диапазона возвращаем переменную (ID элемента)
                // Это предотвращает подстановку константы 0 и позволяет генерировать корректные условия
                return Optimizer.Var(elem.id);
            }

            case 'separator':
                return this.evalValue(graph.inputs[0]?.fromGraph);

            case 'switch': {
                const exprCore = this.buildSwitchExpr(graph);
                const condCtx = this.collectAllCond(graph);
                return condCtx ? Optimizer.When(condCtx, exprCore, Optimizer.Const(0)) : exprCore;
            }

            default:
                return Optimizer.Const(0);
        }
    },

    // js/codegen_graph.js

    /**
     * Рекурсивно собрать полный контекст условий для элемента
     * через всю цепочку cond-портов вверх
     */
 // В codegen_graph.js, в evalFullContext добавь:

    evalFullContext(graph) {
        if (!graph) return null;

        let context = null;
        const elem = graph.elem;

        console.log(`evalFullContext для ${elem.id} (${elem.type})`);

        // 1. Если сам элемент имеет cond-порт — собираем его условие
        if (graph.condInput) {
            const condConn = graph.condInput.conn;
            console.log(`  → имеет cond-0 от ${graph.condInput.fromGraph.elem.id}.${condConn.fromPort}`);
            
            const condLogic = this.evalConditionFromPort(
                graph.condInput.fromGraph,
                condConn.fromPort
            );
            console.log(`  → условие от cond-0: ${Optimizer.printCond(condLogic)}`);
            context = condLogic;

            // 2. Рекурсивно собираем контекст элемента, на который указывает cond-порт
            const upstreamContext = this.evalFullContext(graph.condInput.fromGraph);
            if (upstreamContext) {
                console.log(`  → upstreamContext: ${Optimizer.printCond(upstreamContext)}`);
                context = context ? Optimizer.And(context, upstreamContext) : upstreamContext;
            }
        } else {
            console.log(`  → нет cond-0`);
        }

        console.log(`  → итоговый контекст: ${Optimizer.printCond(context)}`);
        return context;
    },

    /**
     * Получить УСЛОВИЕ для cond-порта элемента
     * Учитывает цепочку сепараторов с TRUE/FALSE ветвлением
     */
    evalConditionFromPort(graph, fromPort) {
        if (!graph) return null;
        const elem = graph.elem;

        // Если это сепаратор — вычисляем его вход и применяем ветвление
        if (elem.type === 'separator') {
            const inputLogic = this.evalLogic(graph.inputs[0]?.fromGraph);
            
            if (fromPort === 'out-0') {
                return inputLogic;
            } else if (fromPort === 'out-1') {
                return Optimizer.Not(inputLogic);
            }
        }

        // Если это логический элемент (AND/OR/NOT/IF) — просто вычисляем логику
        if (elem.type === 'and' || elem.type === 'or' || elem.type === 'not' || elem.type === 'if') {
            return this.evalLogic(graph);
        }

        return null;
    },

    /**
     * Главная функция: получить {cond, expr} для элемента
     */
    evalGraphValue(graph) {

        if (!graph) return { cond: null, expr: Optimizer.Const(0) };

        const elem = graph.elem;
        //let cond = null;

        // ← НОВОЕ: собираем полный контекст через цепочку cond-портов
        let cond = this.collectAllCond(graph);

        let expr = null;

        switch (elem.type) {
            case 'input-signal':
                expr = Optimizer.Var(elem.props.name || graph.nodeId);
                break;

            case 'const':
                expr = Optimizer.Const(Number(elem.props.value) || 0);
                break;

            case 'formula': {
                // Для формулы также собираем условия от всех входных элементов
                const inputConds = graph.inputs.map(inp => {
                    const inResult = this.evalGraphValue(inp.fromGraph);
                    return inResult.cond;
                }).filter(c => c);

                // Объединяем cond-порт с условиями от входов
                for (const inCond of inputConds) {
                    cond = cond ? Optimizer.And(cond, inCond) : inCond;
                }

                expr = Optimizer.Var(this.buildFormulaExpr(elem));
                break;
            }

            case 'separator':
                // Сепаратор — просто пробрасываем значение дальше
                return this.evalGraphValue(graph.inputs[0]?.fromGraph);

            // Логические элементы не должны здесь быть
            case 'and':
            case 'or':
            case 'not':
            case 'if':
            // codegen_graph.js -> evalGraphValue(graph)

            // codegen_graph.js -> evalGraphValue(graph)

            case 'switch': {
                const exprCore = this.buildSwitchExpr(graph);
                const condCtx = this.collectAllCond(graph);
                return { cond: condCtx, expr: exprCore };
                
                //#return { cond: condCtx, expr };
            }
            default:
                    expr = Optimizer.Const(0);
        }

        return { cond, expr };
    },

    exprToStr(expr) {
        if (!expr) return '0';
        if (expr.type === 'var') return expr.name;
        if (expr.type === 'const') return String(expr.n);
        return Optimizer.printExpr(expr);
    },

    buildIfLogic(leftVal, op, rightVal) {
        const leftIsVar = leftVal?.type === 'var';
        const rightIsVar = rightVal?.type === 'var';
        const leftIsConst = leftVal?.type === 'const';
        const rightIsConst = rightVal?.type === 'const';

        const leftStr = this.exprToStr(leftVal);
        const rightStr = this.exprToStr(rightVal);

        const leftZero = leftIsConst && leftVal.n === 0;
        const rightZero = rightIsConst && rightVal.n === 0;

        switch (op) {
            case '=':
                if (rightZero && leftIsVar) return Optimizer.Eq0(leftStr);
                if (leftZero && rightIsVar) return Optimizer.Eq0(rightStr);
                return Optimizer.Cmp(leftStr, '=', rightStr);
            case '!=':
                if (rightZero && leftIsVar) return Optimizer.Ne0(leftStr);
                if (leftZero && rightIsVar) return Optimizer.Ne0(rightStr);
                return Optimizer.Cmp(leftStr, '!=', rightStr);
            case '>':
            case '<':
            case '>=':
            case '<=':
                return Optimizer.Cmp(leftStr, op, rightStr);
            default:
                return Optimizer.TrueCond;
        }
    },
    

    buildFormulaExpr(elem) {
        let result = elem.props.expression || '0';

        // 1) Сначала раскрываем шаблоны (h и др.)
        const map = (typeof Settings !== 'undefined' && Settings.getTemplatesMap)
            ? Settings.getTemplatesMap()
            : null;
        result = expandFormulaTemplates(result, map);

        // 2) Потом раскрываем ссылки на формулы
        const formulaRefs = result.match(/formula[_-]\d+/g) || [];
        for (const ref of formulaRefs) {
            const refElem = AppState.elements[ref];
            if (refElem && refElem.type === 'formula') {
                const refExpr = this.buildFormulaExpr(refElem);
                result = result.replace(new RegExp(ref, 'g'), `(${refExpr})`);
            }
        }
        result = this.expandElementRefs(result, elem.id);

        return result;
    }
};

window.CodeGenGraph = CodeGenGraph;