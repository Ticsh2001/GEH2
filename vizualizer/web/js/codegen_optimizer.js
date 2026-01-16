// js/codegen_optimizer.js

let _depth = 0;
const MAX_DEPTH = 200;

// === Конструкторы ===
function Eq0(v) { return { kind: 'cond', type: 'eq0', v }; }
function Ne0(v) { return { kind: 'cond', type: 'ne0', v }; }
function Cmp(l, op, r) { return { kind: 'cond', type: 'cmp', l, op, r }; }
function And(a, b) {
    if (!a) return b;
    if (!b) return a;
    return { kind: 'cond', type: 'and', a, b };
}
function Or(a, b) {
    if (!a) return b;
    if (!b) return a;
    return { kind: 'cond', type: 'or', a, b };
}
function Not(x) {
    if (!x) return null;
    return { kind: 'cond', type: 'not', x };
}
const TrueCond = { kind: 'cond', type: 'true' };
const FalseCond = { kind: 'cond', type: 'false' };

function Const(n) { return { kind: 'expr', type: 'const', n }; }
function Var(name) { return { kind: 'expr', type: 'var', name }; }
function Op(op, l, r) { return { kind: 'expr', type: 'op', op, l, r }; }
function When(c, t, e) { return { kind: 'expr', type: 'when', c, t, e }; }

// === Утилиты ===
function atomKey(c) {
    if (!c) return null;
    switch (c.type) {
        case 'eq0': return `eq0:${c.v}`;
        case 'ne0': return `ne0:${c.v}`;
        case 'cmp': return `cmp:${c.l}:${c.op}:${c.r}`;
        case 'true': return 'true';
        case 'false': return 'false';
        default: return null;
    }
}

function negateOp(op) {
    switch (op) {
        case '=': return '!=';
        case '!=': return '=';
        case '>': return '<=';
        case '<': return '>=';
        case '>=': return '<';
        case '<=': return '>';
        default: return null;
    }
}

// Преобразует cmp-условие в интервал по одной переменной
// Возвращает { varName, min, minInc, max, maxInc } или null
function cmpToInterval(c) {
    if (!c || c.type !== 'cmp') return null;

    const lNum = parseNumberLiteral(c.l);
    const rNum = parseNumberLiteral(c.r);

    let varName, op, val;

    if (lNum == null && rNum != null) {
        // var OP const
        varName = c.l;
        op = c.op;
        val = rNum;
    } else if (lNum != null && rNum == null) {
        // const OP var  ->  var (OP') const
        varName = c.r;
        op = reverseOp(c.op);
        if (!op) return null;
        val = lNum;
    } else {
        // Либо обе стороны числа, либо обе не числа — не трогаем
        return null;
    }

    // Интересуют только упорядочивающие операторы
    switch (op) {
        case '<':
        case '<=':
        case '>':
        case '>=':
        case '=':
            break;
        default:
            return null;
    }

    let min = Number.NEGATIVE_INFINITY;
    let max = Number.POSITIVE_INFINITY;
    let minInc = false;
    let maxInc = false;

    switch (op) {
        case '<':
            max = val; maxInc = false; break;
        case '<=':
            max = val; maxInc = true; break;
        case '>':
            min = val; minInc = false; break;
        case '>=':
            min = val; minInc = true; break;
        case '=':
            min = val; minInc = true;
            max = val; maxInc = true;
            break;
    }

    return { varName, min, minInc, max, maxInc };
}

function intervalSubset(a, b) {
    if (!a || !b) return false;

    // Нижняя граница: a.min >= b.min
    const amin = a.min, bmin = b.min;
    if (amin === Number.NEGATIVE_INFINITY) {
        if (bmin !== Number.NEGATIVE_INFINITY) return false;
        // оба -∞ — ок
    } else if (bmin === Number.NEGATIVE_INFINITY) {
        // b начинается “раньше” — ок
    } else if (amin > bmin) {
        // a стартует правее b — ок
    } else if (amin < bmin) {
        // a захватывает меньшее значение — не подмножество
        return false;
    } else {
        // amin === bmin
        if (a.minInc && !b.minInc) {
            // a включает границу, а b — нет → в a есть точка, не входящая в b
            return false;
        }
    }

    // Верхняя граница: a.max <= b.max
    const amax = a.max, bmax = b.max;
    if (amax === Number.POSITIVE_INFINITY) {
        if (bmax !== Number.POSITIVE_INFINITY) return false;
    } else if (bmax === Number.POSITIVE_INFINITY) {
        // b идёт дальше — ок
    } else if (amax < bmax) {
        // a заканчивается раньше — ок
    } else if (amax > bmax) {
        return false;
    } else {
        // amax === bmax
        if (a.maxInc && !b.maxInc) {
            return false;
        }
    }

    return true;
}

