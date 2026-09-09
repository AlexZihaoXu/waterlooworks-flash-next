import http from 'node:http';
export function localRequest(endpoint, body, seconds = 300) {
  if (!['/api/v1/models', '/v1/chat/completions'].includes(endpoint))
    throw new Error('Unsupported local endpoint');
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port: 1234,
        path: endpoint,
        method: body ? 'POST' : 'GET',
        headers: { 'Content-Type': 'application/json' },
      },
      (res) => {
        const chunks = [];
        let size = 0;
        res.on('data', (chunk) => {
          size += chunk.length;
          if (size > 1048576) req.destroy(new Error('Response limit'));
          else chunks.push(chunk);
        });
        res.on('error', () => reject(new Error('Interrupted local response')));
        res.on('end', () => {
          clearTimeout(timer);
          if (res.statusCode !== 200) {
            reject(new Error(`Local HTTP ${res.statusCode}`));
            return;
          }
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
          } catch {
            reject(new Error('Invalid response envelope'));
          }
        });
      },
    );
    const timer = setTimeout(
      () => req.destroy(new Error('Request deadline')),
      seconds * 1000,
    );
    req.on('error', () => {
      clearTimeout(timer);
      reject(new Error('Local connection failed or deadline exceeded'));
    });
    req.end(body ? JSON.stringify(body) : undefined);
  });
}
export async function currentModel(id) {
  const data = await localRequest('/api/v1/models', null, 10);
  const models = data.models
    .filter((m) => m.type === 'llm')
    .flatMap((m) =>
      m.loaded_instances.map((i) => ({
        id: i.id,
        key: m.key,
        quantization: m.quantization,
        config: i.config,
        reasoning: m.capabilities?.reasoning?.allowed_options ?? [],
      })),
    );
  const matches = id ? models.filter((m) => m.id === id) : models;
  if (matches.length !== 1)
    throw new Error('Exactly one selected, already-loaded model is required');
  return matches[0];
}
