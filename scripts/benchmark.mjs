import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import {
  chooseReasoning,
  optionsFromArgs,
  safeDatasetPath,
  validateDataset,
  parseFinal,
  evaluateAnswer,
  benchmarkTable,
} from './benchmark-lib.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const hash = (value) => createHash('sha256').update(value).digest('hex');
const maxBytes = 1024 * 1024;
let stopping = false;
process.on('SIGINT', () => {
  stopping = true;
  console.log('Stopping after in-flight requests; no more work will start.');
});

function request(endpoint, body, timeout = 10) {
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
          if (size > maxBytes)
            req.destroy(new Error('Model response exceeded limit'));
          else chunks.push(chunk);
        });
        res.on('error', () => reject(new Error('Model response interrupted')));
        res.on('end', () => {
          clearTimeout(timer);
          if (res.statusCode !== 200) {
            reject(new Error(`LM Studio HTTP ${res.statusCode}`));
            return;
          }
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
          } catch {
            reject(new Error('LM Studio returned invalid response JSON'));
          }
        });
      },
    );
    const timer = setTimeout(
      () => req.destroy(new Error('Model request timed out')),
      timeout * 1000,
    );
    req.on('error', (error) => {
      clearTimeout(timer);
      reject(
        new Error(
          error.message === 'Model response exceeded limit'
            ? error.message
            : 'Model connection failed or timed out',
        ),
      );
    });
    req.end(body ? JSON.stringify(body) : undefined);
  });
}
async function loadedModel(selected) {
  const data = await request('/api/v1/models');
  const loaded = (data.models ?? [])
    .filter((m) => m.type === 'llm')
    .flatMap((m) =>
      (m.loaded_instances ?? []).map((instance) => ({
        key: m.key,
        id: instance.id,
        quantization: m.quantization,
        config: instance.config,
        reasoning: m.capabilities?.reasoning?.allowed_options ?? [],
      })),
    );
  const matches = selected ? loaded.filter((m) => m.id === selected) : loaded;
  if (matches.length !== 1)
    throw new Error(
      'Load exactly one model, or select an already loaded instance with --model',
    );
  if (
    !Number.isInteger(matches[0].config?.context_length) ||
    matches[0].config.context_length < 1
  )
    throw new Error('Loaded context length is unavailable');
  return matches[0];
}
async function main() {
  const options = optionsFromArgs(process.argv.slice(2));
  if (options.help) {
    console.log(`Usage: npm run benchmark -- [options]
  --dataset PATH       Private annotated cases (default .experiment/benchmark/cases.json)
  --dry-run            Detect model and validate inputs, without generation
  --model ID           Select an already loaded instance if multiple are loaded
  --mode both|salary|keywords   Default both (same two prompt profiles)
  --reasoning auto|none|low|medium|high   Auto prefers off, otherwise low
  --limit N            First N cases, 1–100 (default 6)
  --concurrency N      Maximum concurrent calls, 1–4 (default 2)
  --max-tokens N       Output budget, 256–8192 (default 4096)
  --timeout SECONDS    Per-call deadline, 10–600 (default 300)
Results: .experiment/benchmarks/<run>/report.md and results.json.
No collection, database writes, model loading, cloud calls or retries.`);
    return;
  }
  process.chdir(root);
  const privateRoot = path.join(root, '.experiment');
  await fs.mkdir(privateRoot, { recursive: true });
  if ((await fs.realpath(privateRoot)) !== privateRoot)
    throw new Error('Private output directory must not be a symlink');
  if (
    spawnSync('git', ['check-ignore', '-q', '.experiment/benchmarks'], {
      cwd: root,
    }).status !== 0
  )
    throw new Error('Benchmark output directory must remain Git-ignored');
  let datasetBytes;
  try {
    const inputPath = safeDatasetPath(
      await fs.realpath(safeDatasetPath(options.dataset)),
    );
    if ((await fs.stat(inputPath)).size > 20 * maxBytes)
      throw new Error('Dataset too large');
    datasetBytes = await fs.readFile(inputPath, 'utf8');
  } catch {
    throw new Error('Cannot read benchmark dataset; see benchmarks/README.md');
  }
  let cases;
  try {
    cases = validateDataset(JSON.parse(datasetBytes)).slice(0, options.limit);
  } catch {
    throw new Error('Invalid benchmark dataset; see benchmarks/README.md');
  }
  const model = await loadedModel(options.model);
  const reasoning = chooseReasoning(model.reasoning, options.reasoning);
  const concurrency = Math.min(
    options.concurrency,
    Math.max(1, model.config.parallel ?? 1),
  );
  const profiles = {};
  for (const [mode, stem] of [
    ['salary', 'current'],
    ['keywords', 'keywords'],
  ]) {
    const prompt = await fs.readFile(
      path.join(root, 'benchmarks', `${stem}-prompt.txt`),
      'utf8',
    );
    const schema = await fs.readFile(
      path.join(root, 'benchmarks', `${stem}-schema.json`),
      'utf8',
    );
    profiles[mode] = {
      prompt: prompt.trim(),
      schema: JSON.parse(schema),
      sha256: hash(prompt + schema),
    };
  }
  const modes =
    options.mode === 'both' ? ['salary', 'keywords'] : [options.mode];
  const tasks = cases.flatMap((sample) =>
    modes.map((mode) => {
      const profile = profiles[mode];
      const schema = structuredClone(profile.schema);
      const field =
        mode === 'salary'
          ? schema.$defs.EvidenceCandidate.properties.field
          : schema.properties.terms.items.properties.field;
      field.enum = Object.keys(sample.fields).sort();
      const user =
        mode === 'salary'
          ? {
              fields: sample.fields,
              request: { compensation: true, skills: true },
              deterministic_skills: sample.deterministicSkills,
            }
          : { fields: sample.fields };
      const input = JSON.stringify(user)
        .replaceAll('<', '\\u003c')
        .replaceAll('>', '\\u003e');
      const system = `${profile.prompt}\nReturn exactly one JSON object matching this schema: ${JSON.stringify(schema)}`;
      // Conservative byte bound, not an exact token count. Fail rather than truncate.
      if (
        Buffer.byteLength(system + input) + options.maxTokens + 512 >
        model.config.context_length
      )
        throw new Error(
          `Case ${sample.id} exceeds conservative context bound; use a larger loaded context or shorter saved input`,
        );
      return {
        sample,
        mode,
        schema,
        body: {
          model: model.id,
          messages: [
            { role: 'system', content: system },
            { role: 'user', content: input },
          ],
          ...(reasoning ? { reasoning_effort: reasoning } : {}),
          temperature: 0,
          max_tokens: options.maxTokens,
          store: false,
        },
      };
    }),
  );
  console.log(
    `Model: ${model.id}; context ${model.config.context_length}; reasoning ${reasoning ?? 'server default (unknown)'}; ${tasks.length} calls; concurrency ${concurrency}`,
  );
  if (options.dryRun) {
    console.log('Dry run passed. No generation requests sent.');
    return;
  }
  const reportsRoot = path.join(privateRoot, 'benchmarks');
  await fs.mkdir(reportsRoot, { recursive: true });
  if ((await fs.realpath(reportsRoot)) !== reportsRoot)
    throw new Error('Report directory must not be a symlink');
  const out = path.join(
    reportsRoot,
    `${new Date().toISOString().replaceAll(':', '-')}-${randomUUID().slice(0, 8)}`,
  );
  await fs.mkdir(out, { recursive: true });
  const metadata = {
    version: 1,
    model,
    reasoning: reasoning ?? null,
    options,
    datasetSha256: hash(datasetBytes),
    promptHashes: Object.fromEntries(
      Object.entries(profiles).map(([k, v]) => [k, v.sha256]),
    ),
    endpoint: 'http://127.0.0.1:1234/v1/chat/completions',
    structuredOutputEnforced: false,
  };
  await fs.writeFile(
    path.join(out, 'metadata.json'),
    JSON.stringify(metadata, null, 2),
  );
  const results = [];
  const started = performance.now();
  for (
    let offset = 0;
    offset < tasks.length && !stopping;
    offset += concurrency
  ) {
    const current = await loadedModel(model.id);
    if (JSON.stringify(current) !== JSON.stringify(model))
      throw new Error(
        'Loaded model/config changed; stopped before more generation',
      );
    const batch = await Promise.all(
      tasks.slice(offset, offset + concurrency).map(async (task) => {
        const begin = performance.now();
        const result = {
          id: task.sample.id,
          mode: task.mode,
          requestSha256: hash(JSON.stringify(task.body)),
          status: 'error',
          jsonValid: false,
          salaryValuesMatch: false,
          keywordReferenceTotal: task.sample.reference.keywords.length,
        };
        try {
          const response = await request(
            '/v1/chat/completions',
            task.body,
            options.timeout,
          );
          const choice = response.choices?.[0];
          result.finishReason = choice?.finish_reason ?? null;
          const usage = response.usage ?? {};
          result.usage = {
            inputTokens: usage.prompt_tokens ?? null,
            outputTokens: usage.completion_tokens ?? null,
            reasoningTokens:
              usage.completion_tokens_details?.reasoning_tokens ?? null,
          };
          const message = choice?.message ?? {};
          result.reasoningPresent = Boolean(
            message.reasoning_content ||
            message.reasoning ||
            /<think>/.test(message.content ?? ''),
          );
          if (
            reasoning === 'none' &&
            (result.usage.reasoningTokens !== 0 || result.reasoningPresent)
          ) {
            stopping = true;
            result.status = 'reasoning-not-disabled';
          } else if (choice?.finish_reason !== 'stop')
            result.status = 'incomplete-output';
          else {
            try {
              const answer = parseFinal(message.content);
              result.jsonValid = true;
              result.answer = answer; // Final answer only; never persist reasoning.
              Object.assign(
                result,
                evaluateAnswer(task.mode, answer, task.sample),
              );
              result.status = 'completed';
            } catch {
              result.status = result.jsonValid
                ? 'invalid-shape'
                : 'invalid-json';
            }
          }
        } catch {
          result.status = 'transport-error';
          stopping = true;
        }
        result.seconds = (performance.now() - begin) / 1000;
        await fs.writeFile(
          path.join(out, `${task.sample.id}-${task.mode}.json`),
          JSON.stringify(result, null, 2),
        );
        console.log(
          `${result.id} ${result.mode}: ${result.status} (${result.seconds.toFixed(1)}s)`,
        );
        return result;
      }),
    );
    results.push(...batch);
  }
  const salary = results.filter((r) => r.mode === 'salary');
  const keywords = results.filter((r) => r.mode === 'keywords');
  const sum = (rows, f) => rows.reduce((total, r) => total + f(r), 0);
  const summary = {
    ...metadata,
    seconds: (performance.now() - started) / 1000,
    planned: tasks.length,
    completed: results.length,
    interrupted: stopping,
    salaryValueMatches: sum(salary, (r) =>
      Number(r.status === 'completed' && r.salaryValuesMatch),
    ),
    salaryCases: salary.length,
    keywordMatches: sum(keywords, (r) => r.coverage?.matched ?? 0),
    keywordReferences: sum(keywords, (r) => r.keywordReferenceTotal),
    results,
  };
  await fs.writeFile(
    path.join(out, 'results.json'),
    JSON.stringify(summary, null, 2),
  );
  const table = benchmarkTable(summary);
  const heading = `Extraction benchmark\n\nModel: ${model.id}\nReasoning: ${reasoning ?? 'server default'}\nContext: ${model.config.context_length}\nWall time: ${summary.seconds.toFixed(1)}s\nCompleted calls: ${results.length}/${tasks.length}`;
  const report = `# ${heading}\n\n| Metric | Result |\n|---|---:|\n${table.map((row) => `| ${row.Metric} | ${row.Result} |`).join('\n')}\n\nFailed/incomplete replies contribute no usable matches. This is a small annotated lexical benchmark, not overall accuracy. Exact quotation does not establish semantic correctness. Salary matching does not score eligibility, payout timing, hours, or run the Python production validator. Empty references/outputs are not evidence of completeness. Prompt hashes and per-case final answers are in the private JSON artifacts. The salary profile retains the earlier combined compensation/skills prompt; the keyword profile is independent. No automatic retries, production writes or model configuration changes.\n`;
  await fs.writeFile(path.join(out, 'report.md'), report);
  console.log(heading);
  console.table(table);
  console.log(`Report: ${path.relative(root, path.join(out, 'report.md'))}`);
  if (stopping || results.some((r) => r.status !== 'completed'))
    process.exitCode = 2;
}
main().catch((error) => {
  console.error(
    `Benchmark stopped: ${error.message}. Partial results, if any, remain under .experiment/benchmarks. Use --help for options.`,
  );
  process.exitCode = 1;
});
