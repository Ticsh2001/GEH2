// js/codegen.js

const CodeGen = {
    _cache: {},
    _branchCache: {},
    _resolveCache: {},
    _visiting: new Set(),

    reset() {
        this._cache = {};
        this._branchCache = {};
        this._resolveCache = {};
        this._visiting = new Set();
    },

    toExpr(valueStr) {
        const s = String(valueStr).trim();
        if (s === '0') return Optimizer.Const(0);
        const num = parseFloat(s);
        if (!isNaN(num) && String(num) === s) return Optimizer.Const(num);
        return Optimizer.Var(s);
    },

    exprToName(exprAst) {
        if (!exprAst) return '0';
        if (exprAst.type === 'var') return exprAst.name;
        if (exprAst.type === 'const') return String(exprAst.n);
        return Optimizer.printExpr(exprAst);
    },

    mergeCond(a, b) {
        if (!a && !b) return null;
        if (!a) return b;
        if (!b) return a;
        if (Optimizer.condEq && Optimizer.condEq(a, b)) return a;
        return Optimizer.And(a, b);
    },

    getConn(toId, toPort) {
        return AppState.connections.find(c => c.toElement === toId && c.toPort === toPort);
    },

    getConns(toId, prefix) {
        return AppState.connections.filter(c => c.toElement === toId && c.toPort.startsWith(prefix));
    },

    buildFormulaExpr(elem) {
        const expression = elem.props.expression || '0';
        let result = expression;
        const formulaRefs = result.match(/formula-\d+/g) || [];

        for (const ref of formulaRefs) {
            const refElem = AppState.elements[ref];
            if (refElem && refElem.type === 'formula') {
                const refExpr = this.buildFormulaExpr(refElem);
                result = result.replace(new RegExp(ref, 'g'), `(${refExpr})`);
            }
        }

        return result;
    },

    // === Получить ЧИСТУЮ логику элемента ===
    getPureLogic(id) {
        const cacheKey = `logic:${id}`;
        if (cacheKey in this._cache) {
            return this._cache[cacheKey];
        }

        const elem = AppState.elements[id];
        if (!elem) return null;

        let logic = null;

        switch (elem.type) {
            case 'if': {
                const leftConn = this.getConn(id, 'in-0');
                const rightConn = this.getConn(id, 'in-1');

                const leftVal = leftConn ? this.getValue(leftConn.fromElement) : Optimizer.Const(0);
                const rightVal = rightConn ? this.getValue(rightConn.fromElement) : Optimizer.Const(0);

                const op = (elem.props.operator || '=').trim();
                const leftName = this.exprToName(leftVal);
                const rightName = this.exprToName(rightVal);

                const leftZero = leftVal.type === 'const' && leftVal.n === 0;
                const rightZero = rightVal.type === 'const' && rightVal.n === 0;

                switch (op) {
                    case '=':
                        if (rightZero) {
                            logic = Optimizer.Eq0(leftName);
                        } else if (leftZero) {
                            logic = Optimizer.Eq0(rightName);
                        } else {
                            logic = Optimizer.Cmp(leftName, '=', rightName);
                        }
                        break;
                    case '!=':
                        if (rightZero) {
                            logic = Optimizer.Ne0(leftName);
                        } else if (leftZero) {
                            logic = Optimizer.Ne0(rightName);
                        } else {
                            logic = Optimizer.Cmp(leftName, '!=', rightName);
                        }
                        break;
                    case '>':
                    case '<':
                    case '>=':
                    case '<=':
                        logic = Optimizer.Cmp(leftName, op, rightName);
                        break;
                    default:
                        logic = Optimizer.TrueCond;
                }
                break;
            }

            case 'and':
            case 'or': {
                const isAnd = elem.type === 'and';
                const count = elem.props.inputCount || 2;
                let result = null;

                for (let i = 0; i < count; i++) {
                    const conn = this.getConn(id, `in-${i}`);
                    if (!conn) continue;

                    const val = this.getPureLogic(conn.fromElement);
                    if (!val) continue;

                    if (result === null) {
                        result = val;
                    } else {
                        result = isAnd ? Optimizer.And(result, val) : Optimizer.Or(result, val);
                    }
                }
                logic = result || Optimizer.FalseCond;
                break;
            }

            case 'not': {
                const conn = this.getConn(id, 'in-0');
                const inputLogic = conn ? this.getPureLogic(conn.fromElement) : null;
                logic = Optimizer.Not(inputLogic || Optimizer.FalseCond);
                break;
            }

            case 'separator': {
                const conn = this.getConn(id, 'in-0');
                logic = conn ? this.getPureLogic(conn.fromElement) : Optimizer.FalseCond;
                break;
            }

            default:
                logic = null;
        }

        // ↓ новая часть: добавляем контекст с cond-порта для логических элементов
        if (elem.type === 'if' || elem.type === 'and' || elem.type === 'or' || elem.type === 'not') {
            const ctx = this.getConditionFromPort(id);
            if (ctx) {
                if (logic) {
                    logic = Optimizer.And(ctx, logic);
                } else {
                    logic = ctx;
                }
            }
        }

        this._cache[cacheKey] = logic;
        return logic;
    },

    // === Получить значение ===
    getValue(id) {
        const elem = AppState.elements[id];
        if (!elem) return Optimizer.Const(0);

        switch (elem.type) {
            case 'input-signal':
                // Имя сигнала или id как Var(...)
                return this.toExpr(elem.props.name || id);

            case 'const':
                return Optimizer.Const(Number(elem.props.value) || 0);

            case 'formula': {
                // Используем текст формулы как выражение
                const exprStr = this.buildFormulaExpr(elem) || '0';
                return this.toExpr(exprStr);
            }

            default:
                // На всякий случай — даём символическое имя, а не 0
                if (elem.props && typeof elem.props.name === 'string') {
                    return this.toExpr(elem.props.name);
                }
                return this.toExpr(id);
        }
    },

    // === Получить ПОЛНОЕ условие для ветки сепаратора ===
    getBranchCondition(sepId, fromPort) {
        const cacheKey = `${sepId}:${fromPort}`;
        if (cacheKey in this._branchCache) {
            return this._branchCache[cacheKey];
        }

        const sep = AppState.elements[sepId];
        if (!sep || sep.type !== 'separator') return null;

        const inputLogic = this.getPureLogic(sepId);
        const sepContext = this.getConditionFromPort(sepId);

        let branchLogic;
        if (fromPort === 'out-1') {
            branchLogic = inputLogic ? Optimizer.Not(inputLogic) : Optimizer.TrueCond;
        } else {
            branchLogic = inputLogic || Optimizer.TrueCond;
        }

        let result;
        if (sepContext) {
            result = Optimizer.And(sepContext, branchLogic);
        } else {
            result = branchLogic;
        }

        this._branchCache[cacheKey] = result;
        return result;
    },

    // === Получить условие от cond-порта ===
    getConditionFromPort(id) {
        const conn = this.getConn(id, 'cond-0');
        if (!conn) return null;

        const sourceElem = AppState.elements[conn.fromElement];
        if (!sourceElem) return null;

        if (sourceElem.type === 'separator') {
            return this.getBranchCondition(conn.fromElement, conn.fromPort);
        }

        return this.getPureLogic(conn.fromElement);
    },

    // === Основная функция разрешения ===
    resolve(id) {
        if (id in this._resolveCache) {
            return this._resolveCache[id];
        }

        if (this._visiting.has(id)) {
            return null;
        }
        this._visiting.add(id);

        const elem = AppState.elements[id];
        if (!elem) {
            this._visiting.delete(id);
            return null;
        }

        let result = null;

        try {
            switch (elem.type) {
                case 'input-signal':
                    result = {
                        isValue: true,
                        cond: null,
                        expr: this.toExpr(elem.props.name || id)
                    };
                    break;

                case 'const': {
                    const cond = this.getConditionFromPort(id);
                    result = {
                        isValue: true,
                        cond: cond,
                        expr: Optimizer.Const(Number(elem.props.value) || 0)
                    };
                    break;
                }

                case 'formula': {
                    let cond = this.getConditionFromPort(id);

                    const inConns = this.getConns(id, 'in-');
                    for (const conn of inConns) {
                        const inputNode = this.resolve(conn.fromElement);
                        if (inputNode && inputNode.cond) {
                            cond = this.mergeCond(cond, inputNode.cond);
                        }
                    }

                    const fullExpr = this.buildFormulaExpr(elem);
                    result = {
                        isValue: true,
                        cond: cond,
                        expr: Optimizer.Var(fullExpr)
                    };
                    break;
                }

                default:
                    result = null;
            }
        } finally {
            this._visiting.delete(id);
        }

        this._resolveCache[id] = result;
        return result;
    },

    generate() {
        console.log('=== Генерация кода (граф) ===');
        this.reset();

        try {
            const outputs = Object.values(AppState.elements).filter(e => e.type === 'output');

            if (outputs.length === 0) {
                return '/* Нет выходов */';
            }

            const allVariants = [];

            for (const out of outputs) {
                const conns = this.getConns(out.id, 'in-');

                for (const conn of conns) {
                    const graph = CodeGenGraph.buildDependencyGraph(conn.fromElement);
                    const result = CodeGenGraph.evalGraphValue(graph);

                    if (!result || !result.expr) continue;

                    const cond = result.cond ? Optimizer.simplifyCond(result.cond) : null;
                    const isZero = result.expr.type === 'const' && result.expr.n === 0;

                    if (isZero && !cond) continue;

                    allVariants.push({
                        cond,
                        expr: result.expr,
                        isZero
                    });
                }
            }

            console.log('Варианты:', allVariants.map(v => ({
                cond: Optimizer.printCond(v.cond),
                expr: Optimizer.printExpr(v.expr)
            })));

            if (allVariants.length === 0) return '0';

            const valueVariants = allVariants.filter(v => !v.isZero);
            if (valueVariants.length === 0) return '0';

            let result = Optimizer.Const(0);

            for (let i = valueVariants.length - 1; i >= 0; i--) {
                const v = valueVariants[i];
                if (v.cond) {
                    result = Optimizer.When(v.cond, v.expr, result);
                } else {
                    result = v.expr;
                }
            }

            const simplified = Optimizer.simplifyExpr(result);
            return Optimizer.printExpr(simplified);

        } catch (err) {
            console.error('Ошибка:', err);
            return `/* Ошибка: ${err.message} */`;
        }
    }
};

window.CodeGen = CodeGen;