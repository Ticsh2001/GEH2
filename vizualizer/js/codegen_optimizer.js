// js/codegen_optimizer.js

let _depth = 0;
const MAX_DEPTH = 200;

// === Конструкторы ===
function Eq0(v) { return { kind: 'cond', type: 'eq0', v }; }
function Ne0(v) { return { kind: 'cond', type: 'ne0', v }; }
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
        case 'true': return 'true';
        case 'false': return 'false';
        default: return null;
    }
}

function negateAtom(c) {
    if (!c) return null;
    if (c.type === 'eq0') return Ne0(c.v);
    if (c.type === 'ne0') return Eq0(c.v);
    return null;
}

function negateAtomKey(key) {
    if (!key) return null;
    if (key.startsWith('eq0:')) return 'ne0:' + key.slice(4);
    if (key.startsWith('ne0:')) return 'eq0:' + key.slice(4);
    return null;
}

function isNegation(a, b) {
    if (!a || !b) return false;
    if (a.type === 'eq0' && b.type === 'ne0' && a.v === b.v) return true;
    if (a.type === 'ne0' && b.type === 'eq0' && a.v === b.v) return true;
    if (a.type === 'not' && atomEq(a.x, b)) return true;
    if (b.type === 'not' && atomEq(b.x, a)) return true;
    return false;
}

