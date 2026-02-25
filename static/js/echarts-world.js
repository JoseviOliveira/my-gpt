(function () {
  const GEO_URL = '/js/world.geo.json';
  const RETRY_DELAY = 200;
  const MAX_RETRIES = 50;
  let attempts = 0;

  function hasWorld() {
    return Boolean(window.echarts && typeof window.echarts.getMap === 'function' && window.echarts.getMap('world'));
  }

  function waitForEcharts() {
    if (window.echarts && typeof window.echarts.registerMap === 'function') {
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      if (attempts++ > MAX_RETRIES) return reject(new Error('ECharts never loaded'));
      setTimeout(() => waitForEcharts().then(resolve).catch(reject), RETRY_DELAY);
    });
  }

  async function loadGeoJSON() {
    const resp = await fetch(GEO_URL, { cache: 'force-cache' });
    if (!resp.ok) {
      throw new Error(`world.geo.json status ${resp.status}`);
    }
    return resp.json();
  }

  async function ensureWorld() {
    if (hasWorld()) return;
    try {
      await waitForEcharts();
      if (hasWorld()) return;
      const geojson = await loadGeoJSON();
      window.echarts.registerMap('world', geojson, {}, { nameProperty: 'iso_a2' });
      console.info('[echarts] world map registered');
    } catch (err) {
      console.warn('[echarts] failed to load world map', err);
    }
  }

  ensureWorld();
})();
