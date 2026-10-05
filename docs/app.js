'use strict';
const repository = '0x00F6/blocklist-ipsets';
const releaseTag = 'firehol-blocklist-ipsets';
const assetNames = ['firehol-blocklist-ipsets.mmdb', 'firehol-blocklist-ipsets.mmdb.tar.gz'];
const viewNames = new Set(['overview', 'downloads', 'pipeline', 'schema']);

function navigate(initial = false) {
  const requested = location.hash.slice(1);
  const name = viewNames.has(requested) ? requested : 'overview';
  document.querySelectorAll('.view').forEach(view => { view.hidden = view.id !== name; });
  document.querySelectorAll('[data-view]').forEach(link => {
    const active = link.dataset.view === name;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.title = `FireHOL MMDB — ${name === 'overview' ? 'IP intelligence, one database' : name[0].toUpperCase() + name.slice(1)}`;
  if (!initial) {
    document.querySelector('.view:not([hidden]) h1').setAttribute('tabindex', '-1');
    document.querySelector('.view:not([hidden]) h1').focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
}
window.addEventListener('hashchange', () => navigate());
navigate(true);

let toastTimer;
function notify(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 2400);
}
document.querySelectorAll('[data-copy-target]').forEach(button => {
  button.addEventListener('click', async () => {
    const text = document.getElementById(button.dataset.copyTarget).textContent;
    try {
      await navigator.clipboard.writeText(text);
      notify('Copied to clipboard');
    } catch {
      const range = document.createRange();
      range.selectNodeContents(document.getElementById(button.dataset.copyTarget));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      notify('Text selected — press Ctrl+C or copy from the selection');
    }
  });
});

function text(id, value) { document.getElementById(id).textContent = value; }
function size(id, bytes) {
  const node = document.getElementById(id);
  node.replaceChildren(document.createTextNode((bytes / 1e6).toFixed(2) + ' '));
  const unit = document.createElement('small');
  unit.textContent = 'MB';
  node.append(unit);
}
async function loadRelease() {
  try {
    const response = await fetch(`https://api.github.com/repos/${repository}/releases/tags/${releaseTag}`, {
      headers: { Accept: 'application/vnd.github+json' }, signal: AbortSignal.timeout(8000)
    });
    if (!response.ok) throw new Error(`Release API returned ${response.status}`);
    const release = await response.json();
    if (release.draft || !release.body?.split('\n').includes('Publication status: complete')) throw new Error('Publication is not complete');
    const assets = assetNames.map(name => release.assets?.find(asset => asset.name === name && asset.state === 'uploaded' && Number.isSafeInteger(asset.size) && asset.size > 0 && /^sha256:[0-9a-f]{64}$/.test(asset.digest)));
    if (assets.some(asset => !asset)) throw new Error('Expected two confirmed assets and SHA-256 digests');
    const [mmdb, archive] = assets;
    const epochMatch = release.body.match(/^MMDB build_epoch: (\d+)$/m);
    const commitMatch = release.body.match(/^Upstream commit: ([0-9a-f]{40})$/m);
    const epoch = Number(epochMatch?.[1]);
    const date = new Date(epoch * 1000);
    if (!epochMatch || !commitMatch || !Number.isSafeInteger(epoch) || epoch < 0 || !Number.isFinite(date.getTime())) throw new Error('Source metadata missing');
    // Download URLs stay fixed; API content is written as text, never injected as HTML.
    size('mmdb-size', mmdb.size);
    size('archive-size', archive.size);
    text('mmdb-sha', mmdb.digest.slice(7));
    text('archive-sha', archive.digest.slice(7));
    text('raw-bar-label', (mmdb.size / 1e6).toFixed(2) + ' MB');
    text('gz-bar-label', (archive.size / 1e6).toFixed(2) + ' MB');
    text('compression-saving', (100 * (1 - archive.size / mmdb.size)).toFixed(2) + '% smaller');
    text('compression-bytes', ((mmdb.size - archive.size) / 1e6).toFixed(2) + ' MB saved per download · decimal MB');
    document.getElementById('compressed-bar').style.width = Math.min(100, 100 * archive.size / mmdb.size) + '%';
    text('source-date', date.toISOString().replace('T', ' ').replace('.000Z', ' UTC'));
    text('build-epoch', String(epoch));
    const commit = document.getElementById('source-commit');
    commit.href = `https://github.com/${repository}/commit/${commitMatch[1]}`;
    commit.textContent = commitMatch[1].slice(0, 12) + ' ↗';
    text('release-status', 'Latest complete release · source ' + date.toISOString().slice(0, 10));
    text('release-note', 'Sizes, SHA-256 hashes and source metadata loaded from the latest complete GitHub release. Overview counts remain a dated benchmark.');
  } catch (error) {
    text('release-note', 'Live release details are unavailable or publication is in progress. Showing the verified 05 Oct 2026 snapshot; stable download links still point to the rolling release.');
    console.info(`[FireHOL MMDB] ${error.message}; showing verified snapshot. Retry by refreshing the page or inspect the release notes.`);
  }
}
loadRelease();
