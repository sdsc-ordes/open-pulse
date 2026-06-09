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
  const CONV_KEY = 'op:agent:chat:conversations:v1';

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
        { id: 'chaoss_metrics', label: 'CHAOSS', on: true },
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
      // how many chained tool rounds the agent may take per reply
      maxToolTurns: 8,
      // conversation history (kept in localStorage like the other assistants)
      conversations: [],
      currentId: null,
      historyOpen: false,

      // ── lifecycle ────────────────────────────────────────────────────
      init() {
        this.defaultModel = (window.OP_AGENT_DEFAULT_MODEL || '').trim();
        this.model = localStorage.getItem(STORAGE + ':model') || this.defaultModel;
        try {
          const t = JSON.parse(localStorage.getItem(STORAGE + ':tools') || 'null');
          if (Array.isArray(t)) {
            this.tools.forEach((x) => { const f = t.find((y) => y.id === x.id); if (f) x.on = !!f.on; });
          }
        } catch (_) { /* keep defaults */ }
        this.contextNote = localStorage.getItem(STORAGE + ':note') || '';
        const mt = parseInt(localStorage.getItem(STORAGE + ':maxturns'), 10);
        if (Number.isFinite(mt)) this.maxToolTurns = Math.max(1, Math.min(20, mt));
        this._loadConversations();
        this.loadModels();
        this.refreshFiles();
        // Enhance any restored messages once the DOM + CDN libs are ready.
        this.$nextTick(() => this._enhanceAll());
        this._scroll();
      },

      // ── conversation history (localStorage) ───────────────────────────
      _genId() { return 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); },
      _convTitle(msgs) {
        const u = (msgs || []).find((m) => m.role === 'user' && m.content);
        const t = u ? String(u.content).replace(/\s+/g, ' ').trim() : '';
        return t ? (t.length > 48 ? t.slice(0, 47) + '…' : t) : 'New chat';
      },
      _newConversationObj() {
        const c = { id: this._genId(), title: 'New chat', messages: [], updatedAt: Date.now() };
        this.conversations.unshift(c);
        this.currentId = c.id;
        this.messages = c.messages;
      },
      _loadConversations() {
        let list = [];
        try { list = JSON.parse(localStorage.getItem(CONV_KEY) || '[]') || []; } catch (_) { list = []; }
        if (!Array.isArray(list)) list = [];
        // Migrate the old single-conversation blob into the new list.
        if (!list.length) {
          try {
            const old = JSON.parse(localStorage.getItem(STORAGE) || 'null');
            if (Array.isArray(old) && old.length) {
              list = [{ id: this._genId(), title: this._convTitle(old), messages: old, updatedAt: Date.now() }];
              localStorage.removeItem(STORAGE);
            }
          } catch (_) { /* ignore */ }
        }
        this.conversations = list;
        if (list.length) { this.currentId = list[0].id; this.messages = list[0].messages || []; }
        else { this._newConversationObj(); }
      },
      persist() {
        const c = this.conversations.find((x) => x.id === this.currentId);
        if (c) {
          c.messages = this.messages;
          c.updatedAt = Date.now();
          if (!c.title || c.title === 'New chat') c.title = this._convTitle(this.messages);
        }
        try {
          const slim = this.conversations.slice(0, 50).map((x) => ({
            id: x.id, title: x.title, updatedAt: x.updatedAt,
            messages: (x.messages || []).slice(-200),
          }));
          localStorage.setItem(CONV_KEY, JSON.stringify(slim));
        } catch (_) { /* quota — best effort */ }
      },
      newConversation() {
        this.persist();
        this._newConversationObj();
        this.input = ''; this.partial = ''; this.error = '';
        this.historyOpen = false;
        this.persist();
        this.$nextTick(() => { this._enhanceAll(); this._scroll(); });
      },
      loadConversation(id) {
        if (id !== this.currentId) {
          this.persist();
          const c = this.conversations.find((x) => x.id === id);
          if (!c) return;
          this.currentId = id;
          this.messages = c.messages || [];
          this.partial = ''; this.error = '';
        }
        this.historyOpen = false;
        this.$nextTick(() => { this._enhanceAll(); this._scroll(); });
      },
      deleteConversation(id) {
        this.conversations = this.conversations.filter((x) => x.id !== id);
        if (id === this.currentId) {
          if (this.conversations.length) {
            this.currentId = this.conversations[0].id;
            this.messages = this.conversations[0].messages || [];
          } else { this._newConversationObj(); }
          this.$nextTick(() => { this._enhanceAll(); this._scroll(); });
        }
        this.persist();
      },
      clearAllHistory() {
        this.conversations = [];
        this._newConversationObj();
        this.historyOpen = false;
        this.persist();
        this.$nextTick(() => this._enhanceAll());
      },
      relTime(ts) {
        const s = Math.max(0, (Date.now() - (ts || 0)) / 1000);
        if (s < 60) return 'just now';
        if (s < 3600) return Math.floor(s / 60) + 'm ago';
        if (s < 86400) return Math.floor(s / 3600) + 'h ago';
        return Math.floor(s / 86400) + 'd ago';
      },
      saveMaxTurns() {
        try { localStorage.setItem(STORAGE + ':maxturns', String(this.maxToolTurns)); } catch (_) { /* ignore */ }
      },

      clearChat() {
        this.messages = [];
        this.partial = '';
        this.error = '';
        const c = this.conversations.find((x) => x.id === this.currentId);
        if (c) { c.messages = []; c.title = 'New chat'; }
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
              max_tool_turns: this.maxToolTurns,
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

      // ── tool cards: query block + result table ───────────────────────
      _toolRes(m) {
        let res = m.content;
        if (typeof res === 'string') {
          try { res = JSON.parse(res); } catch (_) { return null; }
        }
        return res && typeof res === 'object' ? res : null;
      },
      // Compact summary line shown in the expander header.
      toolMeta(m) {
        const res = this._toolRes(m);
        if (!res) return '';
        const bits = [res.engine || m.name || 'tool'];
        if (res.row_count != null) bits.push(res.row_count + ' rows');
        else if (res.count != null) bits.push(res.count + ' hits');
        if (res.elapsed_ms != null) bits.push(res.elapsed_ms + ' ms');
        if (res.truncated) bits.push('truncated');
        if (res.submitted) bits.push('job ' + (res.job_id || 'submitted'));
        if (res.error) bits.push('error');
        return bits.join(' · ');
      },
      // True when the tool returned an error — used to tint the summary.
      toolErrored(m) { const r = this._toolRes(m); return !!(r && r.error); },
      // The query/args this tool ran, mapped to a highlight language.
      _toolLang(m) {
        switch (m.name) {
          case 'run_cypher': return 'cypher';
          case 'run_sparql': return 'sparql';
          case 'run_duckdb': return 'sql';
          case 'run_opensearch':
            return (m.arguments && m.arguments.mode === 'dsl') ? 'json' : 'sql';
          default: return 'json';
        }
      },
      _toolQueryText(m) {
        const a = m.arguments;
        if (a && typeof a === 'object') {
          if (a.query) return String(a.query);
          if (a.sql) return String(a.sql);
          return JSON.stringify(a, null, 2);
        }
        return String(a || '');
      },
      // Pretty-print a query for display: pretty JSON, or break SQL /
      // Cypher / SPARQL onto multiple lines at clause keywords (only when
      // the model emitted it as one line — keep its own formatting if any).
      _formatQuery(text, lang) {
        if (!text) return text;
        if (lang === 'json') {
          try { return JSON.stringify(JSON.parse(text), null, 2); } catch (_) { return text; }
        }
        if (text.includes('\n')) return text;
        const kw = /\s+(?=\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|UNION ALL|UNION|MATCH|OPTIONAL MATCH|RETURN|WITH|UNWIND|MERGE|CREATE|PREFIX|CONSTRUCT|ASK|DESCRIBE|FILTER|BIND|VALUES|LEFT JOIN|JOIN)\b)/gi;
        return text.replace(kw, '\n').trim();
      },
      // Multiline, syntax-highlighted code block for the tool's query.
      renderToolQuery(m) {
        let text = this._toolQueryText(m);
        if (!text) return '';
        const lang = this._toolLang(m);
        text = this._formatQuery(text, lang);
        let inner;
        if (window.hljs && window.hljs.getLanguage(lang)) {
          inner = `<code class="hljs language-${lang}">${window.hljs.highlight(text, { language: lang, ignoreIllegals: true }).value}</code>`;
        } else if (window.hljs) {
          inner = `<code class="hljs">${window.hljs.highlightAuto(text).value}</code>`;
        } else {
          inner = `<code>${this._esc(text)}</code>`;
        }
        return `<pre class="agent-tool-query">${inner}</pre>`;
      },
      _cellHtml(v) {
        return v == null ? '' : (typeof v === 'object' ? this._esc(JSON.stringify(v)) : this._esc(String(v)));
      },
      // Render rows whether each row is an array (duckdb/opensearch) or a
      // dict keyed by column (cypher/sparql/gme) — the old code assumed
      // arrays, so dict-rows showed up as empty cells.
      _toolTable(cols, rows) {
        const get = (row, col, i) =>
          Array.isArray(row) ? row[i] : (row && typeof row === 'object' ? row[col] : undefined);
        const shown = rows.slice(0, 25);
        let html = '<div class="agent-tool-table"><table><thead><tr>' +
          cols.map((c) => `<th>${this._esc(c)}</th>`).join('') + '</tr></thead><tbody>' +
          shown.map((r) => '<tr>' + cols.map((c, i) => `<td>${this._cellHtml(get(r, c, i))}</td>`).join('') + '</tr>').join('') +
          '</tbody></table></div>';
        if (rows.length > shown.length) {
          html += `<div class="agent-tool-more">…${rows.length - shown.length} more rows</div>`;
        }
        return html;
      },
      _jsonBlock(obj) {
        const j = JSON.stringify(obj, null, 2);
        if (window.hljs && window.hljs.getLanguage('json')) {
          return `<pre class="agent-tool-query"><code class="hljs language-json">${window.hljs.highlight(j, { language: 'json' }).value}</code></pre>`;
        }
        return '<pre>' + this._esc(j) + '</pre>';
      },
      renderToolResult(m) {
        const res = this._toolRes(m);
        if (!res) return '<pre>' + this._esc(String(m.content || '')) + '</pre>';
        if (res.error) return `<div class="agent-tool-err">⚠ ${this._esc(String(res.error))}</div>`;
        if (Array.isArray(res.columns) && Array.isArray(res.rows)) {
          return res.rows.length ? this._toolTable(res.columns, res.rows) : '<div class="agent-tool-more">no rows</div>';
        }
        if (Array.isArray(res.hits)) {
          const rows = res.hits.map((h) => [h.title || h.id, h.url || '', h.score]);
          return rows.length ? this._toolTable(['title', 'url', 'score'], rows) : '<div class="agent-tool-more">no hits</div>';
        }
        // job submissions / list_files / read_file / anything else → JSON.
        return this._jsonBlock(res);
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
          let ids = (j.data || []).map((m) => m.id).filter(Boolean);
          // The endpoint lists 250+ entries including embedding / reranker
          // models (not chat-capable) and dtype variants (…-bfloat16 /
          // -fp8 / -float32) that just clutter the picker. Keep the chat
          // models so big ones (Qwen3-235B, Qwen3-30B, QwQ-32B, …) are easy
          // to find; the field still accepts any id the user types.
          ids = ids
            .filter((id) => !/embed|rerank|gte-|bge-|^BAAI\//i.test(id))
            .filter((id) => !/-(float32|bfloat16|fp8)$/i.test(id));
          this.models = ids.sort();
        } catch (_) { this.models = []; }
      },
      saveModel() {
        // '' means "use the hub default" — clear the override.
        if (this.model) localStorage.setItem(STORAGE + ':model', this.model);
        else localStorage.removeItem(STORAGE + ':model');
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
          { key: '/chaoss', hint: 'CHAOSS metric for a repo', insert: 'Using chaoss_metrics, report ' },
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
