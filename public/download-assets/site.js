// Enhance downloads from the first-party release feed; GitHub Releases remains
// a working fallback when JavaScript or the feed is unavailable.
async function updateDownloads() {
  try {
    const response = await fetch('./updates/stable.json', { signal: AbortSignal.timeout(8000) });
    if (!response.ok) return;
    const release = await response.json();
    if (typeof release.version !== 'string' || !/^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$/.test(release.version)) return;
    const base = `https://github.com/AngadBasandrai/swarmfree/releases/download/v${release.version}/`;
    const destinations = [];
    for (const link of document.querySelectorAll('[data-asset]')) {
      const platform = release.platforms?.[link.dataset.platform];
      if (!platform || typeof platform.url !== 'string' || !platform.url.startsWith(base)) continue;
      const asset = link.dataset.asset.replace('{version}', release.version);
      destinations.push([link, base + encodeURIComponent(asset)]);
    }
    if (!destinations.length) return;
    for (const [link, url] of destinations) link.href = url;
    for (const label of document.querySelectorAll('[data-release-label]')) label.textContent = `v${release.version} alpha`;
  } catch {
    // Preserve accessible release-page links on timeouts or invalid responses.
  }
}
updateDownloads();

const preview = document.querySelector('.preview-dialog');
const previewLink = document.querySelector('[data-preview]');
if (typeof preview?.showModal === 'function') {
  previewLink?.addEventListener('click', event => {
    event.preventDefault();
    preview.showModal();
  });
  preview.querySelector('.close-preview').addEventListener('click', () => preview.close());
  preview.addEventListener('click', event => {
    if (event.target !== preview) return;
    const box = preview.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) preview.close();
  });
}
