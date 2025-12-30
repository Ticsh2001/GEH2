// js/codegen_graph.js

const CodeGenGraph = {
    buildDependencyGraph(elementId) {
        const graph = {
            nodeId: elementId,
            elem: AppState.elements[elementId],
            inputs: [],
            condInput: null,
        };

        if (!graph.elem) return null;

        const inConns = AppState.connections.filter(c => 
            c.toElement === elementId && c.toPort.startsWith('in-')
        );
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

            case 'const':
                return Optimizer.Const(Number(elem.props.value) || 0);

            case 'formula': {
                const expr = this.buildFormulaExpr(elem);
                return Optimizer.Var(expr);
            }

            case 'separator':
                return this.evalValue(graph.inputs[0]?.fromGraph);

            default:
                return Optimizer.Const(0);
        }
    },

    /**
     * Вычислить УСЛОВИЕ для cond-порта элемента
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
        let cond = null;

        // Если есть cond-порт — вычисляем условие от него
        if (graph.condInput) {
            const condConn = graph.condInput.conn;
            const fromPortLogic = this.evalConditionFromPort(
                graph.condInput.fromGraph,
                condConn.fromPort
            );
            cond = fromPortLogic;
        }

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
            // (они только как условия, а не как значения)
            case 'and':
            case 'or':
            case 'not':
            case 'if':
            default:
                expr = Optimizer.Const(0);
        }

        return { cond, expr };
    },

    buildIfLogic(leftVal, op, rightVal) {
        const leftName = leftVal.type === 'var' ? leftVal.name : String(leftVal.n);
        const rightName = rightVal.type === 'var' ? rightVal.name : String(rightVal.n);

        const leftZero = leftVal.type === 'const' && leftVal.n === 0;
        const rightZero = rightVal.type === 'const' && rightVal.n === 0;

        switch (op) {
            case '=':
                if (rightZero) return Optimizer.Eq0(leftName);
                if (leftZero) return Optimizer.Eq0(rightName);
                return Optimizer.Cmp(leftName, '=', rightName);
            case '!=':
                if (rightZero) return Optimizer.Ne0(leftName);
                if (leftZero) return Optimizer.Ne0(rightName);
                return Optimizer.Cmp(leftName, '!=', rightName);
            case '>':
            case '<':
            case '>=':
            case '<=':
                return Optimizer.Cmp(leftName, op, rightName);
            default:
                return Optimizer.TrueCond;
        }
    },

    buildFormulaExpr(elem) {
        let result = elem.props.expression || '0';
        const formulaRefs = result.match(/formula-\d+/g) || [];

        for (const ref of formulaRefs) {
            const refElem = AppState.elements[ref];
            if (refElem && refElem.type === 'formula') {
                const refExpr = this.buildFormulaExpr(refElem);
                result = result.replace(new RegExp(ref, 'g'), `(${refExpr})`);
            }
        }

        return result;
    }
};

window.CodeGenGraph = CodeGenGraph;