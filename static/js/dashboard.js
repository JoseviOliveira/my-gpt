/* admin.js — Main admin dashboard orchestration (refactored) */

const DEFAULT_SHOW_LAST = 500;
const statusBanner = document.getElementById('statusBanner');
const refreshBtn = document.getElementById('refreshBtn');
const rangeInput = document.getElementById('rangeInput');
const metricActiveUsers = document.getElementById('metricActiveUsers');
const metricUsers = document.getElementById('metricUsers');
const metricIps = document.getElementById('metricIps');
const countryTable = document.getElementById('countryTable');
const recentTable = document.getElementById('recentTable');
const countryCountEl = document.getElementById('countryCount');
const recentCountEl = document.getElementById('recentCount');
const worldMapEl = document.getElementById('worldMap');
const mapSubtitleEl = document.getElementById('mapSubtitle');
const recentMapEl = document.getElementById('recentScatter');
const recentMapSubtitleEl = document.getElementById('recentMapSubtitle');
const clearCountryFilterBtn = document.getElementById('clearCountryFilter');
const clearRecentFilterBtn = document.getElementById('clearRecentFilter');
const pathStackEl = document.getElementById('pathStacked');
const groupDetailEl = document.getElementById('groupDetailChart');
const groupDetailSubtitle = document.getElementById('groupDetailSubtitle');
const userChartEl = document.getElementById('userChart');
const userChartSubtitle = document.getElementById('userChartSubtitle');
const clearUserFilterBtn = document.getElementById('clearUserFilter');
const clearGroupFilterBtn = document.getElementById('clearGroupFilter');
const pageSizeInput = document.getElementById('pageSizeInput');
const pagePrevBtn = document.getElementById('pagePrev');
const pageNextBtn = document.getElementById('pageNext');
const pageInfoEl = document.getElementById('pageInfo');
const hideLocalToggle = document.getElementById('hideLocalToggle');

// Helper to detect private/local IP addresses
function isPrivateIP(ip) {
  if (!ip) return false;
  const parts = ip.split('.');
  if (parts.length !== 4) return false;
  const a = parseInt(parts[0], 10);
  const b = parseInt(parts[1], 10);
  // 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 127) return true;
  return false;
}

// State
let lastCountryDataset = [];
let lastRecentDataset = [];
let lastScatterData = [];
let lastHighlightedRegionName = null;
let lastScatterHighlights = [];
let groupDetails = {};
let groupSummary = [];
let currentStackCategories = [];
let lastPathStackHighlight = null;
let currentUserFilter = null;
const highlightState = { country: null, pathGroup: null };

// Delegate to extracted modules
const getGeo = () => window.DashboardGeo || {};
const getAnalytics = () => window.DashboardAnalytics || {};
const getCharts = () => window.DashboardCharts || {};
const getTables = () => window.DashboardTables || {};

const isoToName = (code) => getGeo().isoToName?.(code) || code || '';
const normalizeCountry = (code) => getGeo().normalizeCountry?.(code) || null;
const formatCountry = (code) => getGeo().formatCountry?.(code) || code || '—';
const REGION_OVERRIDES = () => getGeo().REGION_OVERRIDES || {};

async function fetchAnalytics(limit) {
  const url = new URL('/api/dashboard/analytics/summary', window.location.origin);
  if (limit) url.searchParams.set('limit', limit);
  const resp = await fetch(url, { credentials: 'same-origin' });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  return resp.json();
}

function normalizeCountryToken(value) {
  const token = (value || '').toString().trim();
  return token ? token.toUpperCase() : '';
}

function countryMatches(rowCountry, target) {
  if (!target) return false;
  const targetNorm = normalizeCountry(target) || normalizeCountryToken(target);
  const rowNorm = normalizeCountry(rowCountry) || normalizeCountryToken(rowCountry);
  if (targetNorm && rowNorm && targetNorm === rowNorm) return true;
  const targetName = normalizeCountryToken(isoToName(targetNorm || target) || target);
  const rowName = normalizeCountryToken(isoToName(rowNorm || rowCountry) || rowCountry);
  return !!targetName && !!rowName && targetName === rowName;
}

