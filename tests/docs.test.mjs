import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRequest, exampleCode, readPreview } from '../js/docs-request.js';

const endpoint = { path: '/api/public/v1/profiles/{profile_id}/series/daily', parameters: [
  {name:'profile_id', in:'path', required:true}, {name:'metrics', in:'query'}, {name:'limit', in:'query'},
] };
test('documentation requests encode values and stay on the supplied origin', () => {
  const url = new URL(buildRequest(endpoint, {profile_id:'a/b', metrics:'steps,hrv', limit:'30'}, 'https://example.test'));
  assert.equal(url.origin, 'https://example.test');
  assert.ok(url.pathname.includes('a%2Fb'));
  assert.equal(url.searchParams.get('metrics'), 'steps,hrv');
  assert.throws(() => buildRequest({...endpoint, path:'https://outside.test/'}, {profile_id:'x'}, 'https://example.test'));
  assert.throws(() => buildRequest({...endpoint, path:'/api/admin/login'}, {profile_id:'x'}, 'https://example.test'));
  assert.throws(() => buildRequest(endpoint, {}, 'https://example.test'), /profile_id/);
});
test('copyable examples contain the chosen URL and explicit error handling', () => {
  const url = 'https://example.test/api/public/v1/profiles?x=a%26b';
  assert.ok(exampleCode(url).includes(url));
  assert.match(exampleCode(url, 'javascript'), /response.ok/);
  assert.match(exampleCode(url, 'python'), /raise_for_status/);
  assert.match(exampleCode(url, 'python'), /timeout=15/);
  assert.ok(exampleCode("https://example.test/?q=a'b").includes("'\\''"));
});
test('response previews format JSON and leave HTML/SVG as inert text', async () => {
  const json = await readPreview(new Response('{"ok":true}', {headers:{'content-type':'application/json'}}));
  assert.equal(json.text, '{\n  "ok": true\n}');
  assert.equal(json.truncated, false);
  const svg = '<svg onload="alert(1)"></svg>';
  assert.equal((await readPreview(new Response(svg))).text, svg);
});
test('response previews cancel streams at the byte budget', async () => {
  let cancelled = false;
  const stream = new ReadableStream({pull(controller) { controller.enqueue(new Uint8Array(32).fill(65)); }, cancel() { cancelled = true; }});
  const preview = await readPreview(new Response(stream), 40);
  assert.equal(preview.text.length, 40);
  assert.equal(preview.truncated, true);
  assert.equal(cancelled, true);
});
