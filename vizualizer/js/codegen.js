const CodeGen = {

    generate() {
        const outputs = Object.values(AppState.elements).filter(e => e.type === 'output');
        if (outputs.length === 0)
            return "/* Нет выходов */";

        const variants = [];

        for (const out of outputs) {
            const conns = AppState.connections.filter(c =>
                c.toElement === out.id && c.toPort === "in-0"
            );

            for (const conn of conns) {
                const srcId = conn.fromElement;
                const node = this.resolveNode(srcId);
                if (!node || node.value == null) continue;

                variants.push(node);
            }
        }

        if (variants.length === 0)
            return "/* Нет вариантов результата */";

        const defaultValue = "0";
        const expr = this.buildNestedWhen(variants, defaultValue);
        return expr;
    },

    buildNestedWhen(variants, defaultValue) {
        if (variants.length === 0) return defaultValue;
        
        // Специальная обработка для двух вариантов с противоположными условиями
        if (variants.length === 2) {
            const v0 = variants[0];
            const v1 = variants[1];
            
            // Проверяем, являются ли условия противоположными (одно с NOT, другое без)
            const v0HasNot = v0.condition && v0.condition.startsWith('(NOT ');
            const v1HasNot = v1.condition && v1.condition.startsWith('(NOT ');
            
            if (!v0HasNot && v1HasNot) {
                // v0 = "X", v1 = "NOT X"
                const originalPart = v0.condition;
                const notPart = v1.condition;
                
                // Проверяем, что NOT часть действительно инверсия оригинальной
                if (notPart === `(NOT ${originalPart})`) {
                    // Это противоположные условия - упрощаем в один WHEN
                    return `WHEN(${originalPart}, ${v0.value}, ${v1.value})`;
                }
            } else if (v0HasNot && !v1HasNot) {
                // v0 = "NOT X", v1 = "X"
                const notPart = v0.condition;
                const originalPart = v1.condition;
                
                if (notPart === `(NOT ${originalPart})`) {
                    return `WHEN(${originalPart}, ${v1.value}, ${v0.value})`;
                }
            }
        }
        
        // Стандартная логика для остальных случаев
        let expr = defaultValue;
        for (let i = variants.length - 1; i >= 0; i--) {
            const v = variants[i];
            if (!v.condition || v.condition === "TRUE") {
                expr = v.value;
            } else {
                expr = `WHEN(${v.condition}, ${v.value}, ${expr})`;
            }
        }
        return expr;
    },

    resolveNode(id) {
        const elem = AppState.elements[id];
        if (!elem) return { condition: null, value: "0" };

        switch (elem.type) {
            case 'input-signal':
                return { condition: null, value: elem.props.name };

            case 'const':
                return this.resolveConst(id);

            case 'formula':
                return this.resolveFormulaNode(id);

            case 'if':
                return this.resolveIfNode(id);

            case 'and':
            case 'or':
                return this.resolveLogicGateNode(id, elem.type);

            case 'not':
                return this.resolveNotNode(id);

            case 'separator':
                return this.resolveSeparatorNode(id);

            default:
                return { condition: null, value: "0" };
        }
    },

    // ====== Вспомогательные функции ======

    getCondInput(id) {
        const conn = AppState.connections.find(c =>
            c.toElement === id && c.toPort === "cond-0"
        );
        if (!conn) return null;
        
        const sourceNode = this.resolveNode(conn.fromElement);
        const sourceElem = AppState.elements[conn.fromElement];
        
        // Специальная обработка для separator: out-1 это FALSE (инверсия)
        if (sourceElem && sourceElem.type === 'separator') {
            if (conn.fromPort === 'out-1') {
                // FALSE выход - инвертируем условие
                return {
                    condition: sourceNode.condition,
                    value: `(NOT ${sourceNode.value})`,
                    isFalsePort: true
                };
            }
        }
        
        return sourceNode;
    },

    getInput(id, portName) {
        const conn = AppState.connections.find(c =>
            c.toElement === id && c.toPort === portName
        );
        if (!conn) return null;
        return this.resolveNode(conn.fromElement);
    },

    mergeConditions(a, b) {
        if (!a && !b) return null;
        if (!a) return b;
        if (!b) return a;
        return `(${a}) AND (${b})`;
    },

    // ====== Специализация по типам ======

    resolveConst(id) {
        const elem = AppState.elements[id];
        const baseConditionNode = this.getCondInput(id);
        let condition = null;

        if (baseConditionNode) {
            const condExpr = baseConditionNode.value;
            condition = this.mergeConditions(baseConditionNode.condition, condExpr);
        }

        const value = String(elem.props.value);
        return { condition, value };
    },

    resolveFormulaNode(id) {
        const elem = AppState.elements[id];

        const inNode = this.getInput(id, 'in-0');
        let value;

        if (inNode && elem.props.inputCount === 1) {
            value = inNode.value;
        } else {
            value = elem.props.expression || "0";
        }

        const condNode = this.getCondInput(id);
        let condition = null;

        if (condNode) {
            const condExpr = condNode.value;
            condition = this.mergeConditions(condNode.condition, condExpr);
        }

        if (inNode && inNode.condition) {
            condition = this.mergeConditions(condition, inNode.condition);
        }

        return { condition, value };
    },

    resolveIfNode(id) {
        const elem = AppState.elements[id];

        const leftNode  = this.getInput(id, 'in-0');
        const rightNode = this.getInput(id, 'in-1');
        const op        = elem.props.operator || '=';

        const left  = leftNode  ? leftNode.value  : "0";
        const right = rightNode ? rightNode.value : "0";

        // ЕСЛИ теперь имеет один выход типа LOGIC
        const value = `${left} ${op} ${right}`;

        const condNode = this.getCondInput(id);
        let condition = null;

        if (condNode) {
            const condExpr = condNode.value;
            condition = this.mergeConditions(condNode.condition, condExpr);
        }

        return { condition, value };
    },

    resolveLogicGateNode(id, type) {
        const elem = AppState.elements[id];
        const inputCount = elem.props.inputCount || 2;

        const values = [];
        for (let i = 0; i < inputCount; i++) {
            const input = this.getInput(id, `in-${i}`);
            if (input) {
                values.push(input.value);
            } else {
                values.push("FALSE");
            }
        }

        const op = (type === 'and') ? "AND" : "OR";
        const value = `(${values.join(` ${op} `)})`;

        const condNode = this.getCondInput(id);
        let condition = null;

        if (condNode) {
            const condExpr = condNode.value;
            condition = this.mergeConditions(condNode.condition, condExpr);
        }

        // Объединяем условия от всех входов
        for (let i = 0; i < inputCount; i++) {
            const input = this.getInput(id, `in-${i}`);
            if (input && input.condition) {
                condition = this.mergeConditions(condition, input.condition);
            }
        }

        return { condition, value };
    },

    resolveNotNode(id) {
        const A = this.getInput(id, 'in-0');
        const aVal = A ? A.value : "FALSE";

        const value = `(NOT ${aVal})`;

        const condNode = this.getCondInput(id);
        let condition = null;

        if (condNode) {
            const condExpr = condNode.value;
            condition = this.mergeConditions(condNode.condition, condExpr);
        }

        if (A && A.condition) condition = this.mergeConditions(condition, A.condition);

        return { condition, value };
    },

    resolveSeparatorNode(id) {
        // Separator просто передаёт логический сигнал дальше
        const input = this.getInput(id, 'in-0');
        
        if (!input) {
            return { condition: null, value: "FALSE" };
        }

        // Возвращаем значение входа как есть
        // (true/false интерпретируются в зависимости от выходного порта)
        return { condition: input.condition, value: input.value };
    }
};