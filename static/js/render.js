/*
 * render.js — formatting utilities for assistant output
 * - Escapes/sanitizes text before inserting into the DOM
 * - Splits math vs plain text to preserve TeX segments
 * - Applies lightweight Markdown for headings/inline styles
 * - Converts Markdown tables into small HTML tables
 * - Pure helpers returning HTML strings (no state mutations)
 */

// Escapes HTML entities so rendered assistant text stays safe.
function escapeHtml(s){
    return (s || '').replace(/[&<>"']/g, m => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[m]));
  }
  
  // Split into math vs text segments so we don't markdown-format inside TeX
  // Splits text into math and non-math segments to avoid formatting TeX.
  function splitMathSegments(s){
    const segs = [];
    let i = 0;
    const re = /(\\\[|\\\]|\\\(|\\\)|\$\$|\$)/g; // \[, \], \(, \), $$, $
    let m, stack = null;
  
    while ((m = re.exec(s)) !== null) {
      const tok = m[0];
      if (!stack) {
        if (tok === '\\[' || tok === '\\(' || tok === '$$' || tok === '$') {
          if (m.index > i) segs.push({ t: 'text', s: s.slice(i, m.index) });
          stack = { open: tok, start: re.lastIndex };
        }
      } else {
        const open = stack.open;
        const isClose =
          (open === '\\[' && tok === '\\]') ||
          (open === '\\(' && tok === '\\)') ||
          (open === '$$'  && tok === '$$') ||
          (open === '$'   && tok === '$');
        if (isClose) {
          segs.push({ t: 'math', s: open + s.slice(stack.start, m.index) + tok });
          stack = null;
          i = re.lastIndex;
        }
      }
    }
    if (stack) segs.push({ t: 'text', s: s.slice(i) });
    else if (i < s.length) segs.push({ t: 'text', s: s.slice(i) });
    return segs;
  }
  
  // Markdown (headings/bold/italic/code) — but never inside TeX segments
  // Applies lightweight Markdown formatting outside of math segments.
  function formatInline(raw){
    const segs = splitMathSegments(raw || '');
    return segs.map(seg => {
      if (seg.t === 'math') return seg.s; // keep TeX intact
      let x = escapeHtml(seg.s);
  
      // Headings (longest → shortest)
      x = x.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
      x = x.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
      x = x.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
  
      // Bold / italic / code
      x = x.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
      x = x.replace(/__(.+?)__/g,'<strong>$1</strong>');
      x = x.replace(/(^|[^\*])\*(?!\s)(.+?)(?<!\s)\*(?!\*)/g,'$1<em>$2</em>');
      x = x.replace(/(^|[^_])_(?!\s)(.+?)(?<!\s)_(?!_)/g,'$1<em>$2</em>');
      x = x.replace(/`([^`]+)`/g,'<code>$1</code>');
      return x;
    }).join('');
  }
  
  // Table cell: normalize line breaks safely (don’t break \\nu, \\nabla…)
  // Normalizes line breaks in a table cell before inline formatting.
  function renderCell(raw){
    const s = (raw || '')
      .replace(/<br\s*\/?>(?=\s*|$)/gi, '\n')
      .replace(/\\n(?![A-Za-z])/g, '\n');
    const html = formatInline(s);
    return html.replace(/\n/g, '<br>');
  }
  
  // Converts Markdown tables in a chat message into HTML markup.
  function renderTables(text){
    const re = /(^\|.*\|\s*\n\|[ :\-|\t]+\|\s*\n(?:\|.*\|\s*\n)+)/gm;
    const blocks = [];
    let lastIndex = 0, m;
    while((m = re.exec(text)) !== null){
      const [block] = m;
      blocks.push({start:m.index, end:m.index+block.length, html: mdTableToHTML(block)});
    }
    if(!blocks.length) return formatInline(text);
    let out = '';
    for(const b of blocks){
      out += formatInline(text.slice(lastIndex, b.start));
      out += b.html;
      lastIndex = b.end;
    }
    out += formatInline(text.slice(lastIndex));
    return out;
  }
  
  // Builds the HTML structure for a single Markdown table block.
  function mdTableToHTML(block){
    const lines = block.trim().split('\n').filter(l=>/^\|/.test(l));
    if(lines.length < 2) return formatInline(block);
    const header = splitRow(lines[0]);
    const align = parseAlign(lines[1], header.length);
    const rows = lines.slice(2).map(splitRow);
    const thead = '<thead><tr>'+header.map((h,i)=>`<th style="${align[i]}">${renderCell(h)}</th>`).join('')+'</tr></thead>';
    const tbody = '<tbody>'+rows.map(r=>'<tr>'+r.map((c,i)=>`<td style="${align[i]}">${renderCell(c)}</td>`).join('')+'</tr>').join('')+'</tbody>';
    return `<div class="table-wrap"><table>${thead}${tbody}</table></div>`;
  }
  
  // Splits a pipe-delimited table row into trimmed cell strings.
  function splitRow(line){
    return (line||'').replace(/^\||\|$/g,'').split('|').map(s=>s.trim());
  }
  
  // Determines per-column alignment styles from the Markdown separator row.
  function parseAlign(line, n){
    const raw = splitRow(line||'');
    const arr = raw.map(x=>{
      const hasLeft = x.startsWith(':'), hasRight = x.endsWith(':');
      if(hasLeft && hasRight) return 'text-align:center';
      if(hasRight) return 'text-align:right';
      return 'text-align:left';
    });
    while(arr.length<n) arr.push('text-align:left');
    return arr;
  }
