// acorn oracle: node --expose-internals oracle.js < corpus.jsonl > oracle.jsonl
// per record: {id, parse: "ok" | "error", err, calls: [{line, model, effort}], alias: n}
const acorn = require('internal/deps/acorn/acorn/dist/acorn');
const walk = require('internal/deps/acorn/acorn-walk/dist/walk');
const fs = require('fs');
const lines = fs.readFileSync(0, 'utf8').split('\n').filter(Boolean);
const OPTS = {ecmaVersion: 'latest', sourceType: 'module', allowAwaitOutsideFunction: true, allowReturnOutsideFunction: true, allowHashBang: true};
function lit(node) {
  if (!node) return 'missing';
  if (node.type === 'Literal' && typeof node.value === 'string') return node.value;
  if (node.type === 'TemplateLiteral' && node.expressions.length === 0) return node.quasis[0].value.cooked;
  return 'expr';
}
function keyName(p) {
  if (p.type !== 'Property') return null;           // SpreadElement
  if (p.computed) return 'computed';
  return p.key.type === 'Identifier' ? p.key.name : (p.key.type === 'Literal' ? String(p.key.value) : 'other');
}
for (const line of lines) {
  const rec = JSON.parse(line);
  const out = {id: rec.id, calls: [], alias: 0};
  let ast;
  try { ast = acorn.parse(rec.script, OPTS); out.parse = 'ok'; }
  catch (e) { out.parse = 'error'; out.err = String(e.message).slice(0, 80); process.stdout.write(JSON.stringify(out) + '\n'); continue; }
  walk.full(ast, (node) => {
    if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'agent') {
      const c = {line: node.loc ? node.loc.start.line : -1, model: 'missing', effort: 'missing', shape: 'object'};
      const o = node.arguments[1];
      if (!o) { c.shape = 'no-opts'; }
      else if (o.type !== 'ObjectExpression') { c.shape = 'non-object'; c.model = c.effort = 'expr'; }
      else {
        if (o.properties.some(p => p.type === 'SpreadElement')) c.shape = 'spread';
        for (const p of o.properties) {               // last one wins, as in JS
          const k = keyName(p);
          if (k === 'model') c.model = lit(p.value);
          if (k === 'effort') c.effort = lit(p.value);
          if (k === 'computed') c.shape = 'computed';
        }
      }
      out.calls.push(c);
    } else if (node.type === 'Identifier' && node.name === 'agent') {
      out.alias++;                                     // counted again below for callee identifiers; corrected in compare.py
    }
  });
  out.alias -= out.calls.length;
  process.stdout.write(JSON.stringify(out) + '\n');
}