// Удаляет избыточные cmp-условия в массиве атомов
// mode: 'and' | 'or'
function removeRedundantCmpAtoms(atoms, mode) {
    if (!atoms || atoms.length < 2) return atoms;

    const keep = new Array(atoms.length).fill(true);

    for (let i = 0; i < atoms.length; i++) {
        if (!keep[i]) continue;
        const a = atoms[i];
        if (!a || a.type !== 'cmp') continue;

        for (let j = 0; j < atoms.length; j++) {
            if (i === j || !keep[j]) continue;
            const b = atoms[j];
            if (!b || b.type !== 'cmp') continue;

            const rel = cmpImplicationRelation(a, b);
            if (!rel) continue;

            if (rel === 'a_in_b') {
                if (mode === 'or') {
                    // A ⊆ B → A OR B = B  → A лишнее
                    keep[i] = false;
                    break;
                } else if (mode === 'and') {
                    // A ⊆ B → A AND B = A → B лишнее
                    keep[j] = false;
                }
            } else if (rel === 'b_in_a') {
                if (mode === 'or') {
                    // B ⊆ A → A OR B = A → B лишнее
                    keep[j] = false;
                } else if (mode === 'and') {
                    // B ⊆ A → A AND B = B → A лишнее
                    keep[i] = false;
                    break;
                }
            }
        }
    }

    return atoms.filter((_, idx) => keep[idx]);
}

// Отношение между двумя cmp-условиями через интервалы
// 'a_in_b'  — A ⊆ B
// 'b_in_a'  — B ⊆ A
// 'equal'   — одинаковые интервалы (редко используем)
// null      — не можем определить
function cmpImplicationRelation(c1, c2) {
    const i1 = cmpToInterval(c1);
    const i2 = cmpToInterval(c2);
    if (!i1 || !i2) return null;
    if (i1.varName !== i2.varName) return null;

    const aInB = intervalSubset(i1, i2);
    const bInA = intervalSubset(i2, i1);

    if (aInB && bInA) return 'equal';
    if (aInB) return 'a_in_b';
    if (bInA) return 'b_in_a';
    return null;
}

// Разворот оператора при перестановке аргументов (левый/правый)
function reverseOp(op) {
    switch (op) {
        case '<':  return '>';
        case '>':  return '<';
        case '<=': return '>=';
        case '>=': return '<=';
        case '=':
        case '!=':
            return op;
        default:
            return null;
    }
}

// Аккуратный парсер числового литерала.
// Возвращает число или null, если строка не чисто числовая.
function parseNumberLiteral(s) {
    if (typeof s !== 'string') return null;
    const trimmed = s.trim().replace(',', '.');

    // Только простые вещи: -123, 45, 3.14
    if (!/^-?\d+(\.\d+)?$/.test(trimmed)) return null;

    const n = Number(trimmed);
    return Number.isFinite(n) ? n : null;
}


function negateAtomKey(key) {
    if (!key) return null;
    if (key.startsWith('eq0:')) return 'ne0:' + key.slice(4);
    if (key.startsWith('ne0:')) return 'eq0:' + key.slice(4);
    if (key.startsWith('cmp:')) {
        const parts = key.slice(4).split(':');
        if (parts.length === 3) {
            const negOp = negateOp(parts[1]);
            if (negOp) return `cmp:${parts[0]}:${negOp}:${parts[2]}`;
        }
    }
    return null;
}

function isNegation(a, b) {
    if (!a || !b) return false;
    if (a.type === 'eq0' && b.type === 'ne0' && a.v === b.v) return true;
    if (a.type === 'ne0' && b.type === 'eq0' && a.v === b.v) return true;
    if (a.type === 'cmp' && b.type === 'cmp' && a.l === b.l && a.r === b.r) {
        return a.op === negateOp(b.op);
    }
    if (a.type === 'not' && condEq(a.x, b)) return true;
    if (b.type === 'not' && condEq(b.x, a)) return true;
    return false;
}

function isAtomCond(t) {
    return t && (t.type === 'eq0' || t.type === 'ne0' || t.type === 'cmp');
}

function pruneOrByContext(orTerm, contextAtoms) {
    const branches = flattenOr(orTerm);
    const kept = [];

    for (const br of branches) {
        let contradicts = false;

        for (const ctx of contextAtoms) {
            if (isNegation(br, ctx)) {
                contradicts = true;
                break;
            }
        }

        if (!contradicts) kept.push(br);
    }

    if (kept.length === 0) return FalseCond;
    if (kept.length === 1) return kept[0];
    return buildOr(kept);
}