function renderDetailedTableWithFormat(rows) {
  const { pageRows } = getTables().renderDetailedTable(rows, recentTable, pageInfoEl, pagePrevBtn, pageNextBtn, pageSizeInput);
  getTables().renderTable(recentTable, pageRows, [
    (row) => getAnalytics().formatTimestamp(row.ts),
    (row) => row.username || '—',
    (row) => row.method || '—',
    (row) => row.path || '—',
    (row) => row.ip || '—',
    (row) => formatCountry(row.country),
    (row) => row.group_label || '—',
    (row) => row.subgroup_label || '—',
    (row) => getAnalytics().formatUserAgent(row),
  ], {
    setDataAttr: (row) => ({
      country: (row.country || '').toUpperCase(),
      group: (row.group_label || '').toUpperCase(),
    }),
  });
  applyTableHighlight();
}

function updateWorldMap(countries) {
  if (Array.isArray(countries)) {
    lastCountryDataset = countries;
  }
  const charts = getCharts();
  if (!charts.isMapReady()) {
    const initialized = charts.initMap(worldMapEl);
    if (!initialized) {
      if (mapSubtitleEl) mapSubtitleEl.textContent = 'Loading…';
      setTimeout(() => updateWorldMap(lastCountryDataset), 200);
      return;
    }
  }
  charts.updateWorldMap(lastCountryDataset, mapSubtitleEl, isoToName);
  const mapChart = charts.getMapChart();
  if (mapChart) {
    mapChart.off('click');
    mapChart.on('click', handleMapClick);
  }
  applyMapHighlight();
}

function updateRecentScatter(recent) {
  const charts = getCharts();
  const result = charts.updateRecentScatter(recent, recentMapEl, recentMapSubtitleEl, isoToName);
  if (!result.chart) {
    setTimeout(() => updateRecentScatter(recent), 200);
    return;
  }
  lastScatterData = result.data;
  const recentChart = charts.getRecentChart();
  if (recentChart) {
    recentChart.off('click');
    recentChart.off('geoselectchanged');
    recentChart.on('click', handleScatterClick);
    recentChart.on('geoselectchanged', handleGeoSelect);
  }
  applyScatterHighlight();
}

function updatePathStacked(pathGroups) {
  const charts = getCharts();
  const result = charts.updatePathStacked(pathGroups, pathStackEl);
  currentStackCategories = result.categories;
  const pathStackChart = charts.getPathStackChart();
  if (pathStackChart) {
    pathStackChart.off('click');
    pathStackChart.on('click', (params) => {
      if (!params || typeof params.name === 'undefined') return;
      setHighlightedGroup(params.name);
    });
  }
  applyGroupHighlight();
}

function updateGroupDetailChart() {
  let groupName = highlightState.pathGroup;
  if (!groupName && groupSummary.length) {
    groupName = groupSummary[0]?.group || null;
  }
  getCharts().updateGroupDetailChart(groupName, groupDetails, groupDetailEl, groupDetailSubtitle);
}

