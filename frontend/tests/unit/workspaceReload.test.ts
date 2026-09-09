import { expect, it } from 'vitest';
import {
  createWorkspaceReloadPolicy,
  createLatestWorkspaceRead,
} from '../../src/lib/workspaceReload';

// Event-control primitives and generic promises only, not API/posting fixtures.
it('reloads once at details and once at terminal, across repeats, reconnects and runs', () => {
  const reload = createWorkspaceReloadPolicy();
  expect(reload('one', 'running', 'listing')).toBe(false);
  expect(reload('one', 'running', 'details')).toBe(true);
  expect(reload('one', 'running', 'detail_rest')).toBe(false);
  expect(reload('one', 'running', 'details')).toBe(false);
  expect(reload('one', 'completed', 'done')).toBe(true);
  expect(reload('one', 'completed', 'done')).toBe(false);
  expect(reload('one', 'running', 'details')).toBe(false);
  expect(reload('two', 'running', 'details')).toBe(true);
  expect(reload('two', 'completed', 'done')).toBe(true);
  expect(reload('one', 'completed', 'done')).toBe(false);
});
it.each(['completed', 'failed', 'interrupted'])(
  'reloads %s without detail events and preserves partial-update reloads',
  (status) => {
    const reload = createWorkspaceReloadPolicy();
    expect(reload(null, status, 'done')).toBe(false);
    expect(reload('one', status, 'done')).toBe(true);
    expect(reload('one', status, 'done')).toBe(false);
    expect(reload('two', 'running', 'details')).toBe(true);
    expect(reload('two', status, 'details')).toBe(true);
    expect(reload('two', status, 'details')).toBe(false);
  },
);
it.each(['resolve', 'reject'])(
  'ignores stale workspace %s after a newer read completes',
  async (outcome) => {
    const read = createLatestWorkspaceRead();
    let resolve!: (value: string) => void;
    let reject!: (error: Error) => void;
    const pending = new Promise<string>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    const values: string[] = [];
    const errors: string[] = [];
    const early = read(
      () => pending,
      (value) => {
        values.push(value);
      },
      () => {
        errors.push('early');
      },
    );
    await read(
      () => Promise.resolve('terminal'),
      (value) => {
        values.push(value);
      },
      () => {
        errors.push('terminal');
      },
    );
    if (outcome === 'resolve') resolve('early');
    else reject(new Error('primitive failure'));
    await early;
    expect(values).toEqual(['terminal']);
    expect(errors).toEqual([]);
  },
);
it('reports the latest read failure without letting an older success replace it', async () => {
  const read = createLatestWorkspaceRead();
  let resolve!: (value: string) => void;
  const pending = new Promise<string>((yes) => {
    resolve = yes;
  });
  const values: string[] = [];
  const early = read(
    () => pending,
    (value) => {
      values.push(value);
    },
    () => {
      values.push('early error');
    },
  );
  await read(
    () => Promise.reject(new Error('primitive failure')),
    () => {
      values.push('unexpected');
    },
    () => {
      values.push('latest error');
    },
  );
  resolve('stale');
  await early;
  expect(values).toEqual(['latest error']);
});
