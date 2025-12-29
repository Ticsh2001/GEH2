// js/codegen.js

const CodeGen = {
    _cache: {},
    _branchCache: {},
    _resolveCache: {},

    reset() {
        this._cache = {};
        this._branchCache = {};
        this._resolveCache = {};
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
                result = result.replace(ref, `(${refExpr})`);
            }
        }
        
        return result;
    },

    // === Получить ЧИСТУЮ логику элемента (без учёта контекста) ===
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
                const leftZero = leftVal.type === 'const' && leftVal.n === 0;
                const rightZero = rightVal.type === 'const' && rightVal.n === 0;

                if (op === '=' || op === '!=') {
                    if (rightZero) {
                        const name = this.exprToName(leftVal);
                        logic = op === '=' ? Optimizer.Eq0(name) : Optimizer.Ne0(name);
                    } else if (leftZero) {
                        const name = this.exprToName(rightVal);
                        logic = op === '=' ? Optimizer.Eq0(name) : Optimizer.Ne0(name);
                    } else {
                        logic = Optimizer.TrueCond;
                    }
                } else {
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

        this._cache[cacheKey] = logic;
        return logic;
    },

    // === Получить значение (для input-signal, const) ===
    getValue(id) {
        const elem = AppState.elements[id];
        if (!elem) return Optimizer.Const(0);

        if (elem.type === 'input-signal') {
            return this.toExpr(elem.props.name || id);
        }
        if (elem.type === 'const') {
            return Optimizer.Const(Number(elem.props.value) || 0);
        }
        return Optimizer.Const(0);
    },

    // === Получить ПОЛНОЕ условие для ветки сепаратора ===
    getBranchCondition(sepId, fromPort) {
        const cacheKey = `${sepId}:${fromPort}`;
        if (cacheKey in this._branchCache) {
            return this._branchCache[cacheKey];
        }

        const sep = AppState.elements[sepId];
        if (!sep || sep.type !== 'separator') return null;

        // 1. Логика на входе этого сепаратора
        const inputLogic = this.getPureLogic(sepId);

        // 2. Контекст этого сепаратора (от его cond-0)
        const sepContext = this.getConditionFromPort(sepId);

        // 3. Логика ветки
        let branchLogic;
        if (fromPort === 'out-1') {
            branchLogic = inputLogic ? Optimizer.Not(inputLogic) : Optimizer.TrueCond;
        } else {
            branchLogic = inputLogic || Optimizer.TrueCond;
        }

        // 4. Полное условие = контекст AND логика_ветки
        let result;
        if (sepContext) {
            result = Optimizer.And(sepContext, branchLogic);
        } else {
            result = branchLogic;
        }

        this._branchCache[cacheKey] = result;
        return result;
    },

    // === Получить условие от cond-порта элемента ===
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

    // === Основная функция разрешения (для значений с условиями) ===
    resolve(id) {
        if (id in this._resolveCache) {
            return this._resolveCache[id];
        }

        const elem = AppState.elements[id];
        if (!elem) return null;

        let result = null;

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
                // Условие от cond-порта
                let cond = this.getConditionFromPort(id);
                
                // ВАЖНО: собираем условия от ВХОДОВ формулы (in-0, in-1, ...)
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

        this._resolveCache[id] = result;
        return result;
    },

    // === Генерация единого выражения ===
    generate() {
        console.log('=== Генерация кода ===');
        this.reset();

        try {
            const outputs = Object.values(AppState.elements).filter(e => e.type === 'output');
            console.log('Найдено выходов:', outputs.length);

            if (outputs.length === 0) {
                return '/* Нет выходов */';
            }

            const allVariants = [];

            for (const out of outputs) {
                const conns = this.getConns(out.id, 'in-');
                
                for (const conn of conns) {
                    const node = this.resolve(conn.fromElement);
                    if (!node || !node.isValue || !node.expr) continue;

                    const cond = node.cond ? Optimizer.simplifyCond(node.cond) : null;
                    const isZero = node.expr.type === 'const' && node.expr.n === 0;
                    
                    if (isZero && !cond) continue;

                    allVariants.push({
                        cond: cond,
                        expr: node.expr,
                        isZero: isZero,
                        source: conn.fromElement
                    });
                }
            }

            console.log('Всего вариантов:', allVariants.length);
            allVariants.forEach((v, i) => {
                console.log(`  ${i}: source=${v.source}, cond=${Optimizer.printCond(v.cond)}`);
            });

            if (allVariants.length === 0) {
                return '0';
            }

            const valueVariants = allVariants.filter(v => !v.isZero);
            
            console.log('Значимых вариантов:', valueVariants.length);

            if (valueVariants.length === 0) {
                return '0';
            }

            let result = Optimizer.Const(0);

            for (let i = valueVariants.length - 1; i >= 0; i--) {
                const v = valueVariants[i];
                if (v.cond) {
                    result = Optimizer.When(v.cond, v.expr, result);
                } else {
                    result = v.expr;
                }
            }

            console.log('До оптимизации:', Optimizer.printExpr(result));

            const simplified = Optimizer.simplifyExpr(result);
            
            console.log('После оптимизации:', Optimizer.printExpr(simplified));
            
            return Optimizer.printExpr(simplified);

        } catch (err) {
            console.error('Ошибка генерации:', err);
            return `/* Ошибка: ${err.message} */`;
        }
    }
};

window.CodeGen = CodeGen;