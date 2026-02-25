/* admin_charts.js — ECharts visualization rendering */

const FEATURE_COLORS = [
  '#a5b4fc',
  '#93c5fd',
  '#99f6e4',
  '#86efac',
  '#fde68a',
  '#fecdd3',
  '#fbcfe8',
  '#e9d5ff',
  '#f5d0fe',
  '#c7d2fe',
];

let mapChart = null;
let mapReady = false;
let recentChart = null;
let centroidCache = null;
let pathStackChart = null;
let groupDetailChart = null;

function initMap(worldMapEl) {
  if (!worldMapEl || mapReady) return false;
  const ready = window.echarts
    && typeof window.echarts.init === 'function'
    && typeof window.echarts.getMap === 'function'
    && window.echarts.getMap('world');
  if (!ready) {
    return false;
  }
  mapReady = true;
  mapChart = window.echarts.init(worldMapEl, undefined, { renderer: 'canvas' });
  window.addEventListener('resize', () => {
    if (mapChart) {
      mapChart.resize();
    }
  });
  return true;
}

function queueChartResize(chart) {
  if (!chart || typeof requestAnimationFrame !== 'function') return;
  requestAnimationFrame(() => {
    chart.resize();
    requestAnimationFrame(() => {
      chart.resize();
    });
  });
}

function collectCoords(geometry, points) {
  if (!geometry) return;
  const { type, coordinates } = geometry;
  if (!coordinates) return;
  if (type === 'Polygon') {
    coordinates.forEach((ring) => {
      ring.forEach((coord) => points.push(coord));
    });
  } else if (type === 'MultiPolygon') {
    coordinates.forEach((poly) => {
      poly.forEach((ring) => {
        ring.forEach((coord) => points.push(coord));
      });
    });
  }
}

function ensureCentroids() {
  if (centroidCache) return centroidCache;
  const world = window.echarts?.getMap?.('world');
  if (!world) return null;
  const features = world.geoJson?.features || [];
  const map = {};
  features.forEach((feature) => {
    const name = feature?.properties?.name;
    if (!name) return;
    const points = [];
    collectCoords(feature.geometry, points);
    if (!points.length) return;
    const sum = points.reduce(
      (acc, coord) => {
        acc[0] += coord[0];
        acc[1] += coord[1];
        return acc;
      },
      [0, 0]
    );
    map[name] = [sum[0] / points.length, sum[1] / points.length];
  });
  map['Local network'] = [-5.9845, 37.3891]; 
  centroidCache = map;
  return map;
}

function updateWorldMap(data, mapSubtitleEl, isoToName) {
  if (!mapReady || !mapChart) return false;
  
  const chartData = data
    .filter((row) => row.country && row.country.toUpperCase() !== 'LOCAL')
    .map((row) => ({
      name: isoToName(row.country) || row.country,
      value: row.count,
      code: (row.country || '').toUpperCase(),
    }));

  if (!chartData.length) {
    mapChart.clear();
    if (mapSubtitleEl) mapSubtitleEl.textContent = 'No data';
    return true;
  }

  const maxValue = Math.max(...chartData.map((d) => d.value));
  mapChart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (params) => `${params.name}: ${params.value ?? 0}`,
    },
    visualMap: {
      min: 0,
      max: maxValue,
      left: 'left',
      bottom: 20,
      text: ['High', 'Low'],
      inRange: {
        color: ['#dbeafe', '#1d4ed8'],
      },
      calculable: true,
    },
    series: [
      {
        name: 'Requests',
        type: 'map',
        map: 'world',
        roam: true,
        emphasis: {
          label: { show: false },
        },
        itemStyle: {
          areaColor: '#e2e8f0',
          borderColor: '#94a3b8',
        },
        data: chartData,
      },
    ],
  });
  if (mapSubtitleEl) mapSubtitleEl.textContent = `${chartData.length} countries`;
  queueChartResize(mapChart);
  return true;
}

