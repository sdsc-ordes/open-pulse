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

      // ── lifecycle ────────────────────────────────────────────────────
      init() {
        this.defaultModel = (window.OP_AGENT_DEFAULT_MODEL || '').trim();
        this.model = localStorage.getItem(STORAGE + ':model') || this.defaultModel;
        try {
          const raw = localStorage.getItem(STORAGE);
          if (raw) this.messages = JSON.parse(raw) || [];
        } catch (_) { this.messages = []; }
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
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
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
