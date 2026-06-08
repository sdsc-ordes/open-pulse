/* d3-viz.js — generic D3 chart layer for the query consoles.
 *
 * Spread into dbConsole()'s Alpine x-data exactly like window.treeTable():
 *
 *     ...window.treeTable(),
 *     ...window.d3Viz(),
 *
 * It only ever reads the SHARED result arrays the tree-table already owns
 * (this.columns + this.sortedRows()), so the same chart layer works for
 * every engine — cypher, sparql, opensearch — with zero per-engine code.
 *
 * All keys are namespaced (viewMode, axis*, _col*, _viz*, _d3*) so they
 * never shadow tree-table state or the Cypher graph-modal state
 * (graphOpen / graphMapping / openGraph / …).
 *
 * D3 itself is loaded page-locally from a CDN (see databases.html), the
 * same way overview.html loads Chart.js. The table view never needs D3,
 * so a slow/blocked CDN degrades gracefully to table-only.
 */
(function () {
  'use strict';

  // A string is temporal only with a real date separator (YYYY-MM minimum)
  // so bare integers like 1370 aren't mistaken for the year 1370.
  var TEMPORAL_RE = /^\d{4}-\d{2}(-\d{2})?([T ]\d{2}:\d{2})?/;

  function d3Viz() {
    return {
      // ── state ────────────────────────────────────────────────────────
      viewMode: 'table', // 'table' | 'timeseries' | 'scatter'
      axisX: '',
      axisY: '',
      axisColor: '',
      axisSize: '',
      _colTypes: {}, // col name -> 'numeric' | 'temporal' | 'categorical'
      _d3Ready: false,
      _themeObs: null,

      // ── lifecycle ────────────────────────────────────────────────────
      // Poll for the CDN D3 (mirrors overview.html _waitForChartJs). Call
      // from dbConsole.init(). Also wires a one-time observer so charts
      // recolour when the hub light/dark theme toggles.
      async _waitForD3() {
        for (var i = 0; i < 60 && typeof window.d3 === 'undefined'; i++) {
          await new Promise(function (r) { setTimeout(r, 50); });
        }
        this._d3Ready = typeof window.d3 !== 'undefined';
        if (this._d3Ready && !this._themeObs) {
          var self = this;
          this._themeObs = new MutationObserver(function () {
            if (self.viewMode !== 'table') self.renderViz();
          });
          this._themeObs.observe(document.documentElement, {
            attributes: true, attributeFilter: ['data-theme'],
          });
        }
      },

      // Called from run() once columns/rows have landed.
      onResult() {
        this._colTypes = this._inferColumnTypes();
        this._pickDefaultAxes();
        if (this.applicableViews().indexOf(this.viewMode) === -1) {
          this.viewMode = 'table'; // demote when the prior view no longer fits
        }
        if (this.viewMode !== 'table') {
          var self = this;
          this.$nextTick(function () { self.renderViz(); });
        }
      },

      onViewModeChange() {
        if (this.viewMode !== 'table' &&
            (this.columns.indexOf(this.axisX) === -1 ||
             this.columns.indexOf(this.axisY) === -1)) {
          this._pickDefaultAxes();
        }
        var self = this;
        this.$nextTick(function () { self.renderViz(); });
      },

      // ── column typing ────────────────────────────────────────────────
      _isTemporal(v) {
        if (v instanceof Date) return true;
        if (typeof v !== 'string') return false;
        return TEMPORAL_RE.test(v) && !isNaN(Date.parse(v));
      },
      _isNumeric(v) {
        if (typeof v === 'number') return isFinite(v);
        if (typeof v === 'string' && v.trim() !== '') return isFinite(Number(v));
        return false;
      },
      _num(v) {
        var n = typeof v === 'number' ? v : Number(v);
        return isFinite(n) ? n : null;
      },
      _inferColumnTypes() {
        var types = {};
        var rows = this.rows || [];
        var cols = this.columns || [];
        for (var ci = 0; ci < cols.length; ci++) {
          var temporal = 0, numeric = 0, n = 0;
          for (var i = 0; i < rows.length && n < 50; i++) {
            var v = rows[i][ci];
            if (v === null || v === undefined || v === '') continue;
            n++;
            if (typeof v === 'object') continue; // nested → categorical
            if (this._isTemporal(v)) temporal++;
            else if (this._isNumeric(v)) numeric++;
          }
          if (n === 0) types[cols[ci]] = 'categorical';
          else if (temporal / n >= 0.7) types[cols[ci]] = 'temporal';
          else if (numeric / n >= 0.7) types[cols[ci]] = 'numeric';
          else types[cols[ci]] = 'categorical';
        }
        return types;
      },
      _colsOfType(t) {
        var self = this;
        return (this.columns || []).filter(function (c) { return self._colTypes[c] === t; });
      },

      // Which views make sense for the current result.
      applicableViews() {
        var views = ['table'];
        var nums = this._colsOfType('numeric');
        // time series: an ordered x (anything) + at least one numeric y
        if (nums.length >= 1 && this.columns.length >= 2) views.push('timeseries');
        // scatter: two numeric axes
        if (nums.length >= 2) views.push('scatter');
        return views;
      },

      _pickDefaultAxes() {
        var nums = this._colsOfType('numeric');
        var temporal = this._colsOfType('temporal');
        var cats = this._colsOfType('categorical');
        if (this.viewMode === 'scatter') {
          this.axisX = nums[0] || this.columns[0] || '';
          this.axisY = nums[1] || nums[0] || '';
          this.axisColor = nums[2] || cats[0] || '';
          this.axisSize = nums[3] || '';
        } else {
          this.axisX = temporal[0] || cats[0] || this.columns[0] || '';
          var self = this;
          this.axisY = nums.filter(function (c) { return c !== self.axisX; })[0] || nums[0] || '';
          this.axisColor = '';
          this.axisSize = '';
        }
      },

      // ── rendering ────────────────────────────────────────────────────
      _cssVar(name, fallback) {
        var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return v || fallback || '';
      },

      renderViz() {
        if (this.viewMode === 'table') return;
        var d3 = window.d3;
        if (!d3 || !this.$refs.d3Root || !this.rows.length) return;
        var svg = d3.select(this.$refs.d3Root);
        svg.selectAll('*').remove();
        try {
          if (this.viewMode === 'timeseries') this._renderTimeSeries(d3, svg);
          else if (this.viewMode === 'scatter') this._renderScatter(d3, svg);
        } catch (e) {
          console.warn('d3 render failed:', e);
          svg.selectAll('*').remove();
          svg.append('text').attr('x', 14).attr('y', 26)
            .attr('fill', this._cssVar('--fg-muted', '#888')).attr('font-size', 12)
            .text('Could not plot this result — try different axes or the Table view.');
        }
      },

      _themeColors() {
        return {
          accent: this._cssVar('--accent', '#6ea8fe'),
          muted: this._cssVar('--fg-muted', '#8a8a8a'),
          border: this._cssVar('--border', '#444'),
        };
      },

      _styleAxis(g, c) {
        g.selectAll('text').attr('fill', c.muted).attr('font-size', 10);
        g.selectAll('line').attr('stroke', c.border);
        g.selectAll('path').attr('stroke', c.border);
      },

      _renderTimeSeries(d3, svg) {
        var xi = this.columns.indexOf(this.axisX);
        var yi = this.columns.indexOf(this.axisY);
        if (xi < 0 || yi < 0) return;
        var W = this.$refs.d3Root.clientWidth || 720, H = 360;
        var m = { t: 18, r: 20, b: 64, l: 66 };
        svg.attr('viewBox', '0 0 ' + W + ' ' + H).attr('width', '100%').attr('height', H);
        var c = this._themeColors();
        var self = this;
        var temporal = this._colTypes[this.axisX] === 'temporal';

        var data = this.sortedRows().map(function (r) {
          return { x: temporal ? new Date(r[xi]) : r[xi], y: self._num(r[yi]) };
        }).filter(function (d) { return d.y !== null && (!temporal || !isNaN(+d.x)); });
        if (temporal) data.sort(function (a, b) { return a.x - b.x; }); // line follows time, not table sort
        if (!data.length) return;

        var x = temporal
          ? d3.scaleTime().domain(d3.extent(data, function (d) { return d.x; })).range([m.l, W - m.r])
          : d3.scalePoint().domain(data.map(function (d) { return d.x; })).range([m.l, W - m.r]).padding(0.5);
        var y = d3.scaleLinear().domain([0, d3.max(data, function (d) { return d.y; }) || 1]).nice().range([H - m.b, m.t]);

        var gx = svg.append('g').attr('transform', 'translate(0,' + (H - m.b) + ')')
          .call(d3.axisBottom(x).ticks(Math.min(8, data.length)));
        if (!temporal) {
          gx.selectAll('text').attr('transform', 'rotate(-35)').attr('text-anchor', 'end')
            .attr('dx', '-0.4em').attr('dy', '0.3em');
        }
        var gy = svg.append('g').attr('transform', 'translate(' + m.l + ',0)').call(d3.axisLeft(y).ticks(6));
        this._styleAxis(gx, c); this._styleAxis(gy, c);

        var line = d3.line().x(function (d) { return x(d.x); }).y(function (d) { return y(d.y); });
        svg.append('path').datum(data).attr('fill', 'none').attr('stroke', c.accent)
          .attr('stroke-width', 2).attr('d', line);
        svg.append('g').selectAll('circle').data(data).join('circle')
          .attr('cx', function (d) { return x(d.x); }).attr('cy', function (d) { return y(d.y); })
          .attr('r', 3).attr('fill', c.accent)
          .append('title').text(function (d) {
            var xv = temporal ? d.x.toISOString().slice(0, 10) : d.x;
            return self.axisX + ': ' + xv + '\n' + self.axisY + ': ' + d.y;
          });
        this._axisLabels(svg, W, H, c);
      },

      _renderScatter(d3, svg) {
        var xi = this.columns.indexOf(this.axisX);
        var yi = this.columns.indexOf(this.axisY);
        if (xi < 0 || yi < 0) return;
        var ci = this.axisColor ? this.columns.indexOf(this.axisColor) : -1;
        var si = this.axisSize ? this.columns.indexOf(this.axisSize) : -1;
        var hasColor = ci >= 0;
        var W = this.$refs.d3Root.clientWidth || 720, H = 380;
        var m = { t: 18, r: hasColor ? 150 : 22, b: 64, l: 66 };
        svg.attr('viewBox', '0 0 ' + W + ' ' + H).attr('width', '100%').attr('height', H);
        var c = this._themeColors();
        var self = this;

        var data = this.sortedRows().map(function (r) {
          return {
            x: self._num(r[xi]), y: self._num(r[yi]),
            cv: hasColor ? r[ci] : null,
            sv: si >= 0 ? self._num(r[si]) : null,
            label: r[0],
          };
        }).filter(function (d) { return d.x !== null && d.y !== null; });
        if (!data.length) return;

        var x = d3.scaleLinear().domain(d3.extent(data, function (d) { return d.x; })).nice().range([m.l, W - m.r]);
        var y = d3.scaleLinear().domain(d3.extent(data, function (d) { return d.y; })).nice().range([H - m.b, m.t]);

        var colorNumeric = hasColor && this._colTypes[this.axisColor] === 'numeric';
        var colorFn = function () { return c.accent; };
        var ordinal = null, seq = null;
        if (colorNumeric) {
          seq = d3.scaleSequential(d3.interpolateViridis).domain(d3.extent(data, function (d) { return self._num(d.cv); }));
          colorFn = function (d) { return seq(self._num(d.cv)); };
        } else if (hasColor) {
          var domain = Array.from(new Set(data.map(function (d) { return String(d.cv); })));
          ordinal = d3.scaleOrdinal(d3.schemeCategory10).domain(domain);
          colorFn = function (d) { return ordinal(String(d.cv)); };
        }

        var radiusFn = function () { return 4.5; };
        if (si >= 0) {
          var se = d3.extent(data, function (d) { return d.sv; });
          var rs = d3.scaleSqrt().domain([Math.min(0, se[0] || 0), se[1] || 1]).range([3, 18]);
          radiusFn = function (d) { return rs(d.sv || 0); };
        }

        var gx = svg.append('g').attr('transform', 'translate(0,' + (H - m.b) + ')').call(d3.axisBottom(x).ticks(7));
        var gy = svg.append('g').attr('transform', 'translate(' + m.l + ',0)').call(d3.axisLeft(y).ticks(6));
        this._styleAxis(gx, c); this._styleAxis(gy, c);

        svg.append('g').selectAll('circle').data(data).join('circle')
          .attr('cx', function (d) { return x(d.x); }).attr('cy', function (d) { return y(d.y); })
          .attr('r', radiusFn).attr('fill', colorFn).attr('fill-opacity', 0.78)
          .attr('stroke', c.border).attr('stroke-width', 0.5)
          .append('title').text(function (d) {
            var t = (d.label != null ? d.label + '\n' : '') +
              self.axisX + ': ' + d.x + '\n' + self.axisY + ': ' + d.y;
            if (hasColor) t += '\n' + self.axisColor + ': ' + d.cv;
            if (si >= 0) t += '\n' + self.axisSize + ': ' + d.sv;
            return t;
          });

        // Categorical colour legend (numeric colour is a continuous ramp;
        // a discrete legend would be misleading, so we skip it there).
        if (hasColor && !colorNumeric && ordinal) {
          var lg = svg.append('g').attr('transform', 'translate(' + (W - m.r + 14) + ',' + m.t + ')');
          ordinal.domain().slice(0, 12).forEach(function (key, i) {
            var row = lg.append('g').attr('transform', 'translate(0,' + (i * 18) + ')');
            row.append('rect').attr('width', 10).attr('height', 10).attr('rx', 2).attr('fill', ordinal(key));
            row.append('text').attr('x', 15).attr('y', 9).attr('fill', c.muted).attr('font-size', 11)
              .text(key.length > 16 ? key.slice(0, 15) + '…' : key);
          });
        }
        this._axisLabels(svg, W, H, c);
      },

      _axisLabels(svg, W, H, c) {
        svg.append('text').attr('x', W / 2).attr('y', H - 6).attr('text-anchor', 'middle')
          .attr('fill', c.muted).attr('font-size', 11).text(this.axisX);
        svg.append('text').attr('transform', 'rotate(-90)').attr('x', -H / 2).attr('y', 15)
          .attr('text-anchor', 'middle').attr('fill', c.muted).attr('font-size', 11).text(this.axisY);
      },
    };
  }

  window.d3Viz = d3Viz;
})();