function updateRecentScatter(recent, recentMapEl, recentMapSubtitleEl, isoToName) {
  if (!recentMapEl) return { chart: null, data: [] };
  const ready = window.echarts
    && typeof window.echarts.init === 'function'
    && typeof window.echarts.getMap === 'function'
    && window.echarts.getMap('world');
  if (!ready) {
    if (recentMapSubtitleEl) recentMapSubtitleEl.textContent = 'Loading…';
    return { chart: null, data: [] };
  }
  if (!recentChart) {
    recentChart = window.echarts.init(recentMapEl, undefined, { renderer: 'canvas' });
  }
  const centroids = ensureCentroids();
  if (!centroids) {
    if (recentMapSubtitleEl) recentMapSubtitleEl.textContent = 'Preparing…';
    return { chart: recentChart, data: [] };
  }
  const scatterData = (recent || [])
    .filter((row) => row?.country)
    .slice(0, 80)
    .map((row) => {
      const displayName = isoToName(row.country) || row.country;
      const coords = centroids[displayName];
      if (!coords) return null;
      return {
        name: displayName,
        value: [...coords, 1],
        username: row.username || 'guest',
        ts: row.ts || '',
        path: row.path || '',
        code: (row.country || '').toUpperCase(),
      };
    })
    .filter(Boolean);
  
  if (!scatterData.length) {
    recentChart.clear();
    if (recentMapSubtitleEl) recentMapSubtitleEl.textContent = 'No activity';
    return { chart: recentChart, data: [] };
  }
  
  recentChart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        const data = params?.data || {};
        return `${data.name}<br/>User: ${data.username || '—'}<br/>${data.path || ''}<br/>${data.ts || ''}`;
      },
    },
    geo: {
      map: 'world',
      roam: true,
      itemStyle: {
        areaColor: '#e5e7eb',
        borderColor: '#94a3b8',
      },
      emphasis: {
        itemStyle: {
          areaColor: '#bfdbfe',
        },
      },
    },
    series: [
      {
        type: 'effectScatter',
        coordinateSystem: 'geo',
        symbolSize: 8,
        rippleEffect: { brushType: 'stroke' },
        itemStyle: { color: '#f97316' },
        emphasis: {
          scale: true,
          scaleSize: 2,
          itemStyle: {
            color: '#f97316',
            shadowBlur: 10,
            shadowColor: 'rgba(249,115,22,0.5)',
          },
        },
        data: scatterData,
      },
    ],
  });
  if (recentMapSubtitleEl) recentMapSubtitleEl.textContent = `${scatterData.length} recent hits`;
  queueChartResize(recentChart);
  return { chart: recentChart, data: scatterData };
}

function updatePathStacked(pathGroups, pathStackEl) {
  if (!pathStackEl) return { chart: null, categories: [] };
  const groups = (pathGroups || []).slice(0, 10);
  if (!groups.length) {
    if (pathStackChart) pathStackChart.clear();
    return { chart: pathStackChart, categories: [] };
  }
  if (!pathStackChart && window.echarts?.init) {
    pathStackChart = window.echarts.init(pathStackEl, undefined, { renderer: 'canvas' });
    window.addEventListener('resize', () => {
      pathStackChart?.resize();
    });
  }
  if (!pathStackChart) return { chart: null, categories: [] };
  
  const categories = groups.map((g) => g.group);
  const series = [{
    name: 'Hits',
    type: 'bar',
    data: groups.map((g, idx) => ({
      value: g.total || 0,
      itemStyle: { color: FEATURE_COLORS[idx % FEATURE_COLORS.length] },
    })),
  }];
  pathStackChart.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
    },
    grid: { left: 60, right: 20, bottom: 40, top: 20 },
    xAxis: {
      type: 'value',
      boundaryGap: [0, 0.01],
      axisLabel: { color: '#475569' },
    },
    yAxis: {
      type: 'category',
      data: categories,
      axisLabel: { color: '#475569' },
    },
    series,
  });
  return { chart: pathStackChart, categories };
}

