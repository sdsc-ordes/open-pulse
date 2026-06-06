/* agent-chat.js — full-page agent chat client.
 *
 * window.agentChat() is an Alpine factory mounted by templates/agent.html.
 * It streams /api/ai/chat (tools_enabled — the SPARQL / Cypher /
 * OpenSearch / DuckDB tool belt) and renders the reply richly:
 *   - GitHub-flavoured Markdown   → marked + DOMPurify (XSS-safe)
 *   - code blocks                 → highlight.js
 *   - images                      → markdown ![](…)
 *   - tool calls                  → collapsible result cards (table / JSON)
 *   - ```vega-lite / ```vega      → rendered charts via vega-embed
 *   - ```html                     → "Render" → sandboxed <iframe>
 *
 * All CDN libs degrade gracefully: if marked/DOMPurify are missing the
 * chat falls back to escaped text; if vega/iframe libs are missing the
 * blocks just stay as highlighted code.
 */
(function () {
  'use strict';

  const STORAGE = 'op:agent:chat:v1';

  function agentChat() {
    return {
      // ── state ────────────────────────────────────────────────────────
      messages: [],
      input: '',
      partial: '',
      busy: false,
      error: '',
      model: '',
      defaultModel: '',
      toolsEnabled: true,
      _markedReady: false,
      // agent + tool selection ("checkpoints")
      tools: [
        { id: 'run_duckdb', label: 'DuckDB', on: true },
        { id: 'run_opensearch', label: 'OpenSearch', on: true },
        { id: 'run_cypher', label: 'Cypher', on: true },
        { id: 'run_sparql', label: 'SPARQL', on: true },
        { id: 'gme_search', label: 'GME search', on: true },
        // Job-spawning actions — off by default; opt in to let the agent
        // kick off an extraction / crawl.
        { id: 'extract_metadata', label: 'Extract · gimie', on: false, action: true },
        { id: 'run_crawler', label: 'Crawler', on: false, action: true },
      ],
      models: [],
      settingsOpen: false,
      // attached files (server-side under /tmp) + freeform context note
      files: [],
      uploading: false,
      contextNote: '',
      // slash-command palette
      slashOpen: false,
      slashItems: [],
      slashIdx: 0,

      // ── lifecycle ────────────────────────────────────────────────────
      init() {
        this.defaultModel = (window.OP_AGENT_DEFAULT_MODEL || '').trim();
        this.model = localStorage.getItem(STORAGE + ':model') || this.defaultModel;
        try {
          const raw = localStorage.getItem(STORAGE);
          if (raw) this.messages = JSON.parse(raw) || [];
        } catch (_) { this.messages = []; }
        try {
          const t = JSON.parse(localStorage.getItem(STORAGE + ':tools') || 'null');
          if (Array.isArray(t)) {
            this.tools.forEach((x) => { const f = t.find((y) => y.id === x.id); if (f) x.on = !!f.on; });
          }
        } catch (_) { /* keep defaults */ }
        this.contextNote = localStorage.getItem(STORAGE + ':note') || '';
        this.loadModels();
        this.refreshFiles();
        // Enhance any restored messages once the DOM + CDN libs are ready.
        this.$nextTick(() => this._enhanceAll());
        this._scroll();
      },

      persist() {
        try {
          localStorage.setItem(STORAGE, JSON.stringify(this.messages.slice(-120)));
        } catch (_) { /* quota — best effort */ }
      },

      clearChat() {
        this.messages = [];
        this.partial = '';
        this.error = '';
        this.persist();
      },

      // ── sending ──────────────────────────────────────────────────────
      async send() {
        const text = this.input.trim();
        if (!text || this.busy) return;
        this.messages.push({ role: 'user', content: text });
        this.input = '';
        this.persist();
        this._autosize();
        this._scroll();
        await this._completion();
      },

      // Map the display-shaped history to the strict OpenAI schema the
      // upstream expects (assistant-with-tool_calls keeps content null;
      // tool messages carry a tool_call_id + string content).
      _messagesForUpstream() {
        return this.messages
          .filter((m) => m && m.role)
          .map((m) => {
            if (m.role === 'tool') {
              return {
                role: 'tool',
                tool_call_id: m.tool_call_id || '',
                content:
                  typeof m.content === 'string'
                    ? m.content
                    : JSON.stringify(m.content || {}),
              };
            }
            if (m.role === 'assistant' && m.tool_calls) {
              return {
                role: 'assistant',
                tool_calls: m.tool_calls,
                content: m.content || null,
              };
            }
            return { role: m.role, content: m.content || '' };
          });
      },

      async _completion() {
        this.busy = true;
        this.partial = '';
        this.error = '';
        if (this.model) localStorage.setItem(STORAGE + ':model', this.model);
        try {
          const resp = await fetch('/api/ai/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              messages: this._messagesForUpstream(),
              model: this.model || undefined,
              temperature: 0.3,
              tools_enabled: this.toolsEnabled,
              tool_names: this.enabledToolNames(),
              context: this._buildContext(),
            }),
          });
          if (!resp.ok || !resp.body) {
            this.messages.push({
              role: 'assistant',
              content: `⚠ Request failed (HTTP ${resp.status}). Check the LLM endpoint under Settings.`,
            });
            this.persist();
            return;
          }
          await this._consumeStream(resp.body.getReader());
        } catch (e) {
          this.messages.push({ role: 'assistant', content: `⚠ ${e.message}` });
          this.persist();
        } finally {
          this.busy = false;
          this.partial = '';
          this.$nextTick(() => { this._enhanceAll(); this._scroll(); });
        }
      },

      // SSE consumer — mirrors the contract in routes/ai.py: OpenAI-style
      // content deltas + synthetic ``op_tool`` frames for tool results +
      // ``{error}`` frames. The agentic loop may emit several assistant /
      // tool turns within one stream.
      async _consumeStream(reader) {
        const decoder = new TextDecoder();
        let buf = '';
        let accumulated = '';
        const toolCallsAcc = [];
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) !== -1) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const payload = frame
              .split('\n')
              .filter((l) => l.startsWith('data:'))
              .map((l) => l.slice(5).trim())
              .join('');
            if (!payload || payload === '[DONE]') continue;
            let obj;
            try { obj = JSON.parse(payload); } catch (_) { continue; }
            if (obj.error) {
              accumulated += `\n\n⚠ ${obj.error}`;
              this.partial = accumulated;
              continue;
            }
            // Tool-result frame → flush the in-progress assistant turn,
            // then push a tool message the UI renders as a card.
            if (obj.op_tool) {
              if (accumulated || toolCallsAcc.length) {
                this.messages.push({
                  role: 'assistant',
                  content: accumulated,
                  tool_calls: toolCallsAcc.length ? toolCallsAcc.slice() : undefined,
                });
                accumulated = '';
                toolCallsAcc.length = 0;
              }
              this.messages.push({
                role: 'tool',
                tool_call_id: obj.tool_call_id,
                name: obj.name,
                arguments: obj.arguments,
                content: obj.result,
              });
              this.partial = '';
              this.persist();
              this.$nextTick(() => { this._enhanceAll(); this._scroll(); });
              continue;
            }
            const choice = (obj.choices || [])[0] || {};
            const delta = choice.delta || {};
            if (delta.content) {
              accumulated += delta.content;
              this.partial = accumulated;
              this._scroll();
            }
            for (const tc of delta.tool_calls || []) {
              const i = tc.index || 0;
              while (toolCallsAcc.length <= i) {
                toolCallsAcc.push({ id: '', type: 'function', function: { name: '', arguments: '' } });
              }
              const slot = toolCallsAcc[i];
              if (tc.id) slot.id = tc.id;
              if (tc.type) slot.type = tc.type;
              if (tc.function?.name) slot.function.name = tc.function.name;
              if (tc.function?.arguments) slot.function.arguments += tc.function.arguments;
            }
          }
        }
        if (accumulated || toolCallsAcc.length) {
          this.messages.push({
            role: 'assistant',
            content: accumulated,
            tool_calls: toolCallsAcc.length ? toolCallsAcc.slice() : undefined,
          });
          this.persist();
        }
      },

      // ── markdown rendering ───────────────────────────────────────────
      renderMessage(content) {
        if (typeof window.marked === 'undefined' || typeof window.DOMPurify === 'undefined') {
          return '<pre>' + this._esc(content) + '</pre>';
        }
        this._initMarkedOnce();
        const html = window.marked.parse(String(content || ''));
        return window.DOMPurify.sanitize(html, {
          ADD_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td'],
          ADD_ATTR: ['class', 'data-lang', 'target', 'rel'],
          // Allow inline data: images (charts / small assets the model emits).
          ADD_DATA_URI_TAGS: ['img'],
        });
      },
      _esc(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      },
      _initMarkedOnce() {
        if (this._markedReady) return;
        this._markedReady = true;
        const esc = this._esc;
        window.marked.use({
          gfm: true,
          breaks: true,
          renderer: {
            code(token) {
              const code = typeof token === 'object' ? token.text : token;
              const lang = ((typeof token === 'object' ? token.lang : arguments[1]) || '')
                .toLowerCase().trim();
              // Leave vega / html fences as plain tagged blocks — the
              // post-render enhancer turns them into charts / iframes.
              if (!window.hljs || ['vega-lite', 'vega', 'html'].includes(lang)) {
                return `<pre><code class="language-${esc(lang)}">${esc(code)}</code></pre>`;
              }
              let highlighted, actual = lang;
              if (lang && window.hljs.getLanguage(lang)) {
                highlighted = window.hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
              } else {
                const a = window.hljs.highlightAuto(code);
                highlighted = a.value; actual = a.language || '';
              }
              return `<pre><code class="hljs language-${esc(actual)}">${highlighted}</code></pre>`;
            },
          },
        });
      },

      // Render a tool message's result as a compact table (when it has
      // columns/rows) or pretty JSON otherwise.
      renderToolResult(m) {
        let res = m.content;
        if (typeof res === 'string') {
          try { res = JSON.parse(res); } catch (_) { return '<pre>' + this._esc(m.content) + '</pre>'; }
        }
        if (res && Array.isArray(res.columns) && Array.isArray(res.rows)) {
          const cols = res.columns;
          const rows = res.rows.slice(0, 20);
          const cell = (v) =>
            v == null ? '' : typeof v === 'object' ? this._esc(JSON.stringify(v)) : this._esc(String(v));
          let html = '<div class="agent-tool-meta">' +
            this._esc(`${res.engine || 'tool'} · ${res.row_count != null ? res.row_count + ' rows' : ''}` +
              (res.elapsed_ms != null ? ` · ${res.elapsed_ms} ms` : '') +
              (res.truncated ? ' · truncated' : '')) + '</div>';
          html += '<div class="agent-tool-table"><table><thead><tr>' +
            cols.map((c) => `<th>${this._esc(c)}</th>`).join('') + '</tr></thead><tbody>' +
            rows.map((r) => '<tr>' + cols.map((_, i) => `<td>${cell(r[i])}</td>`).join('') + '</tr>').join('') +
            '</tbody></table></div>';
          if (res.rows.length > rows.length) {
            html += `<div class="agent-tool-meta">…${res.rows.length - rows.length} more rows</div>`;
          }
          return html;
        }
        return '<pre>' + this._esc(JSON.stringify(res, null, 2)) + '</pre>';
      },
      toolArgs(m) {
        if (m.arguments && typeof m.arguments === 'object') {
          return m.arguments.query || m.arguments.sql || JSON.stringify(m.arguments);
        }
        return String(m.arguments || '');
      },

      // ── post-render enhancement (charts + html sandbox) ──────────────
      enhance(el) {
        if (!el || el.getAttribute('data-enhanced') === '1') return;
        if (window.hljs) {
          el.querySelectorAll('pre code:not(.hljs)').forEach((c) => {
            if (![...c.classList].some((k) => k.startsWith('language-vega') || k === 'language-html')) {
              try { window.hljs.highlightElement(c); } catch (_) { /* ignore */ }
            }
          });
        }
        this._renderVega(el);
        this._renderHtmlBlocks(el);
        el.setAttribute('data-enhanced', '1');
      },
      _enhanceAll() {
        document.querySelectorAll('.agent-msg-body:not([data-enhanced="1"])').forEach((el) => this.enhance(el));
      },
      _renderVega(el) {
        if (typeof window.vegaEmbed === 'undefined') return;
        el.querySelectorAll('code.language-vega-lite, code.language-vega').forEach((code) => {
          let spec;
          try { spec = JSON.parse(code.textContent); } catch (_) { return; }
          const isLite = code.classList.contains('language-vega-lite');
          const host = document.createElement('div');
          host.className = 'agent-vega';
          const pre = code.closest('pre');
          (pre || code).replaceWith(host);
          window.vegaEmbed(host, spec, {
            actions: false,
            mode: isLite ? 'vega-lite' : 'vega',
            theme: (document.documentElement.dataset.theme === 'light') ? undefined : 'dark',
          }).catch((e) => { host.innerHTML = '<div class="agent-vega-err">chart error: ' + this._esc(e.message) + '</div>'; });
        });
      },
      _renderHtmlBlocks(el) {
        el.querySelectorAll('code.language-html').forEach((code) => {
          const pre = code.closest('pre');
          if (!pre) return;
          const src = code.textContent;
          const wrap = document.createElement('div');
          wrap.className = 'agent-html';
          const bar = document.createElement('div');
          bar.className = 'agent-html-bar';
          const btn = document.createElement('button');
          btn.className = 'btn btn-ghost';
          btn.textContent = '▶ Render HTML';
          bar.appendChild(btn);
          const codeWrap = document.createElement('div');
          codeWrap.appendChild(pre.cloneNode(true));
          wrap.appendChild(bar);
          wrap.appendChild(codeWrap);
          pre.replaceWith(wrap);
          let on = false;
          btn.addEventListener('click', () => {
            on = !on;
            btn.textContent = on ? '✕ Hide render' : '▶ Render HTML';
            codeWrap.style.display = on ? 'none' : '';
            let frame = wrap.querySelector('iframe');
            if (on && !frame) {
              frame = document.createElement('iframe');
              frame.className = 'agent-html-frame';
              // Sandboxed: scripts allowed for interactive demos, but no
              // same-origin access (can't read cookies / call the hub API).
              frame.setAttribute('sandbox', 'allow-scripts');
              frame.srcdoc = window.DOMPurify
                ? window.DOMPurify.sanitize(src, { WHOLE_DOCUMENT: true, ADD_TAGS: ['style'] })
                : src;
              wrap.appendChild(frame);
            } else if (frame) {
              frame.style.display = on ? '' : 'none';
            }
          });
        });
      },

      // ── small UI helpers ─────────────────────────────────────────────
      onKeydown(e) {
        if (this.slashOpen && this.slashItems.length) {
          if (e.key === 'ArrowDown') { e.preventDefault(); this.slashIdx = (this.slashIdx + 1) % this.slashItems.length; return; }
          if (e.key === 'ArrowUp') { e.preventDefault(); this.slashIdx = (this.slashIdx - 1 + this.slashItems.length) % this.slashItems.length; return; }
          if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); this.applySlash(this.slashItems[this.slashIdx]); return; }
          if (e.key === 'Escape') { e.preventDefault(); this.slashOpen = false; return; }
        }
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
      },
      onComposerInput() {
        this._autosize();
        const v = this.input;
        if (v.startsWith('/') && !v.includes('\n') && !v.includes(' ')) this._openSlash(v);
        else this.slashOpen = false;
      },

      // ── agent + tools ("checkpoints") ────────────────────────────────
      enabledToolNames() {
        // Selected data-plane tools + the file helpers (always available so
        // the agent can use attachments regardless of the toggles).
        return [...this.tools.filter((t) => t.on).map((t) => t.id), 'list_files', 'read_file'];
      },
      toggleTool(id) {
        const t = this.tools.find((x) => x.id === id);
        if (t) t.on = !t.on;
        localStorage.setItem(STORAGE + ':tools', JSON.stringify(this.tools.map((x) => ({ id: x.id, on: x.on }))));
      },
      async loadModels() {
        try {
          const r = await fetch('/api/ai/models', { credentials: 'include' });
          const j = await r.json();
          this.models = (j.data || []).map((m) => m.id).filter(Boolean).sort();
        } catch (_) { this.models = []; }
      },

      // ── context note ─────────────────────────────────────────────────
      saveNote() { localStorage.setItem(STORAGE + ':note', this.contextNote || ''); },
      _buildContext() {
        const ctx = {};
        if (this.contextNote && this.contextNote.trim()) ctx.note = this.contextNote.trim();
        if (this.files.length) ctx.files = this.files.map((f) => ({ name: f.name, size: f.size, path: f.path }));
        return Object.keys(ctx).length ? ctx : undefined;
      },

      // ── attached files (live under /tmp, server-side) ────────────────
      pickFile() { if (this.$refs.file) this.$refs.file.click(); },
      async onFilePicked(e) {
        const list = e.target.files;
        if (list && list.length) await this.uploadFiles(list);
        e.target.value = '';
      },
      async uploadFiles(list) {
        this.uploading = true;
        try {
          for (const f of list) {
            const fd = new FormData(); fd.append('file', f);
            const r = await fetch('/api/ai/files', { method: 'POST', body: fd, credentials: 'include' });
            if (r.ok) {
              const meta = await r.json();
              if (!this.files.find((x) => x.name === meta.name)) this.files.push(meta);
            }
          }
        } catch (_) { /* best effort */ } finally { this.uploading = false; }
      },
      async refreshFiles() {
        try { const r = await fetch('/api/ai/files', { credentials: 'include' }); const j = await r.json(); this.files = j.files || []; } catch (_) { /* none */ }
      },
      async removeFile(name) {
        try { await fetch('/api/ai/files/' + encodeURIComponent(name), { method: 'DELETE', credentials: 'include' }); } catch (_) { /* ignore */ }
        this.files = this.files.filter((f) => f.name !== name);
      },
      fmtSize(n) {
        n = Number(n) || 0;
        for (const u of ['B', 'KB', 'MB']) { if (n < 1024) return Math.round(n) + u; n /= 1024; }
        return n.toFixed(1) + 'GB';
      },

      // ── slash-command palette ────────────────────────────────────────
      _slashRegistry() {
        return [
          { key: '/clear', hint: 'Clear the conversation', run: () => this.clearChat() },
          { key: '/tools', hint: 'Agent & tool settings', run: () => { this.settingsOpen = true; } },
          { key: '/attach', hint: 'Attach a file (stored in /tmp)', run: () => this.pickFile() },
          { key: '/context', hint: 'Add a context note', run: () => { this.settingsOpen = true; this.$nextTick(() => this.$refs.note && this.$refs.note.focus()); } },
          { key: '/duckdb', hint: 'Query the index stores', insert: 'Using run_duckdb, ' },
          { key: '/opensearch', hint: 'Query GrimoireLab activity', insert: 'Using run_opensearch, ' },
          { key: '/cypher', hint: 'Query the community graph', insert: 'Using run_cypher, ' },
          { key: '/sparql', hint: 'Query repository properties', insert: 'Using run_sparql, ' },
          { key: '/search', hint: 'Semantic search a GME index', insert: 'Using gme_search, find ' },
          { key: '/extract', hint: 'Extract a repo (gimie) — needs the tool on', insert: 'Extract metadata for ' },
          { key: '/crawl', hint: 'Start a crawl — needs the tool on', insert: 'Crawl these GitHub seeds: ' },
          { key: '/plot', hint: 'Ask for a Vega-Lite chart', insert: 'Plot as a Vega-Lite chart: ' },
        ];
      },
      _openSlash(v) {
        const q = v.slice(1).toLowerCase();
        this.slashItems = this._slashRegistry().filter((c) => !q || c.key.slice(1).startsWith(q));
        this.slashIdx = 0;
        this.slashOpen = this.slashItems.length > 0;
      },
      applySlash(item) {
        if (!item) return;
        this.slashOpen = false;
        if (item.insert !== undefined) { this.input = item.insert; this._autosize(); if (this.$refs.input) this.$refs.input.focus(); return; }
        this.input = '';
        if (item.run) item.run();
      },
      _autosize() {
        this.$nextTick(() => {
          const ta = this.$refs.input;
          if (!ta) return;
          ta.style.height = 'auto';
          ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
        });
      },
      _scroll() {
        this.$nextTick(() => {
          const el = this.$refs.scroll;
          if (el) el.scrollTop = el.scrollHeight;
        });
      },
      loadExample(text) { this.input = text; this._autosize(); this.$refs.input?.focus(); },
    };
  }

  window.agentChat = agentChat;
})();
