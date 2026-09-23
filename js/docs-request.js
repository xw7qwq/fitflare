// Pure helpers shared by the API reference and offline regression tests.
const API_BASE = '/api/public/v1';
export function buildRequest(endpoint, values, origin) {
  let path = endpoint.path;
  const query = new URLSearchParams();
  for (const parameter of endpoint.parameters) {
    const value = String(values[parameter.name] ?? '').trim();
    if (!value) {
      if (parameter.required) throw new Error(`请填写 ${parameter.name}。`);
      continue;
    }
    if (parameter.in === 'path') path = path.replace(`{${parameter.name}}`, encodeURIComponent(value));
    else query.set(parameter.name, value);
  }
  const url = new URL(path, origin);
  if (url.origin !== new URL(origin).origin || !(url.pathname === API_BASE || url.pathname.startsWith(API_BASE + '/'))) {
    throw new Error('只能请求本站版本化 API。');
  }
  url.search = query.toString();
  return url.href;
}

export function exampleCode(url, language = 'curl') {
  const quoted = JSON.stringify(url);
  if (language === 'javascript') return `const response = await fetch(${quoted}, {\n  credentials: 'same-origin',\n  cache: 'no-store',\n});\nif (!response.ok) throw new Error(\`HTTP \${response.status}\`);\nconst type = response.headers.get('content-type') || '';\nconst data = type.includes('application/json')\n  ? await response.json()\n  : await response.text();\nconsole.log(data);`;
  if (language === 'python') return `import requests\n\nresponse = requests.get(${quoted}, timeout=15)\nresponse.raise_for_status()\ndata = (response.json() if 'application/json' in\n        response.headers.get('Content-Type', '') else response.text)\nprint(data)`;
  return `curl -fsS '${url.replaceAll("'", "'\\''")}'`;
}

export async function readPreview(response, limit = 65536) {
  if (!response.body) return { text: '', truncated: false };
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = '', size = 0, truncated = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const take = Math.min(value.byteLength, limit - size);
      text += decoder.decode(value.subarray(0, take), { stream: true });
      size += take;
      if (size >= limit) { truncated = true; await reader.cancel(); break; }
    }
    text += decoder.decode();
  } finally { reader.releaseLock(); }
  if (!truncated && (response.headers.get('content-type') || '').includes('application/json')) {
    try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* Display malformed JSON as inert text. */ }
  }
  return { text, truncated };
}
