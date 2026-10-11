// Renderer logic tests using DOM-shaped text nodes. This is not a browser visual test.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  set innerHTML(_) { throw new Error('Unsafe HTML sink'); }
}
const document = {createElement: tag => new Node(tag), createTextNode: text => { const n = new Node('#text'); n.textContent = text; return n; }};
const script = fs.readFileSync('web/app.js', 'utf8');
const start = script.indexOf('function inline('), end = script.indexOf('function message(');
assert.ok(start >= 0 && end > start);
const ctx = vm.createContext({document, URL});
vm.runInContext(script.slice(start, end), ctx);
const root = new Node('div');
const attack = '<img src=x onerror=alert(1)> **безопасно** `code` [ссылка](javascript:alert(1))\n```html\n<script>alert(2)</script>\n```';
ctx.markdown(attack, root);
function tags(node) { return [node.tag, ...node.children.flatMap(tags)]; }
assert.ok(root.textContent.includes('<script>alert(2)</script>'));
assert.ok(root.textContent.includes('<img src=x onerror=alert(1)>'));
assert.ok(tags(root).includes('strong') && tags(root).includes('pre'));
assert.ok(!tags(root).includes('script') && !tags(root).includes('img') && !tags(root).includes('a'));
const sources = new Node('div');
ctx.renderSources([{citation_id:'S1', title:'<script>x</script>', source:'a.md', quote:'<img onerror=x>', source_url:'javascript:alert(1)'},
                   {citation_id:'S2', title:'Good', source:'b.md', quote:'Quote', source_url:'https://example.org/doc'}], sources);
const links = [];
function visit(n) { if (n.tag === 'a') links.push(n); n.children.forEach(visit); }
visit(sources);
assert.equal(links.length, 1);
assert.equal(links[0].rel, 'noopener noreferrer');
assert.equal(links[0].href, 'https://example.org/doc');
assert.ok(!tags(sources).includes('script') && !tags(sources).includes('img'));
assert.ok(!/localStorage|sessionStorage|document\.cookie|innerHTML\s*=/.test(script));
console.log('Safe Markdown, source URLs and absence of persistent key storage: PASS');
