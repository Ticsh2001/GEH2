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
            const atomMap = new Map();
            const otherTerms = [];

            for (const t of allTerms) {
                if (t.type === 'true') continue;
                if (t.type === 'false') return FalseCond;

                const key = atomKey(t);
                if (key) {
                    const negKey = negateAtomKey(key);
                    if (negKey && atomMap.has(negKey)) {
                        return FalseCond;
                    }
                    if (!atomMap.has(key)) {
                        atomMap.set(key, t);
                    }
                } else {
                    otherTerms.push(t);
                }
            }

            const uniqueAtoms = Array.from(atomMap.values());
            const result = [...uniqueAtoms, ...otherTerms];

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

            const uniqueAtoms = Array.from(atomMap.values());
            const result = [...uniqueAtoms, ...otherTerms];

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