/* Tree-aware table renderer.
 *
 * Shared by any page that needs to render a SPARQL/Cypher result set —
 * the Databases console uses it, Projects uses it. Provides:
 *
 *   - nested-table rendering for array-of-object cells
 *   - multi-row thead with rowspan/colspan when row cells are
 *     themselves objects (each top-level column expands per key)
 *   - per-column type override (auto / json / pretty / text / url / image)
 *     surfaced as a <select> embedded in the level-0 <th>
 *   - column sort with locale-aware numeric fall-back
 *
 * Consumed via Alpine ``x-data`` spread. A page mixes it into its data
 * function like this:
 *
 *   function projectsBuilder() {
 *     return {
 *       ...window.treeTable(),
 *       // page-specific state + methods on top
 *     };
 *   }
 *
 * The mixin owns ``columns`` / ``rows`` / ``truncated`` / ``sortColumn`` /
 * ``sortDir`` / ``cellTypes`` / ``treeMaxDepth``. Pages can override any
 * of them in their own object (Alpine merges by spread order — later
 * wins).
 *
 * Markup contract: the consuming template wraps the rendered table in
 * ``<div class="tree-table">…</div>`` so the scoped CSS in tree-table.css
 * applies, and binds the content via ``x-html="renderTreeTable()"``.
 * Header dropdown + sort use delegated handlers; the wrapper needs
 * ``@change="onCellTypeChange"`` and ``@click="onHeaderClick"``.
 */

