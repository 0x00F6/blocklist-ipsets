import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { poll } from './worker.mjs';

const sha = 'a'.repeat(40);
function fixture(previous = null, status = 204, ref = { object: { sha, type: 'commit' } }) {
  const calls = [], writes = [];
  const env = {
    GITHUB_TOKEN: 'test-token',
    STATE: { get: async () => previous, put: async (...args) => writes.push(args) },
  };
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    return options.method === 'POST'
      ? new Response(null, { status })
      : Response.json(ref);
  };
  return { env, fetcher, calls, writes };
}

test('unchanged source does not dispatch or write state', async () => {
  const f = fixture(sha);
  assert.equal(await poll(f.env, f.fetcher), 'unchanged');
  assert.equal(f.calls.length, 1);
  assert.equal(f.writes.length, 0);
});
test('first poll and changed source dispatch the configured event', async () => {
  for (const previous of [null, 'b'.repeat(40)]) {
    const f = fixture(previous);
    assert.equal(await poll(f.env, f.fetcher), 'dispatched');
    assert.equal(f.calls[1].url, 'https://api.github.com/repos/0x00F6/blocklist-ipsets/dispatches');
    const payload = JSON.parse(f.calls[1].options.body);
    assert.equal(payload.event_type, 'firehol-updated');
    assert.equal(payload.client_payload.sha, sha);
    assert.deepEqual(f.writes, [['last-dispatched-master-sha', sha]]);
  }
});
test('failed dispatch leaves state unchanged for retry', async () => {
  const f = fixture(null, 403);
  await assert.rejects(poll(f.env, f.fetcher), /HTTP 403/);
  assert.equal(f.writes.length, 0);
});
test('failed upstream request never dispatches', async () => {
  const f = fixture();
  await assert.rejects(poll(f.env, async () => new Response(null, { status: 429 })), /HTTP 429/);
  assert.equal(f.writes.length, 0);
});
test('malformed upstream references are rejected', async () => {
  for (const ref of [{}, { object: { sha: 'bad', type: 'commit' } }, { object: { sha, type: 'tag' } }]) {
    const f = fixture(null, 204, ref);
    await assert.rejects(poll(f.env, f.fetcher), /Invalid upstream/);
    assert.equal(f.calls.length, 1);
  }
});
test('failed KV write surfaces a failure', async () => {
  const f = fixture();
  f.env.STATE.put = async () => { throw new Error('KV unavailable'); };
  await assert.rejects(poll(f.env, f.fetcher), /KV unavailable/);
});
test('missing deployment bindings fail before network access', async () => {
  await assert.rejects(poll({}), /Missing/);
});
test('worker exposes only a scheduled handler', () => {
  assert.equal(typeof worker.scheduled, 'function');
  assert.equal(worker.fetch, undefined);
});