function atomEq(a, b) {
    if (a === b) return true;
    if (!a || !b) return false;
    if (a.type !== b.type) return false;
    if (a.type === 'eq0' || a.type === 'ne0') return a.v === b.v;
    if (a.type === 'true' || a.type === 'false') return true;
    return false;
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

// === Главная функция упрощения условий ===
function simplifyCond(c) {
    _depth++;
    if (_depth > MAX_DEPTH) {
        _depth--;
        return c;
    }
    
    try {
        const step1 = simplifyCondBasic(c);
        const step2 = optimizeAndChain(step1);
        return step2;
    } finally {
        _depth--;
    }
}

// === Базовое упрощение ===
function simplifyCondBasic(c) {
    if (!c || c.kind !== 'cond') return c;

    switch (c.type) {
        case 'true':
        case 'false':
        case 'eq0':
        case 'ne0':
            return c;

        case 'not': {
            const x = simplifyCondBasic(c.x);
            if (!x) return TrueCond;
            if (x.type === 'true') return FalseCond;
            if (x.type === 'false') return TrueCond;
            if (x.type === 'not') return simplifyCondBasic(x.x);
            if (x.type === 'eq0') return Ne0(x.v);
            if (x.type === 'ne0') return Eq0(x.v);
            if (x.type === 'and') return simplifyCondBasic(Or(Not(x.a), Not(x.b)));
            if (x.type === 'or') return simplifyCondBasic(And(Not(x.a), Not(x.b)));
            return Not(x);
        }

        case 'and': {
            const a = simplifyCondBasic(c.a);
            const b = simplifyCondBasic(c.b);
            if (!a) return b;
            if (!b) return a;
            if (a.type === 'false' || b.type === 'false') return FalseCond;
            if (a.type === 'true') return b;
            if (b.type === 'true') return a;
            if (atomEq(a, b)) return a;
            if (isNegation(a, b)) return FalseCond;
            return And(a, b);
        }

        case 'or': {
            const a = simplifyCondBasic(c.a);
            const b = simplifyCondBasic(c.b);
            if (!a) return b;
            if (!b) return a;
            if (a.type === 'true' || b.type === 'true') return TrueCond;
            if (a.type === 'false') return b;
            if (b.type === 'false') return a;
            if (atomEq(a, b)) return a;
            if (isNegation(a, b)) return TrueCond;
            return Or(a, b);
        }

        default:
            return c;
    }
}

// === Оптимизация AND-цепочки ===
function optimizeAndChain(c) {
    if (!c) return c;
    if (c.type === 'or') return optimizeOrChain(c);
    if (c.type !== 'and') return c;

    const allTerms = flattenAnd(c);
    const atoms = [];
    const atomSet = new Set();
    const orTerms = [];
    
    for (const t of allTerms) {
        const key = atomKey(t);
        if (key) {
            if (t.type === 'true') continue;
            if (t.type === 'false') return FalseCond;
            if (!atomSet.has(key)) {
                atomSet.add(key);
                atoms.push(t);
            }
        } else if (t.type === 'or') {
            orTerms.push(t);
        } else if (t.type === 'and') {
            const inner = optimizeAndChain(t);
            if (inner.type === 'false') return FalseCond;
            if (inner.type !== 'true') {
                const innerTerms = flattenAnd(inner);
                for (const it of innerTerms) {
                    const itKey = atomKey(it);
                    if (itKey && !atomSet.has(itKey)) {
                        atomSet.add(itKey);
                        atoms.push(it);
                    } else if (it.type === 'or') {
                        orTerms.push(it);
                    }
                }
            }
        }
    }

    for (let i = 0; i < atoms.length; i++) {
        for (let j = i + 1; j < atoms.length; j++) {
            if (isNegation(atoms[i], atoms[j])) return FalseCond;
        }
    }

    const simplifiedOrTerms = [];
    for (const orTerm of orTerms) {
        const simplified = simplifyOrWithKnownAtoms(orTerm, atoms, atomSet);
        if (simplified.type === 'false') return FalseCond;
        if (simplified.type === 'true') continue;
        
        const simplifiedKey = atomKey(simplified);
        if (simplifiedKey) {
            for (const a of atoms) {
                if (isNegation(a, simplified)) return FalseCond;
            }
            if (!atomSet.has(simplifiedKey)) {
                atomSet.add(simplifiedKey);
                atoms.push(simplified);
            }
            continue;
        }
        
        if (simplified.type === 'or') {
            const orParts = flattenOr(simplified);
            if (orParts.some(p => atomSet.has(atomKey(p)))) continue;
        }
        
        simplifiedOrTerms.push(simplified);
    }

    const result = [...atoms, ...simplifiedOrTerms];
    if (result.length === 0) return TrueCond;
    if (result.length === 1) return result[0];
    
    let out = result[0];
    for (let i = 1; i < result.length; i++) {
        out = And(out, result[i]);
    }
    return out;
}

function simplifyOrWithKnownAtoms(orCond, knownAtoms, knownSet) {
    const orParts = flattenOr(orCond);
    const newParts = [];
    
    for (const part of orParts) {
        if (part.type === 'true') return TrueCond;
        if (part.type === 'false') continue;
        
        const partKey = atomKey(part);
        if (partKey && knownSet.has(partKey)) return TrueCond;
        
        let isContradicted = false;
        for (const known of knownAtoms) {
            if (isNegation(known, part)) {
                isContradicted = true;
                break;
            }
        }
        if (isContradicted) continue;
        
        newParts.push(part);
    }
    
    if (newParts.length === 0) return FalseCond;
    if (newParts.length === 1) return newParts[0];
    
    let out = newParts[0];
    for (let i = 1; i < newParts.length; i++) {
        out = Or(out, newParts[i]);
    }
    return out;
}

function optimizeOrChain(c) {
    if (!c || c.type !== 'or') return c;

    const terms = flattenOr(c);
    const atomSet = new Set();
    const atoms = [];

    for (const t of terms) {
        if (t.type === 'true') return TrueCond;
        if (t.type === 'false') continue;
        
        const key = atomKey(t);
        if (key && !atomSet.has(key)) {
            atomSet.add(key);
            atoms.push(t);
        }
    }

    for (let i = 0; i < atoms.length; i++) {
        for (let j = i + 1; j < atoms.length; j++) {
            if (isNegation(atoms[i], atoms[j])) return TrueCond;
        }
    }

    if (atoms.length === 0) return FalseCond;
    if (atoms.length === 1) return atoms[0];
    
    let out = atoms[0];
    for (let i = 1; i < atoms.length; i++) {
        out = Or(out, atoms[i]);
    }
    return out;
}

// === Сравнение ===
function condEq(x, y) {
    if (x === y) return true;
    if (!x && !y) return true;
    if (!x || !y) return false;
    if (x.type !== y.type) return false;
    
    switch (x.type) {
        case 'eq0':
        case 'ne0': 
            return x.v === y.v;
        case 'true':
        case 'false': 
            return true;
        case 'not': 
            return condEq(x.x, y.x);
        case 'and':
        case 'or': 
            return (condEq(x.a, y.a) && condEq(x.b, y.b)) ||
                   (condEq(x.a, y.b) && condEq(x.b, y.a));
        default: 
            return false;
    }
}

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
        const basic = simplifyExprBasic(expr);
        return optimizeNestedWhen(basic, []);
    } finally {
        _depth--;
    }
}

