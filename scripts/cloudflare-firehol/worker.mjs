const SOURCE = 'firehol/blocklist-ipsets';
const TARGET = '0x00F6/blocklist-ipsets';
const STATE_KEY = 'last-dispatched-master-sha';

async function github(path, token, options = {}, fetcher = fetch) {
  const response = await fetcher(`https://api.github.com/repos/${path}`, {
    ...options,
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'User-Agent': 'firehol-commit-monitor',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
    signal: AbortSignal.timeout(20000),
  });
  if (!response.ok) {
    // Do not log response bodies or credentials.
    throw new Error(`GitHub ${options.method || 'GET'} ${path}: HTTP ${response.status}`);
  }
  return response;
}

export async function poll(env, fetcher = fetch) {
  if (!env.GITHUB_TOKEN || !env.STATE) throw new Error('Missing GITHUB_TOKEN or STATE binding');
  const response = await github(`${SOURCE}/git/ref/heads/master`, env.GITHUB_TOKEN, {}, fetcher);
  const ref = await response.json();
  const sha = ref.object?.sha;
  if (ref.object?.type !== 'commit' || !/^[a-f0-9]{40}$/.test(sha || '')) {
    throw new Error('Invalid upstream master commit reference');
  }
  if (await env.STATE.get(STATE_KEY) === sha) {
    console.log(`worker.mjs poll: unchanged source_sha=${sha}`);
    return 'unchanged';
  }
  await github(`${TARGET}/dispatches`, env.GITHUB_TOKEN, {
    method: 'POST',
    body: JSON.stringify({
      event_type: 'firehol-updated',
      client_payload: { sha, source: SOURCE, branch: 'master' },
    }),
  }, fetcher);
  // Record only successful dispatches. Failures remain retryable next tick.
  await env.STATE.put(STATE_KEY, sha);
  console.log(`worker.mjs poll: dispatched target=${TARGET} source_sha=${sha}`);
  return 'dispatched';
}

export default {
  async scheduled(_controller, env) {
    await poll(env);
  },
};