function condEq(a, b) {
    if (a === b) return true;
    if (!a || !b) return false;
    if (a.type !== b.type) return false;

    switch (a.type) {
        case 'eq0':
        case 'ne0':
            return a.v === b.v;
        case 'cmp':
            return a.l === b.l && a.op === b.op && a.r === b.r;
        case 'true':
        case 'false':
            return true;
        case 'not':
            return condEq(a.x, b.x);
        case 'and':
        case 'or':
            return (condEq(a.a, b.a) && condEq(a.b, b.b)) ||
                   (condEq(a.a, b.b) && condEq(a.b, b.a));
        default:
            return false;
    }
}

function flattenAnd(c) {
    if (!c) return [];
    if (c.type === 'and') return [...flattenAnd(c.a), ...flattenAnd(c.b)];
    return [c];
}

function flattenOr(c) {
    if (!c) return [];
    if (c.type === 'or') return [...flattenOr(c.a), ...flattenOr(c.b)];
    return [c];
}

function buildAnd(terms) {
    if (terms.length === 0) return TrueCond;
    let result = terms[0];
    for (let i = 1; i < terms.length; i++) {
        result = And(result, terms[i]);
    }
    return result;
}

function buildOr(terms) {
    if (terms.length === 0) return FalseCond;
    let result = terms[0];
    for (let i = 1; i < terms.length; i++) {
        result = Or(result, terms[i]);
    }
    return result;
}

// Поглощение для AND: X AND (X OR Y) = X
function applyAndAbsorption(terms) {
    if (!terms || terms.length < 2) return terms;

    const keep = new Array(terms.length).fill(true);

    for (let i = 0; i < terms.length; i++) {
        if (!keep[i]) continue;
        const ti = terms[i];
        if (!ti || ti.type !== 'or') continue;

        const orParts = flattenOr(ti);
        let drop = false;

        outer:
        for (const part of orParts) {
            for (let j = 0; j < terms.length; j++) {
                if (j === i || !keep[j]) continue;
                if (condEq(part, terms[j])) {
                    drop = true;
                    break outer;
                }
            }
        }

        if (drop) {
            keep[i] = false;
        }
    }

    return terms.filter((_, idx) => keep[idx]);
}

// Поглощение для OR: X OR (X AND Y) = X
function applyOrAbsorption(terms) {
    if (!terms || terms.length < 2) return terms;

    const keep = new Array(terms.length).fill(true);

    for (let i = 0; i < terms.length; i++) {
        if (!keep[i]) continue;
        const ti = terms[i];
        if (!ti || ti.type !== 'and') continue;

        const andParts = flattenAnd(ti);
        let drop = false;

        outer:
        for (const part of andParts) {
            for (let j = 0; j < terms.length; j++) {
                if (j === i || !keep[j]) continue;
                if (condEq(part, terms[j])) {
                    drop = true;
                    break outer;
                }
            }
        }

        if (drop) {
            keep[i] = false;
        }
    }

    return terms.filter((_, idx) => keep[idx]);
}

// === Упрощение условий ===
function simplifyCond(c) {
    _depth++;
    if (_depth > MAX_DEPTH) {
        _depth--;
        return c;
    }

    try {
        return simplifyCondCore(c);
    } finally {
        _depth--;
    }
}