function applyAllFilters() {
  const baseRows = Array.isArray(lastRecentDataset) ? lastRecentDataset : [];
  if (currentUserFilter && !baseRows.some((row) => getAnalytics().normalizeUser(row?.username) === currentUserFilter)) {
    currentUserFilter = null;
  }
  if (highlightState.pathGroup && !baseRows.some((row) => {
    const label = (row?.group_label || 'App').trim() || 'App';
    return label === highlightState.pathGroup;
  })) {
    highlightState.pathGroup = null;
  }
  if (highlightState.country && !baseRows.some((row) => countryMatches(row?.country, highlightState.country))) {
    highlightState.country = null;
  }
  let filtered = baseRows.slice();
  
  // Filter out local/private IP traffic if checkbox is enabled
  if (hideLocalToggle && hideLocalToggle.checked) {
    filtered = filtered.filter((row) => !isPrivateIP(row?.ip));
  }
  
  if (highlightState.country) {
    filtered = filtered.filter((row) => countryMatches(row?.country, highlightState.country));
  }
  if (highlightState.pathGroup) {
    filtered = filtered.filter((row) => {
      const label = (row?.group_label || 'App').trim() || 'App';
      return label === highlightState.pathGroup;
    });
  }
  if (currentUserFilter) {
    filtered = filtered.filter((row) => getAnalytics().normalizeUser(row?.username) === currentUserFilter);
  }
  const totals = getAnalytics().buildTotals(filtered);
  const countries = getAnalytics().buildCountries(filtered).slice(0, 7);
  const groups = getAnalytics().buildGroupSummary(filtered);
  const details = getAnalytics().buildGroupDetails(filtered);

  metricUsers.textContent = totals.unique_users ?? '0';
  metricIps.textContent = totals.unique_ips ?? '0';
  if (metricActiveUsers) {
    metricActiveUsers.textContent = totals.active_users ?? '0';
  }
  countryCountEl.textContent = `${countries.length} entries`;
  groupSummary = groups;
  groupDetails = details;

  getTables().renderTable(countryTable, countries, [
    (row) => row.country ? `${formatCountry(row.country)}` : 'Unknown',
    (row) => row.count,
  ], {
    rowClass: 'cursor-pointer',
    rowTitle: 'Filter by country',
    setDataAttr: (row) => ({ country: (row.country || '').toString() }),
  });

  const visible = filtered.length;
  const countryFilter = !!highlightState.country;
  const userLabel = currentUserFilter ? `user: ${currentUserFilter}` : null;
  const countryLabel = countryFilter ? 'country filter' : null;
  const groupLabel = highlightState.pathGroup ? `group: ${highlightState.pathGroup}` : null;
  const labels = [userLabel, countryLabel, groupLabel].filter(Boolean).join(', ');
  if (recentCountEl) {
    recentCountEl.textContent = labels ? `${visible} rows (${labels})` : `${visible} rows`;
  }
  if (clearUserFilterBtn) {
    clearUserFilterBtn.classList.toggle('hidden', !currentUserFilter);
  }
  if (clearCountryFilterBtn) {
    clearCountryFilterBtn.classList.toggle('hidden', !highlightState.country);
  }
  if (clearRecentFilterBtn) {
    clearRecentFilterBtn.classList.toggle('hidden', !highlightState.country);
  }
  if (clearGroupFilterBtn) {
    clearGroupFilterBtn.classList.toggle('hidden', !highlightState.pathGroup);
  }
  renderDetailedTableWithFormat(filtered);
  updateWorldMap(countries);
  updateRecentScatter(filtered);
  updatePathStacked(groups);
  updateGroupDetailChart();
  getCharts().updateUserChart(filtered, userChartEl, userChartSubtitle, normalizeUser, currentUserFilter);
  
  const userChartListEl = userChartEl?.querySelector('.user-spark-list');
  if (userChartListEl) {
    userChartListEl.querySelectorAll('.user-spark-row').forEach((rowEl) => {
      const userName = rowEl.querySelector('.user-spark-name')?.textContent || '';
      rowEl.onclick = () => {
        currentUserFilter = currentUserFilter === userName ? null : userName;
        applyAllFilters();
      };
    });
  }
  applyGroupHighlight();
}

