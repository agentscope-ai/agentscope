// Evaluate the extracted JSON instructions with the client's JS number semantics.
import fs from 'node:fs';

const binary = {
  '+': (a, b) => a + b, '-': (a, b) => a - b, '*': (a, b) => a * b,
  '**': (a, b) => a ** b, '/': (a, b) => a / b, '%': (a, b) => a % b, '===': (a, b) => a === b,
  '!==': (a, b) => a !== b, '<': (a, b) => a < b, '>': (a, b) => a > b,
  '<=': (a, b) => a <= b, '>=': (a, b) => a >= b,
};
const math = { round: Math.round, floor: Math.floor, ceil: Math.ceil, abs: Math.abs, min: Math.min, max: Math.max };

function prepare(plan, input) {
  function invoke(id, args) {
    const fn = plan.functions[id];
    const scope = Object.create(null);

    function value(n) {
      if (Object.hasOwn(n, 'literal')) return n.literal;
      if (n.numeric === 'Number') return Number(value(n.value));
      if (n.numeric === 'Number.isFinite') return Number.isFinite(value(n.value));
      if (n.variable) return scope[n.variable];
      if (n.property) {
        const obj = value(n.of);
        if (obj == null) throw new Error('Missing object for field ' + n.property);
        return Object.hasOwn(obj, n.property) ? obj[n.property] : undefined;
      }
      if (n.array) return n.array.map(value);
      if (n.object) return Object.fromEntries(Object.entries(n.object).map(([k, v]) => [k, value(v)]));
      if (n.call) return invoke(n.call, n.args.map(value));
      if (n.when) return value(n.when) ? value(n.yes) : value(n.no);
      if (n.math && Object.hasOwn(math, n.math)) return math[n.math](...n.args.map(value));
      if (n.unary === '!') return !value(n.value);
      if (n.unary === '-') return -value(n.value);
      if (n.unary === '+') return +value(n.value);
      if (n.op) {
        const a = value(n.left);
        if (n.op === '&&') return a && value(n.right);
        if (n.op === '||') return a || value(n.right);
        if (n.op === '??') return a ?? value(n.right);
        if (Object.hasOwn(binary, n.op)) return binary[n.op](a, value(n.right));
      }
      if (n.request) {
        const route = plan.endpoint.template.replace(/\{(\d+)\}/g, (_, index) => {
          const segment = value(n.bindings[index]);
          if (!['string', 'number', 'boolean'].includes(typeof segment) ||
              ['', '.', '..'].includes(String(segment))) throw new Error('Invalid path parameter');
          if (!n.encodedSegments[index] && !/^[A-Za-z0-9._~-]+$/.test(String(segment))) {
            throw new Error('Unescaped path parameter contains a reserved character');
          }
          return encodeURIComponent(segment);
        });
        const request = { method: plan.endpoint.method, path: route, pathValues: n.bindings.map(value) };
        if (n.config) {
          const config = value(n.config);
          if (config == null || Array.isArray(config) || typeof config !== 'object' ||
              Object.keys(config).some(k => k !== 'params')) throw new Error('Only query params are supported in request config');
          if (config.params) request.query = config.params;
        }
        if (n.query) request.query = value(n.query);
        if (n.body) request.body = value(n.body);
        return request;
      }
      throw new Error('Unknown expression in tool definition');
    }

    for (const [i, p] of fn.params.entries()) {
      scope[p.name] = args[i] === undefined && p.default ? value(p.default) : args[i];
    }
    for (const s of fn.steps) {
      try {
        if (s.earlyIf && value(s.earlyIf)) return value(s.value);
        if (s.return) return value(s.return);
        if (s.assign) scope[s.assign] = value(s.value);
        else if (s.rejectIf && value(s.rejectIf)) throw new Error(s.message);
      } catch (err) {
        throw new Error(`${s.evidence.file}:${s.evidence.start}: ${err.message}`);
      }
    }
  }
  for (const d of plan.editorProgram ? [] : plan.derivations ?? []) {
    const rows = d.each.split('.').reduce((obj, k) => obj && Object.hasOwn(obj, k) ? obj[k] : undefined, input);
    if (!Array.isArray(rows) || !rows.length) throw new Error('Expected nonempty rows at ' + d.each);
    rows.forEach((row, index) => {
      if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('Expected a record in ' + d.each);
      try { row[d.field] = invoke(d.symbol, [row]); }
      catch (err) { throw new Error(d.each + '[' + index + ']: ' + err.message); }
    });
  }
  return invoke(plan.entry, plan.functions[plan.entry].params.map(p => Object.hasOwn(input, p.name) ? input[p.name] : undefined));
}

try {
  const { plan, input, lookupOnly } = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (plan.editorProgram) {
    const { getQuickJS, shouldInterruptAfterDeadline } = await import('quickjs-emscripten');
    const vm = await getQuickJS();
    const { argument, code, lookups = {} } = plan.editorProgram;
    const missing = Object.keys(lookups).filter(key => !Object.hasOwn(input.editorState ?? {},key));
    const requests = missing.filter(key => lookups[key].code).map(key =>
      JSON.stringify(key) + ': (() => {' + lookups[key].code + '})()'
    );
    const action = lookupOnly ? 'return {' + requests.join(',') + '}' : code;
    const literal = value => 'JSON.parse(' + JSON.stringify(JSON.stringify(value)) + ')';
    const script = `(async () => {
      "use strict";
      const __body = ${literal(input[argument])}, __state = ${literal(input.editorState ?? {})};
      const __requiredState = name => {
        if (!Object.hasOwn(__state,name)) throw new Error("Missing ERP validation configuration: " + name);
        return __state[name];
      };
      const console = {error(...values) { if (values.at(-1) instanceof Error) throw values.at(-1); }};
      const ref = value => ({value}), reactive = value => value;
      const computed = read => ({get value() { return read() }});
      const reject = text => { throw new Error(String(text)) };
      const message = {error: reject, warning: reject};
      const __reference = (name, original) => Object.hasOwn(__state,name) ? ref(__state[name]) :
        ${literal(missing)}.includes(name) ? {get value() { throw new Error("Missing ERP reference data: " + name) }} : original();
      const nextTick = () => Promise.resolve();
      const __vue = {ref, reactive, computed, message, nextTick};
      return JSON.stringify(await (() => { ${action} })());
    })()`;
    const runtime = vm.newRuntime();
    runtime.setMemoryLimit(32 * 1024 * 1024);
    runtime.setMaxStackSize(512 * 1024);
    runtime.setInterruptHandler(shouldInterruptAfterDeadline(Date.now() + 2000));
    const context = runtime.newContext();
    try {
      const promise = context.unwrapResult(context.evalCode(script));
      try {
        context.unwrapResult(runtime.executePendingJobs());
        const result = context.unwrapResult(context.getPromiseState(promise));
        try {
          const value = JSON.parse(context.getString(result));
          if (lookupOnly) process.stdout.write(JSON.stringify(value));
          else input[argument] = value;
        }
        finally { result.dispose(); }
      } finally { promise.dispose(); }
    } finally { context.dispose(); runtime.dispose(); }
  }
  if (!lookupOnly) console.log(JSON.stringify(prepare(plan, input), (_key, val) => {
    if (typeof val === 'number' && !Number.isFinite(val)) throw new Error('Non-finite request number');
    return val;
  }));
} catch (err) {
  console.error(err.message);
  process.exitCode = 1;
}