function simplifyCondCore(c) {
    if (!c || c.kind !== 'cond') return c;

    switch (c.type) {
        case 'true':
        case 'false':
        case 'eq0':
        case 'ne0':
        case 'cmp':
            return c;

        case 'not': {
            const x = simplifyCondCore(c.x);
            if (!x) return TrueCond;
            if (x.type === 'true') return FalseCond;
            if (x.type === 'false') return TrueCond;
            if (x.type === 'not') return simplifyCondCore(x.x);
            if (x.type === 'eq0') return Ne0(x.v);
            if (x.type === 'ne0') return Eq0(x.v);
            if (x.type === 'cmp') {
                const negOp = negateOp(x.op);
                if (negOp) return Cmp(x.l, negOp, x.r);
            }
            if (x.type === 'and') return simplifyCondCore(Or(Not(x.a), Not(x.b)));
            if (x.type === 'or') return simplifyCondCore(And(Not(x.a), Not(x.b)));
            return Not(x);
        }

case 'and': {
    const a = simplifyCondCore(c.a);
    const b = simplifyCondCore(c.b);

    if (!a) return b;
    if (!b) return a;
    if (a.type === 'false' || b.type === 'false') return FalseCond;
    if (a.type === 'true') return b;
    if (b.type === 'true') return a;

    const allTerms = [...flattenAnd(a), ...flattenAnd(b)];
    
    // === НОВОЕ: Сразу собираем все eq0/ne0 для быстрой проверки ===
    const eq0Vars = new Map(); // var -> term
    const ne0Vars = new Map(); // var -> term
    const cmpTerms = [];
    const otherTerms = [];
    
    for (const t of allTerms) {
        if (t.type === 'true') continue;
        if (t.type === 'false') return FalseCond;
        
        if (t.type === 'eq0') {
            // Проверка на противоречие сразу
            if (ne0Vars.has(t.v)) {
                console.log(`Противоречие найдено: ${t.v} = 0 AND ${t.v} != 0`);
                return FalseCond;
            }
            eq0Vars.set(t.v, t);
        } else if (t.type === 'ne0') {
            // Проверка на противоречие сразу
            if (eq0Vars.has(t.v)) {
                console.log(`Противоречие найдено: ${t.v} != 0 AND ${t.v} = 0`);
                return FalseCond;
            }
            ne0Vars.set(t.v, t);
        } else if (t.type === 'cmp') {
            cmpTerms.push(t);
        } else if (t.type === 'or') {
            // === НОВОЕ: Проверяем каждую ветку OR на противоречие с контекстом ===
            const orTerms = flattenOr(t);
            const validBranches = [];
            
            for (const branch of orTerms) {
                let branchValid = true;
                
                if (branch.type === 'ne0' && eq0Vars.has(branch.v)) {
                    console.log(`OR ветка ${branch.v} != 0 противоречит контексту ${branch.v} = 0`);
                    branchValid = false;
                } else if (branch.type === 'eq0' && ne0Vars.has(branch.v)) {
                    console.log(`OR ветка ${branch.v} = 0 противоречит контексту ${branch.v} != 0`);
                    branchValid = false;
                }
                
                if (branchValid) {
                    validBranches.push(branch);
                }
            }
            
            if (validBranches.length === 0) {
                console.log(`Все ветки OR противоречат контексту → FALSE`);
                return FalseCond;
            } else if (validBranches.length === 1) {
                // Если осталась только одна ветка OR, добавляем её напрямую
                const singleBranch = validBranches[0];
                if (singleBranch.type === 'eq0') {
                    if (ne0Vars.has(singleBranch.v)) return FalseCond;
                    eq0Vars.set(singleBranch.v, singleBranch);
                } else if (singleBranch.type === 'ne0') {
                    if (eq0Vars.has(singleBranch.v)) return FalseCond;
                    ne0Vars.set(singleBranch.v, singleBranch);
                } else {
                    otherTerms.push(singleBranch);
                }
            } else {
                // Перестраиваем OR только с валидными ветками
                otherTerms.push(buildOr(validBranches));
            }
        } else {
            otherTerms.push(t);
        }
    }
    
    // Собираем уникальные атомы
    const atomMap = new Map();
    
    for (const [v, term] of eq0Vars) {
        const key = atomKey(term);
        if (key) atomMap.set(key, term);
    }
    
    for (const [v, term] of ne0Vars) {
        const key = atomKey(term);
        if (key) atomMap.set(key, term);
    }
    
    for (const term of cmpTerms) {
        const key = atomKey(term);
        if (key) {
            const negKey = negateAtomKey(key);
            if (negKey && atomMap.has(negKey)) {
                return FalseCond;
            }
            if (!atomMap.has(key)) {
                atomMap.set(key, term);
            }
        }
    }
    
    let uniqueAtoms = Array.from(atomMap.values());
    uniqueAtoms = removeRedundantCmpAtoms(uniqueAtoms, 'and');
    
    let result = [...uniqueAtoms, ...otherTerms];
    
    // Поглощение: X AND (X OR Y) = X
    // === НОВОЕ: выбрасываем из OR ветки, противоречащие контексту AND ===
    const contextAtoms = result.filter(t => isAtomCond(t));
    result = result.map(t => {
        if (t.type !== 'or') return t;
        return pruneOrByContext(t, contextAtoms);
    }).filter(t => t.type !== 'true'); // на всякий случай
    
    result = applyAndAbsorption(result);
    
    if (result.length === 0) return TrueCond;
    if (result.length === 1) return result[0];
    
    return buildAnd(result);
}

        case 'or': {
            const a = simplifyCondCore(c.a);
            const b = simplifyCondCore(c.b);

            if (!a) return b;
            if (!b) return a;
            if (a.type === 'true' || b.type === 'true') return TrueCond;
            if (a.type === 'false') return b;
            if (b.type === 'false') return a;

            const allTerms = [...flattenOr(a), ...flattenOr(b)];
            const atomMap = new Map();
            const otherTerms = [];

            for (const t of allTerms) {
                if (t.type === 'true') return TrueCond;
                if (t.type === 'false') continue;

                const key = atomKey(t);
                if (key) {
                    const negKey = negateAtomKey(key);
                    if (negKey && atomMap.has(negKey)) {
                        return TrueCond;
                    }
                    if (!atomMap.has(key)) {
                        atomMap.set(key, t);
                    }
                } else {
                    otherTerms.push(t);
                }
            }

            let uniqueAtoms = Array.from(atomMap.values());
            uniqueAtoms = removeRedundantCmpAtoms(uniqueAtoms, 'or');

            let result = [...uniqueAtoms, ...otherTerms];

            // Поглощение: X OR (X AND Y) = X
            result = applyOrAbsorption(result);

            if (result.length === 0) return FalseCond;
            if (result.length === 1) return result[0];

            return buildOr(result);
        }

        default:
            return c;
    }
}

