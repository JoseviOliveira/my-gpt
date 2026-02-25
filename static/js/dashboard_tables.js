/* admin_tables.js — Table rendering and pagination */

const DEFAULT_ROWS_PER_PAGE = 50;

let currentPage = 1;
let pageSize = DEFAULT_ROWS_PER_PAGE;
let lastDetailedRows = [];

function renderTable(bodyEl, rows, columns, opts = {}) {
  if (!bodyEl) return;
  bodyEl.innerHTML = '';
  rows.forEach((row) => {
    const tr = document.createElement('tr');
    if (opts.rowClass) {
      tr.className = `${tr.className} ${opts.rowClass}`.trim();
    }
    if (opts.rowTitle) {
      tr.title = typeof opts.rowTitle === 'function' ? opts.rowTitle(row) : opts.rowTitle;
    }
    columns.forEach((col) => {
      const td = document.createElement('td');
      td.className = 'py-2 pr-3 text-slate-700 dark:text-slate-200';
      td.textContent = typeof col === 'function' ? col(row) : row[col] ?? '—';
      tr.appendChild(td);
    });
    bodyEl.appendChild(tr);
    if (typeof opts.setDataAttr === 'function') {
      const attrs = opts.setDataAttr(row) || {};
      Object.entries(attrs).forEach(([key, value]) => {
        if (value === undefined || value === null || value === '') {
          delete tr.dataset[key];
        } else {
          tr.dataset[key] = value;
        }
      });
    }
  });
}

function renderDetailedTable(rows, recentTable, pageInfoEl, pagePrevBtn, pageNextBtn, pageSizeInput) {
  const size = Math.max(25, Number(pageSizeInput?.value || pageSize) || DEFAULT_ROWS_PER_PAGE);
  pageSize = size;
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  currentPage = Math.min(currentPage, totalPages);
  const start = (currentPage - 1) * pageSize;
  const pageRows = rows.slice(start, start + pageSize);
  
  if (pageInfoEl) {
    pageInfoEl.textContent = `Page ${currentPage} of ${totalPages}`;
  }
  if (pagePrevBtn) pagePrevBtn.disabled = currentPage <= 1;
  if (pageNextBtn) pageNextBtn.disabled = currentPage >= totalPages;
  
  lastDetailedRows = rows;
  return { pageRows, currentPage, totalPages };
}

function setCurrentPage(page) {
  currentPage = page;
}

function getCurrentPage() {
  return currentPage;
}

function getTablePageSize() {
  return pageSize;
}

function getLastDetailedRows() {
  return lastDetailedRows;
}

// Export as global
window.DashboardTables = {
  renderTable,
  renderDetailedTable,
  setCurrentPage,
  getCurrentPage,
  getPageSize: getTablePageSize,
  getLastDetailedRows,
  DEFAULT_ROWS_PER_PAGE,
};