async function loadAnalytics() {
  const limit = Number(rangeInput?.value) || DEFAULT_SHOW_LAST;
  const statusStart = Date.now();
  if (statusBanner) {
    statusBanner.textContent = `Loading last ${limit} rows…`;
    statusBanner.classList.remove('fade-out');
  }
  refreshBtn.disabled = true;
  try {
    const data = await fetchAnalytics(limit);
    const recent = Array.isArray(data?.recent) ? data.recent : [];
    lastRecentDataset = recent;
    applyAllFilters();
    if (statusBanner) {
      const elapsed = Date.now() - statusStart;
      const delay = Math.max(0, 1500 - elapsed);
      setTimeout(() => {
        statusBanner.textContent = `Updated · ${new Date().toLocaleTimeString('en-GB', { hour12: false })}`;
        statusBanner.classList.remove('fade-out');
        setTimeout(() => {
          statusBanner.classList.add('fade-out');
        }, 6000);
      }, delay);
    }
  } catch (err) {
    console.error('[admin] analytics fetch failed', err);
    if (statusBanner) {
      statusBanner.textContent = `Failed to load analytics: ${err.message}`;
      statusBanner.classList.remove('fade-out');
    }
  } finally {
    refreshBtn.disabled = false;
  }
}

function setHighlightedCountry(code) {
  const normalized = normalizeCountry(code);
  highlightState.country = highlightState.country === normalized ? null : normalized;
  applyAllFilters();
}

function setHighlightedGroup(name) {
  const label = name || null;
  highlightState.pathGroup = highlightState.pathGroup === label ? null : label;
  applyAllFilters();
}

function handleMapClick(params) {
  const code = (params?.data?.code || '').toUpperCase();
  setHighlightedCountry(code || null);
}

function handleScatterClick(params) {
  const code = (params?.data?.code || '').toUpperCase();
  setHighlightedCountry(code || null);
}

function handleGeoSelect(params) {
  const selected = params?.selected || {};
  const names = Object.keys(selected);
  if (!names.length) return;
  const name = names.find((key) => selected[key]?.geoIndex === 0);
  if (!name) return;
  const code = Object.keys(REGION_OVERRIDES()).find(
    (iso) => (isoToName(iso) || iso).toLowerCase() === name.toLowerCase()
  );
  if (code) setHighlightedCountry(code);
}

function applyMapHighlight() {
  const mapChart = getCharts().getMapChart();
  if (!mapChart) return;
  if (lastHighlightedRegionName) {
    try {
      mapChart.dispatchAction({ type: 'downplay', seriesIndex: 0, name: lastHighlightedRegionName });
    } catch {}
    lastHighlightedRegionName = null;
  }
  const target = highlightState.country;
  if (!target) return;
  const displayName = isoToName(target) || target;
  try {
    mapChart.dispatchAction({ type: 'highlight', seriesIndex: 0, name: displayName });
    lastHighlightedRegionName = displayName;
  } catch {}
}

function applyScatterHighlight() {
  const recentChart = getCharts().getRecentChart();
  if (!recentChart) return;
  if (lastScatterHighlights.length) {
    lastScatterHighlights.forEach((idx) => {
      try {
        recentChart.dispatchAction({ type: 'downplay', seriesIndex: 0, dataIndex: idx });
      } catch {}
    });
    lastScatterHighlights = [];
  }
  const target = highlightState.country;
  if (!target) return;
  lastScatterData.forEach((item, idx) => {
    if ((item.code || '').toUpperCase() === target) {
      try {
        recentChart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: idx });
        lastScatterHighlights.push(idx);
      } catch {}
    }
  });
}

function applyGroupHighlight() {
  const pathStackChart = getCharts().getPathStackChart();
  if (!pathStackChart) return;
  const categories = currentStackCategories || [];
  const target = highlightState.pathGroup;
  const targetIndex = target ? categories.indexOf(target) : -1;
  const seriesCount = pathStackChart.getOption().series?.length || 0;
  if (lastPathStackHighlight !== null) {
    for (let i = 0; i < seriesCount; i++) {
      try {
        pathStackChart.dispatchAction({ type: 'downplay', seriesIndex: i, dataIndex: lastPathStackHighlight });
      } catch {}
    }
    lastPathStackHighlight = null;
  }
  if (targetIndex >= 0) {
    for (let i = 0; i < seriesCount; i++) {
      try {
        pathStackChart.dispatchAction({ type: 'highlight', seriesIndex: i, dataIndex: targetIndex });
      } catch {}
    }
    lastPathStackHighlight = targetIndex;
  }
}

