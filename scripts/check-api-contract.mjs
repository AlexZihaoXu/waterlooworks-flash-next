import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString, COMMENT_HEADER } from 'openapi-typescript';
import prettier from 'prettier';

const output = new URL('../frontend/src/lib/api.generated.ts', import.meta.url);
const schema = new URL('../docs/openapi.json', import.meta.url);
const nodes = await openapiTS(schema);
const config = await prettier.resolveConfig(fileURLToPath(output));
const expected = await prettier.format(COMMENT_HEADER + astToString(nodes), {
  ...config,
  parser: 'typescript',
});
if ((await readFile(output, 'utf8')) !== expected) {
  throw new Error(
    'Generated frontend API types are out of date; run npm run api:generate.',
  );
}
console.log('OpenAPI frontend types are synchronized.');