window.treeTable = function () {
  return {
    // ── state ─────────────────────────────────────────────────
    columns: [],
    rows: [],
    truncated: false,
    sortColumn: null,
    sortDir: 'asc',           // 'asc' | 'desc'
    cellTypes: {},            // column-name → 'auto'|'json'|'pretty'|'text'|'url'|'image'
    treeMaxDepth: 5,          // safety cap on recursive subtree expansion

    // ── sorting ───────────────────────────────────────────────
    toggleSort(col) {
      if (this.sortColumn === col) {
        this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        this.sortColumn = col;
        this.sortDir = 'asc';
      }
    },
    clearSort() {
      this.sortColumn = null;
      this.sortDir = 'asc';
    },
    sortedRows() {
      if (!this.sortColumn) return this.rows;
      const idx = this.columns.indexOf(this.sortColumn);
      if (idx === -1) return this.rows;
      const dir = this.sortDir === 'desc' ? -1 : 1;
      // Copy first — Array.sort mutates and would re-trigger Alpine watchers.
      return [...this.rows].sort((a, b) => {
        const av = a[idx], bv = b[idx];
        // Stable handling of nulls / undefined: always last.
        const aN = av === null || av === undefined;
        const bN = bv === null || bv === undefined;
        if (aN && bN) return 0;
        if (aN) return 1;
        if (bN) return -1;
        // Numeric path when both sides parse as finite numbers.
        const an = Number(av), bn = Number(bv);
        if (Number.isFinite(an) && Number.isFinite(bn) && av !== '' && bv !== '') {
          return (an - bn) * dir;
        }
        // Fall back to locale-aware string compare.
        return String(av).localeCompare(String(bv), undefined, { numeric: true }) * dir;
      });
    },

    // ── meta helpers (consumed by the template's status line) ─
    cellLabel(v) {
      if (v === null || v === undefined) return '';
      if (typeof v === 'object') return JSON.stringify(v);
      return String(v);
    },

    // ── Tree-aware renderer ────────────────────────────────────
    // Single x-html blob: walks every cell to discover nested structure,
    // emits multi-row thead (object expansion) + nested <table>s for
    // array cells. Per-column type dropdowns are embedded in the
    // level-0 <th>; a delegated @change handler rewrites cellTypes.
    renderTreeTable() {
      const rows = this.sortedRows();
      const cols = this.columns;

      // Top-level subtrees honor the per-column type override.
      const subtrees = cols.map((c, j) =>
        this._buildTopSubtree(rows.map(r => r[j]), this.getCellType(c))
      );

      // Number of header rows = 1 (column name + dropdown) + max object
      // expansion depth. Leaves and arrays don't grow the header.
      const objDepth = (t) => {
        if (!t || t.kind !== 'object') return 0;
        return 1 + Math.max(0, ...Object.values(t.children).map(objDepth));
      };
      const headerLevels = 1 + Math.max(0, ...subtrees.map(objDepth));
      const headerGrid = Array.from({ length: headerLevels }, () => []);
      for (let j = 0; j < cols.length; j++) {
        this._addHeaderCells(cols[j], subtrees[j], 0, headerLevels - 1, headerGrid, /*topLevel=*/ true);
      }

      let html = '<table class="data tree">';
      html += '<thead>';
      for (const row of headerGrid) {
        html += '<tr>';
        for (const cell of row) {
          if (cell.topLevel) {
            const sortMark = this.sortColumn === cell.label
              ? (this.sortDir === 'asc' ? ' ↑' : ' ↓') : '';
            const safeCol = this._escape(cell.label);
            html += `<th colspan="${cell.colspan}" rowspan="${cell.rowspan}">`
                  + `<div class="th-with-type">`
                  +   `<span class="th-label" data-col-sort="${safeCol}" title="Sort by ${safeCol}">${safeCol}${sortMark}</span>`
                  +   this._typeSelectHtml(cell.label, this.getCellType(cell.label))
                  + `</div>`
                  + `</th>`;
          } else {
            html += `<th colspan="${cell.colspan}" rowspan="${cell.rowspan}">${this._escape(cell.label)}</th>`;
          }
        }
        html += '</tr>';
      }
      html += '</thead><tbody>';

      for (const row of rows) {
        html += '<tr>';
        for (let j = 0; j < cols.length; j++) {
          html += this._renderCells(row[j], subtrees[j]);
        }
        html += '</tr>';
      }
      html += '</tbody></table>';
      return html;
    },

    getCellType(col) {
      return this.cellTypes[col] || 'auto';
    },

    onCellTypeChange(e) {
      const col = e.target && e.target.getAttribute && e.target.getAttribute('data-col-type');
      if (!col) return;
      // Replace object so Alpine sees a property change (not a deep mutation).
      this.cellTypes = { ...this.cellTypes, [col]: e.target.value };
    },

    onHeaderClick(e) {
      // Delegated click handler for the sort affordance. Only fires for
      // elements explicitly tagged data-col-sort (the column-name span);
      // the type-selector dropdown sits next to it but doesn't carry the
      // attr, so clicking the dropdown won't trigger a sort.
      const col = e.target && e.target.getAttribute && e.target.getAttribute('data-col-sort');
      if (!col) return;
      this.toggleSort(col);
    },

    _typeSelectHtml(col, current) {
      const opts = ['auto', 'json', 'pretty', 'text', 'url', 'image'];
      const safeCol = this._escape(col);
      const options = opts.map(t =>
        `<option value="${t}"${t === current ? ' selected' : ''}>${t}</option>`
      ).join('');
      return `<select class="th-type" data-col-type="${safeCol}" title="Render this column as…">${options}</select>`;
    },

    // Top-level subtree honors the user's override:
    //   url/image/text/pretty → leaf with a render hint
    //   json                  → object/array expansion, mixed shapes allowed
    //   auto                  → object/array expansion, mixed → leaf (safe default)
    _buildTopSubtree(values, type) {
      if (type === 'text' || type === 'pretty' || type === 'url' || type === 'image') {
        return { kind: 'leaf', renderAs: type };
      }
      return this._buildSubtree(values, 0, /*allowMixed=*/ type === 'json');
    },

    // Recursive shape detector. `allowMixed` lets json-typed columns
    // expand even when some rows have primitives or arrays alongside
    // objects — primitive rows just render empty for the sub-keys.
    _buildSubtree(values, depth, allowMixed) {
      if (depth >= this.treeMaxDepth) return { kind: 'leaf' };
      let hasObj = false, hasArr = false, hasPrim = false;
      const keys = new Set();
      const arrItems = [];
      for (const v of values) {
        if (v === null || v === undefined) continue;
        if (Array.isArray(v)) {
          hasArr = true;
          for (const it of v) arrItems.push(it);
        } else if (typeof v === 'object') {
          hasObj = true;
          for (const k of Object.keys(v)) keys.add(k);
        } else {
          hasPrim = true;
        }
      }
      if (!allowMixed) {
        if (hasObj && hasPrim) return { kind: 'leaf' };
        if (hasArr && (hasObj || hasPrim)) return { kind: 'leaf' };
      }
      // Forced-expand under json: pick whichever shape dominates;
      // arrays beat objects since "tabla en tabla" reads better than
      // "expanded keys with empty cells for the array rows".
      if (hasArr) return { kind: 'array', child: this._buildSubtree(arrItems, depth + 1, allowMixed) };
      if (hasObj) {
        const children = {};
        for (const k of keys) {
          const sub = values.map(v =>
            (v && typeof v === 'object' && !Array.isArray(v)) ? v[k] : undefined
          );
          children[k] = this._buildSubtree(sub, depth + 1, allowMixed);
        }
        return { kind: 'object', children };
      }
      return { kind: 'leaf' };
    },

    _leafCount(t) {
      if (!t || t.kind !== 'object') return 1;
      return Object.values(t.children).reduce((a, c) => a + this._leafCount(c), 0);
    },

    _addHeaderCells(label, subtree, level, maxLevel, grid, topLevel) {
      if (subtree.kind !== 'object') {
        grid[level].push({
          label,
          colspan: this._leafCount(subtree),
          rowspan: maxLevel - level + 1,
          topLevel: !!topLevel,
        });
        return;
      }
      grid[level].push({
        label,
        colspan: this._leafCount(subtree),
        rowspan: 1,
        topLevel: !!topLevel,
      });
      for (const k of Object.keys(subtree.children)) {
        this._addHeaderCells(k, subtree.children[k], level + 1, maxLevel, grid, /*topLevel=*/ false);
      }
    },

    _renderCells(value, subtree) {
      if (subtree.kind === 'leaf') {
        return this._renderLeaf(value, subtree.renderAs);
      }
      if (subtree.kind === 'object') {
        let out = '';
        for (const k of Object.keys(subtree.children)) {
          const sub = (value && typeof value === 'object' && !Array.isArray(value)) ? value[k] : undefined;
          out += this._renderCells(sub, subtree.children[k]);
        }
        return out;
      }
      if (subtree.kind === 'array') {
        const items = Array.isArray(value) ? value : [];
        if (!items.length) return '<td class="card-meta" style="font-style: italic;">∅</td>';
        const child = subtree.child;
        if (child.kind === 'object') {
          const keys = Object.keys(child.children);
          const objDepth = (t) => (!t || t.kind !== 'object') ? 0 : 1 + Math.max(0, ...Object.values(t.children).map(objDepth));
          const innerLevels = 1 + Math.max(0, ...keys.map(k => objDepth(child.children[k])));
          const innerGrid = Array.from({ length: innerLevels }, () => []);
          for (const k of keys) {
            this._addHeaderCells(k, child.children[k], 0, innerLevels - 1, innerGrid, /*topLevel=*/ false);
          }
          let inner = '<table class="nested">';
          inner += '<thead>';
          for (const r of innerGrid) {
            inner += '<tr>';
            for (const c of r) {
              inner += `<th colspan="${c.colspan}" rowspan="${c.rowspan}">${this._escape(c.label)}</th>`;
            }
            inner += '</tr>';
          }
          inner += '</thead><tbody>';
          for (const item of items) {
            inner += '<tr>';
            for (const k of keys) {
              const sub = (item && typeof item === 'object' && !Array.isArray(item)) ? item[k] : undefined;
              inner += this._renderCells(sub, child.children[k]);
            }
            inner += '</tr>';
          }
          inner += '</tbody></table>';
          return `<td class="nested-cell">${inner}</td>`;
        }
        // Array of primitives or array-of-arrays: stacked list.
        let html = '<td class="nested-cell"><div class="nested-list">';
        for (const item of items) {
          if (Array.isArray(item) || (item && typeof item === 'object')) {
            html += `<div>${this._escape(JSON.stringify(item))}</div>`;
          } else {
            html += `<div>${this._escape(item === null || item === undefined ? '' : String(item))}</div>`;
          }
        }
        html += '</div></td>';
        return html;
      }
      return '<td></td>';
    },

    // Leaf renderer. `renderAs` (set on the subtree by _buildTopSubtree)
    // selects the variant; undefined falls back to the default which is
    // "single-line text / JSON-stringified objects" — same as the auto
    // heuristic produced before per-column types existed.
    _renderLeaf(value, renderAs) {
      if (value === null || value === undefined) {
        return '<td class="mono" style="font-size: 12px;"></td>';
      }
      if (renderAs === 'url' && typeof value === 'string' && value) {
        const safe = this._escape(value);
        return `<td><a href="${safe}" target="_blank" rel="noopener" class="cell-url">${safe}</a></td>`;
      }
      if (renderAs === 'image' && typeof value === 'string' && value) {
        const safe = this._escape(value);
        return `<td class="cell-img-cell"><img src="${safe}" loading="lazy" alt="" class="cell-img"/></td>`;
      }
      if (renderAs === 'pretty') {
        const text = typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
        return `<td><pre class="cell-pretty">${this._escape(text)}</pre></td>`;
      }
      // text / auto leaf / fallback
      const label = typeof value === 'object' ? JSON.stringify(value) : String(value);
      return `<td class="mono" style="font-size: 12px;">${this._escape(label)}</td>`;
    },

    _escape(s) {
      if (s === null || s === undefined) return '';
      return String(s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
      ));
    },
  };
};