function applyTableHighlight() {
  const target = (highlightState.country || '').toUpperCase();
  const hasTarget = !!target;

  const highlightRows = (table) => {
    if (!table) return;
    table.querySelectorAll('tr').forEach((tr) => {
      const code = tr.dataset.country || '';
      const match = hasTarget && countryMatches(code, target);
      tr.classList.toggle('table-highlight', match);
    });
  };

  highlightRows(countryTable);
  if (!recentTable) return;
  recentTable.querySelectorAll('tr').forEach((tr) => {
    const code = tr.dataset.country || '';
    const match = hasTarget && countryMatches(code, target);
    tr.classList.toggle('table-highlight', match);
  });
}

// Event listeners
if (refreshBtn) {
  refreshBtn.addEventListener('click', (evt) => {
    evt.preventDefault();
    loadAnalytics();
  });
}

if (rangeInput) {
  rangeInput.addEventListener('change', () => loadAnalytics());
}

if (pageSizeInput) {
  pageSizeInput.addEventListener('change', () => {
    getTables().setCurrentPage(1);
    renderDetailedTableWithFormat(getTables().getLastDetailedRows());
  });
}
if (hideLocalToggle) {
  hideLocalToggle.addEventListener('change', () => {
    applyAllFilters();
    updateWorldMap();
    updateRecentMap();
    updatePathStack();
    updateGroupDetail();
    updateUserChart();
  });
}
if (pagePrevBtn) {
  pagePrevBtn.addEventListener('click', (evt) => {
    evt.preventDefault();
    const current = getTables().getCurrentPage();
    if (current > 1) {
      getTables().setCurrentPage(current - 1);
      renderDetailedTableWithFormat(getTables().getLastDetailedRows());
    }
  });
}

if (pageNextBtn) {
  pageNextBtn.addEventListener('click', (evt) => {
    evt.preventDefault();
    const current = getTables().getCurrentPage();
    const pageSize = getTables().getPageSize();
    const totalPages = Math.max(1, Math.ceil(getTables().getLastDetailedRows().length / pageSize));
    if (current < totalPages) {
      getTables().setCurrentPage(current + 1);
      renderDetailedTableWithFormat(getTables().getLastDetailedRows());
    }
  });
}

if (clearUserFilterBtn) {
  clearUserFilterBtn.addEventListener('click', (evt) => {
    evt.preventDefault();
    currentUserFilter = null;
    applyAllFilters();
  });
}

if (clearCountryFilterBtn) {
  clearCountryFilterBtn.addEventListener('click', (evt) => {
    evt.preventDefault();
    highlightState.country = null;
    applyAllFilters();
  });
}

if (clearRecentFilterBtn) {
  clearRecentFilterBtn.addEventListener('click', (evt) => {
    evt.preventDefault();
    highlightState.country = null;
    applyAllFilters();
  });
}

if (clearGroupFilterBtn) {
  clearGroupFilterBtn.addEventListener('click', (evt) => {
    evt.preventDefault();
    highlightState.pathGroup = null;
    applyAllFilters();
  });
}

if (countryTable) {
  countryTable.addEventListener('click', (event) => {
    const row = event.target.closest('tr');
    if (!row) return;
    const code = row.dataset.country || row.querySelector('td')?.textContent || '';
    if (!code) return;
    setHighlightedCountry(code);
  });
}

if (recentTable) {
  recentTable.addEventListener('click', (event) => {
    const row = event.target.closest('tr');
    if (!row) return;
    const code = row.dataset.country;
    if (!code) return;
    setHighlightedCountry(code);
  });
}

// Initialize
loadAnalytics();
