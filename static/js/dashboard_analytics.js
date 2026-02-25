/* admin_analytics.js — Data processing for admin analytics */

// Constants
const ACTIVE_USER_WINDOW_MIN = 10;

function normalizeUser(value) {
  const clean = (value || '').trim();
  return clean ? clean : 'anonymous';
}

function buildTotals(rows) {
  const users = new Set();
  const ips = new Set();
  const activeUsers = new Set();
  const cutoff = Date.now() - ACTIVE_USER_WINDOW_MIN * 60 * 1000;
  (rows || []).forEach((row) => {
    users.add(normalizeUser(row?.username));
    const ip = (row?.ip || '').trim();
    if (ip) ips.add(ip);
    if (row?.ts) {
      const ts = new Date(row.ts).getTime();
      if (!Number.isNaN(ts) && ts >= cutoff) {
        activeUsers.add(normalizeUser(row?.username));
      }
    }
  });
  return {
    unique_users: users.size,
    unique_ips: ips.size,
    active_users: activeUsers.size,
  };
}

function buildCountries(rows) {
  const tally = new Map();
  (rows || []).forEach((row) => {
    const code = (row?.country || '').trim();
    const key = code || '';
    tally.set(key, (tally.get(key) || 0) + 1);
  });
  return Array.from(tally.entries())
    .map(([country, count]) => ({ country, count }))
    .sort((a, b) => b.count - a.count);
}

function buildGroupSummary(rows) {
  const summary = new Map();
  (rows || []).forEach((row) => {
    const group = (row?.group_label || 'App').trim() || 'App';
    const method = (row?.method || 'GET').toUpperCase();
    const entry = summary.get(group) || { group, total: 0, methods: {} };
    entry.total += 1;
    entry.methods[method] = (entry.methods[method] || 0) + 1;
    summary.set(group, entry);
  });
  return Array.from(summary.values()).sort((a, b) => b.total - a.total);
}

function buildGroupDetails(rows, limit = 10) {
  const grouped = new Map();
  (rows || []).forEach((row) => {
    const group = (row?.group_label || 'App').trim() || 'App';
    const subgroup = (row?.subgroup_label || 'Other').trim() || 'Other';
    const method = (row?.method || 'GET').toUpperCase();
    if (!grouped.has(group)) grouped.set(group, new Map());
    const groupBucket = grouped.get(group);
    const entry = groupBucket.get(subgroup) || { subgroup, total: 0, methods: {} };
    entry.total += 1;
    entry.methods[method] = (entry.methods[method] || 0) + 1;
    groupBucket.set(subgroup, entry);
  });
  const detailMap = {};
  grouped.forEach((entries, group) => {
    const ordered = Array.from(entries.values()).sort((a, b) => b.total - a.total);
    detailMap[group] = ordered.slice(0, limit);
  });
  return detailMap;
}

function formatUserAgent(row) {
  const browser = row.ua_browser || 'Unknown';
  const version = row.ua_browser_ver ? ` ${row.ua_browser_ver}` : '';
  const os = row.ua_os || '';
  const device = row.ua_device || '';
  const parts = [];
  if (device) parts.push(device);
  if (os) parts.push(os);
  const browserText = `${browser}${version}`.trim();
  if (browserText) {
    parts.push(browserText);
  }
  return parts.length ? parts.join(' • ') : 'Unknown';
}

function formatTimestamp(raw) {
  if (!raw) return '—';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  const yy = String(parsed.getUTCFullYear()).slice(-2);
  const mm = String(parsed.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(parsed.getUTCDate()).padStart(2, '0');
  const hh = String(parsed.getUTCHours()).padStart(2, '0');
  const mi = String(parsed.getUTCMinutes()).padStart(2, '0');
  const ss = String(parsed.getUTCSeconds()).padStart(2, '0');
  return `${yy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
}

// Export as global
window.DashboardAnalytics = {
  normalizeUser,
  buildTotals,
  buildCountries,
  buildGroupSummary,
  buildGroupDetails,
  formatUserAgent,
  formatTimestamp,
};