function updateGroupDetailChart(groupName, groupDetails, groupDetailEl, groupDetailSubtitle) {
  if (!groupDetailEl) return null;
  if (!groupName) {
    if (groupDetailSubtitle) groupDetailSubtitle.textContent = 'No data available';
    if (groupDetailChart) groupDetailChart.clear();
    return null;
  }
  if (groupDetailSubtitle) {
    groupDetailSubtitle.textContent = `Top features in ${groupName}`;
  }
  const entries = (groupDetails[groupName] || []).slice(0, 6);
  if (!entries.length) {
    if (groupDetailChart) groupDetailChart.clear();
    return null;
  }
  if (!groupDetailChart && window.echarts?.init) {
    groupDetailChart = window.echarts.init(groupDetailEl, undefined, { renderer: 'canvas' });
    window.addEventListener('resize', () => groupDetailChart?.resize());
  }
  if (!groupDetailChart) return null;
  
  const categories = entries.map((row) => row.subgroup || row.path || '—');
  const series = [{
    name: 'Hits',
    type: 'bar',
    data: entries.map((row, idx) => ({
      value: row.total || 0,
      itemStyle: { color: FEATURE_COLORS[idx % FEATURE_COLORS.length] },
    })),
  }];
  groupDetailChart.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
    },
    grid: { left: 80, right: 20, bottom: 40, top: 20 },
    xAxis: { type: 'value', axisLabel: { color: '#475569' } },
    yAxis: { type: 'category', data: categories, axisLabel: { color: '#475569' } },
    series,
  });
  return groupDetailChart;
}

function updateUserChart(recent, userChartEl, userChartSubtitle, normalizeUser, currentUserFilter) {
  if (!userChartEl) return;
  const rows = Array.isArray(recent) ? recent : [];
  const counts = new Map();
  const userBuckets = new Map();
  const now = Date.now();
  const windowMinutes = 60;
  const bucketCount = 12;
  const windowMs = windowMinutes * 60 * 1000;
  const bucketMs = windowMs / bucketCount;
  const windowStart = now - windowMs;

  rows.forEach((row) => {
    const user = normalizeUser(row?.username);
    if (!user) return;
    counts.set(user, (counts.get(user) || 0) + 1);
    const ts = row?.ts ? new Date(row.ts).getTime() : NaN;
    if (!Number.isNaN(ts) && ts >= windowStart) {
      const idx = Math.min(bucketCount - 1, Math.max(0, Math.floor((ts - windowStart) / bucketMs)));
      if (!userBuckets.has(user)) {
        userBuckets.set(user, new Array(bucketCount).fill(0));
      }
      userBuckets.get(user)[idx] += 1;
    }
  });

  const entries = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  const listEl = userChartEl.querySelector('.user-spark-list') || userChartEl;
  listEl.innerHTML = '';

  if (userChartSubtitle) {
    userChartSubtitle.textContent = entries.length ? 'Last 60 min' : 'No activity';
  }

  entries.forEach(([user, total]) => {
    const rowEl = document.createElement('button');
    rowEl.type = 'button';
    rowEl.className = 'user-spark-row';
    if (currentUserFilter && currentUserFilter !== user) rowEl.classList.add('muted');
    if (currentUserFilter && currentUserFilter === user) rowEl.classList.add('active');

    const nameEl = document.createElement('div');
    nameEl.className = 'user-spark-name';
    nameEl.textContent = user || '—';

    const countEl = document.createElement('div');
    countEl.className = 'user-spark-count';
    countEl.textContent = total;

    const sparkEl = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    sparkEl.setAttribute('viewBox', '0 0 120 28');
    sparkEl.setAttribute('class', 'user-sparkline');
    sparkEl.setAttribute('aria-hidden', 'true');
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    bg.setAttribute('class', 'spark-bg');
    bg.setAttribute('d', 'M0 22 L120 22');
    sparkEl.appendChild(bg);

    const series = userBuckets.get(user) || new Array(bucketCount).fill(0);
    const maxVal = Math.max(1, ...series);
    const step = 120 / (bucketCount - 1);
    let d = '';
    series.forEach((val, idx) => {
      const x = Number((idx * step).toFixed(2));
      const y = Number((24 - (val / maxVal) * 18).toFixed(2));
      d += `${idx === 0 ? 'M' : 'L'}${x} ${y} `;
    });
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d.trim());
    sparkEl.appendChild(path);

    rowEl.appendChild(nameEl);
    rowEl.appendChild(sparkEl);
    rowEl.appendChild(countEl);
    listEl.appendChild(rowEl);
  });
}

// Export as global
window.DashboardCharts = {
  initMap,
  updateWorldMap,
  updateRecentScatter,
  updatePathStacked,
  updateGroupDetailChart,
  updateUserChart,
  getMapChart: () => mapChart,
  getRecentChart: () => recentChart,
  getPathStackChart: () => pathStackChart,
  getGroupDetailChart: () => groupDetailChart,
  isMapReady: () => mapReady,
};