function simplifyExprBasic(expr) {
    if (!expr || expr.kind !== 'expr') return expr;
    
    switch (expr.type) {
        case 'const':
        case 'var':
            return expr;

        case 'op': {
            const l = simplifyExprBasic(expr.l);
            const r = simplifyExprBasic(expr.r);
            
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
            const t = simplifyExprBasic(expr.t);
            const e = simplifyExprBasic(expr.e);
            
            if (c?.type === 'true') return t;
            if (c?.type === 'false') return e;
            if (exprEq(t, e)) return t;

            return When(c, t, e);
        }

        default:
            return expr;
    }
}

// === Контекстная оптимизация вложенных WHEN ===
// previousConditions - список условий всех внешних WHEN (которые были FALSE)
function optimizeNestedWhen(expr, previousConditions) {
    if (!expr || expr.type !== 'when') return expr;
    
    const c = expr.c;
    const t = optimizeNestedWhen(expr.t, previousConditions);
    
    // Для ELSE ветки: добавляем текущее условие в контекст
    const newContext = [...previousConditions, c];
    let e = expr.e;
    
    if (e && e.type === 'when') {
        // Оптимизируем условие вложенного WHEN с учётом контекста
        const optimizedInnerC = simplifyCondWithContext(e.c, newContext);
        
        if (optimizedInnerC?.type === 'true') {
            e = optimizeNestedWhen(e.t, newContext);
        } else if (optimizedInnerC?.type === 'false') {
            e = optimizeNestedWhen(e.e, newContext);
        } else {
            const innerT = optimizeNestedWhen(e.t, newContext);
            const innerE = optimizeNestedWhen(e.e, newContext);
            
            if (exprEq(innerT, innerE)) {
                e = innerT;
            } else {
                e = When(optimizedInnerC, innerT, innerE);
            }
        }
    } else {
        e = optimizeNestedWhen(e, newContext);
    }
    
    if (exprEq(t, e)) return t;
    
    return When(c, t, e);
}

// === Упростить условие с учётом контекста предыдущих WHEN ===
// Контекст: список условий, которые были FALSE (мы в их ELSE ветках)
function simplifyCondWithContext(cond, previousConditions) {
    if (!cond || previousConditions.length === 0) return simplifyCond(cond);
    
    const innerAtoms = flattenAnd(cond);
    const innerAtomMap = new Map(); // key -> atom
    
    for (const a of innerAtoms) {
        const key = atomKey(a);
        if (key) innerAtomMap.set(key, a);
    }
    
    // Для каждого предыдущего условия проверяем, что отличается
    for (const prevCond of previousConditions) {
        const prevAtoms = flattenAnd(prevCond);
        const prevAtomSet = new Set();
        
        for (const a of prevAtoms) {
            const key = atomKey(a);
            if (key) prevAtomSet.add(key);
        }
        
        // Ищем "отличающийся" атом - тот, который в inner является отрицанием атома из prev
        // Пример: prev = A AND B AND C, inner = A AND B AND NOT(C)
        // Отличие: C vs NOT(C), значит остальные (A AND B) можно убрать
        
        let differingAtom = null;
        let commonCount = 0;
        
        for (const [innerKey, innerAtom] of innerAtomMap) {
            const negKey = negateAtomKey(innerKey);
            
            if (prevAtomSet.has(innerKey)) {
                // Этот атом одинаковый
                commonCount++;
            } else if (negKey && prevAtomSet.has(negKey)) {
                // Этот атом - отрицание атома из prev
                differingAtom = innerAtom;
            }
        }
        
        // Если нашли отличающийся атом и есть общие части
        if (differingAtom && commonCount > 0) {
            // Убираем общие атомы, оставляем только отличающийся
            const remainingAtoms = [];
            
            for (const [innerKey, innerAtom] of innerAtomMap) {
                // Оставляем атом если:
                // 1. Он отличающийся (отрицание чего-то из prev)
                // 2. Его нет в prev (новый атом)
                if (!prevAtomSet.has(innerKey)) {
                    remainingAtoms.push(innerAtom);
                }
            }
            
            if (remainingAtoms.length === 0) {
                return TrueCond;
            }
            
            const newCond = buildAnd(remainingAtoms);
            return simplifyCond(newCond);
        }
    }
    
    return simplifyCond(cond);
}

// === Печать ===
function printCond(c) {
    if (!c) return 'TRUE';
    
    switch (c.type) {
        case 'eq0': return `${c.v} = 0`;
        case 'ne0': return `${c.v} != 0`;
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
    Eq0, Ne0, And, Or, Not, TrueCond, FalseCond,
    Const, Var, Op, When,
    simplifyCond, simplifyExpr, 
    printCond, printExpr,
    condEq, exprEq
};