// === Сравнение выражений ===
function exprEq(a, b) {
    if (a === b) return true;
    if (!a && !b) return true;
    if (!a || !b) return false;
    if (a.type !== b.type) return false;

    switch (a.type) {
        case 'const': return a.n === b.n;
        case 'var': return a.name === b.name;
        case 'op': return a.op === b.op && exprEq(a.l, b.l) && exprEq(a.r, b.r);
        case 'when': return condEq(a.c, b.c) && exprEq(a.t, b.t) && exprEq(a.e, b.e);
        default: return false;
    }
}

// === Упрощение выражений ===
function simplifyExpr(expr) {
    _depth++;
    if (_depth > MAX_DEPTH) {
        _depth--;
        return expr;
    }

    try {
        return simplifyExprCore(expr);
    } finally {
        _depth--;
    }
}

function simplifyExprCore(expr) {
    if (!expr || expr.kind !== 'expr') return expr;

    switch (expr.type) {
        case 'const':
        case 'var':
            return expr;

        case 'op': {
            const l = simplifyExprCore(expr.l);
            const r = simplifyExprCore(expr.r);

            if (expr.op === '+') {
                if (r?.type === 'const' && r.n === 0) return l;
                if (l?.type === 'const' && l.n === 0) return r;
            }
            if (expr.op === '*') {
                if (l?.type === 'const' && l.n === 0) return Const(0);
                if (r?.type === 'const' && r.n === 0) return Const(0);
                if (l?.type === 'const' && l.n === 1) return r;
                if (r?.type === 'const' && r.n === 1) return l;
            }
            return Op(expr.op, l, r);
        }

        case 'when': {
            const c = simplifyCond(expr.c);
            const t = simplifyExprCore(expr.t);
            const e = simplifyExprCore(expr.e);

            if (c?.type === 'true') return t;
            if (c?.type === 'false') return e;
            if (exprEq(t, e)) return t;

            return When(c, t, e);
        }

        default:
            return expr;
    }
}

// === Печать ===
function printCond(c) {
    if (!c) return 'TRUE';

    switch (c.type) {
        case 'eq0': return `${c.v} = 0`;
        case 'ne0': return `${c.v} != 0`;
        case 'cmp': return `${c.l} ${c.op} ${c.r}`;
        case 'and': return `(${printCond(c.a)} AND ${printCond(c.b)})`;
        case 'or':  return `(${printCond(c.a)} OR ${printCond(c.b)})`;
        case 'not': return `NOT(${printCond(c.x)})`;
        case 'true': return 'TRUE';
        case 'false': return 'FALSE';
        default: return '?';
    }
}

function printExpr(e) {
    if (!e) return '0';

    switch (e.type) {
        case 'const': return String(e.n);
        case 'var': return e.name;
        case 'op': return `(${printExpr(e.l)}${e.op}${printExpr(e.r)})`;
        case 'when': return `WHEN(${printCond(e.c)}, ${printExpr(e.t)}, ${printExpr(e.e)})`;
        default: return '?';
    }
}

window.Optimizer = {
    Eq0, Ne0, Cmp, And, Or, Not, TrueCond, FalseCond,
    Const, Var, Op, When,
    simplifyCond, simplifyExpr, 
    printCond, printExpr,
    condEq, exprEq
};