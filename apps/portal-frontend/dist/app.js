// app.js -- DarkForge Command Center -- Alpine.js factories
// No bundler. All functions on window scope for Alpine auto-discovery.
// No innerHTML / x-html. All dynamic text via x-text / textContent.

function el(tag, opts) {
  opts = opts || {};
  var e = document.createElement(tag);
  if (opts.text !== undefined) e.textContent = opts.text;
  if (opts.cls) e.className = opts.cls;
  if (opts.colspan) e.colSpan = opts.colspan;
  return e;
}

async function fetchJson(url, init) {
  try {
    var r = await fetch(url, Object.assign({ cache: 'no-store' }, init || {}));
    if (!r.ok) return { error: 'HTTP ' + r.status };
    return await r.json();
  } catch (err) {
    return { error: String(err) };
  }
}

async function postJson(url, body) {
  try {
    var r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store'
    });
    if (!r.ok) {
      var msg = 'HTTP ' + r.status;
      try { var j = await r.json(); msg = j.detail || j.error || msg; } catch(x) {}
      return { error: msg };
    }
    return await r.json();
  } catch (err) {
    return { error: String(err) };
  }
}

function formatAge(s) {
  if (s == null || s < 0) return '--';
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}

function dataUri(b64) { return 'data:image/png;base64,' + (b64 || ''); }

function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? s.substring(0, n) + '...' : s;
}

function showToast(text, kind, opts) {
  kind = kind || 'info';
  opts = opts || {};
  var durationMs = opts.durationMs || 4000;
  var stack = document.getElementById('toast-stack');
  if (!stack) return;
  var t = document.createElement('div');
  t.className = 'toast ' + kind;
  // Use textContent for main text (no XSS)
  var msg = document.createElement('span');
  msg.textContent = text;
  t.appendChild(msg);
  // P1-2: optional action button
  if (opts.action) {
    var btn = document.createElement('button');
    btn.className = 'toast-action-btn';
    btn.textContent = opts.action.label;
    btn.addEventListener('click', function () {
      opts.action.onClick();
      try { stack.removeChild(t); } catch(x) {}
    });
    t.appendChild(btn);
  }
  stack.appendChild(t);
  setTimeout(function () { try { stack.removeChild(t); } catch(x) {} }, durationMs);
}

function confirmModal(title, message) {
  return new Promise(function (resolve) {
    var backdrop  = document.getElementById('modal-backdrop');
    var titleEl   = document.getElementById('modal-title');
    var bodyEl    = document.getElementById('modal-body');
    var okBtn     = document.getElementById('modal-ok');
    var cancelBtn = document.getElementById('modal-cancel');
    if (!backdrop) { resolve(true); return; }
    titleEl.textContent = title;
    bodyEl.textContent  = message;
    backdrop.style.display = 'flex';
    function cleanup(result) {
      backdrop.style.display = 'none';
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      resolve(result);
    }
    function onOk()     { cleanup(true); }
    function onCancel() { cleanup(false); }
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
  });
}

setInterval(function () {
  var e = document.getElementById('footer-time');
  if (e) e.textContent = 'Last refreshed: ' + new Date().toISOString().substring(11, 19) + ' UTC';
}, 1000);

// -- Alpine root --
function root() {
  return {
    identity: {},
    historyOpen: false,
    init: function () { this.loadIdentity(); },
    loadIdentity: async function () {
      var d = await fetchJson('/api/identity');
      if (!d.error) this.identity = d;
    }
  };
}

// -- Cluster card --
function clusterPanel() {
  return {
    state: {},
    busy: false,
    startedAt: null,
    elapsed: '',
    _elapsedTimer: null,
    init: function () {
      this.refresh();
      setInterval(() => this.refresh(), 3000);
    },
    refresh: async function () {
      var d = await fetchJson('/api/cluster/state');
      if (!d.error) this.state = d;
    },
    isRunning: function () {
      return this.state.power === 'Running' && this.state.state === 'Succeeded';
    },
    isStopped: function () {
      return this.state.power === 'Stopped';
    },
    isTransitioning: function () {
      var s = this.state;
      return s.state === 'Starting' || s.state === 'Stopping' ||
             s.power === 'Starting' || s.power === 'Stopping';
    },
    powerLabel: function () {
      if (this.busy) return 'Initiating...';
      if (this.isTransitioning()) return this.state.state || this.state.power || 'Transitioning';
      if (this.isRunning()) return 'Running';
      if (this.isStopped()) return 'Stopped';
      return this.state.state || '--';
    },
    toggle: async function () {
      var action = this.isRunning() ? 'stop' : 'start';
      var ok = await confirmModal(
        action === 'stop' ? 'Stop cluster?' : 'Start cluster?',
        'This will ' + action + ' aks-opensandbox-dev. Continue?'
      );
      if (!ok) return;
      this.busy = true;
      this.startedAt = Date.now();
      var self = this;
      this._elapsedTimer = setInterval(function () {
        self.elapsed = ((Date.now() - self.startedAt) / 1000).toFixed(0);
      }, 500);
      try {
        var resp = await postJson('/api/cluster/' + action, {});
        if (resp.error) showToast('Failed: ' + resp.error, 'error');
        else showToast('Cluster ' + action + ' initiated', 'success');
      } catch (e) {
        showToast('Failed: ' + e, 'error');
      } finally {
        this.busy = false;
        clearInterval(this._elapsedTimer);
        this.elapsed = '';
        this.refresh();
      }
    }
  };
}

// -- Swarm card --
function swarmPanel() {
  return {
    n: 4,
    model: 'Kimi-K2.6',
    image: '',
    running: false,
    runId: null,
    results: [],
    log: [],
    summary: null,
    phase: '',
    elapsed: '0',
    _elapsedTimer: null,
    _es: null,
    init: function () { this.loadRecent(); },
    loadRecent: async function () {
      var d = await fetchJson('/api/swarm/runs');
      if (!d.error && Array.isArray(d) && d.length > 0) {
        var last = d[0];
        if (last.passes !== undefined) this.summary = { passes: last.passes, total: last.n };
      }
    },
    run: async function () {
      this.running = true;
      this.results = [];
      this.log = [];
      this.summary = null;
      this.phase = 'starting';
      this.elapsed = '0';
      var resp = await postJson('/api/swarm/runs', {
        n: this.n, model: this.model, image: this.image || null
      });
      if (resp.error) { showToast(resp.error, 'error'); this.running = false; return; }
      this.runId = resp.run_id;
      pushHash('swarm', this.runId);
      var start = Date.now();
      var self = this;
      this._elapsedTimer = setInterval(function () {
        self.elapsed = ((Date.now() - start) / 1000).toFixed(1);
      }, 100);
      var es = new EventSource('/api/swarm/runs/' + this.runId + '/events');
      this._es = es;
      es.addEventListener('phase', function (e) {
        try { var d = JSON.parse(e.data); self.phase = d.phase || d.data || 'running'; } catch(x) {}
      });
      es.addEventListener('result', function (e) {
        try { self.results.push(JSON.parse(e.data)); } catch(x) {}
      });
      es.addEventListener('summary', function (e) {
        try { self.summary = Object.assign({}, self.summary || {}, JSON.parse(e.data)); } catch(x) {}
      });
      es.addEventListener('log', function (e) {
        try {
          var line = JSON.parse(e.data).line || e.data;
          self.log.push(line);
          if (self.log.length > 200) self.log.shift();
        } catch(x) { self.log.push(e.data); }
        var pane = document.getElementById('swarm-log-pane');
        if (pane) requestAnimationFrame(function () { pane.scrollTop = pane.scrollHeight; });
      });
      es.addEventListener('done', function () {
        self.phase = 'done'; self.running = false;
        clearInterval(self._elapsedTimer);
        es.close(); self.loadRecent();
        showToast('Swarm run complete', 'success');
      });
      es.onerror = function () { if (!self.running) es.close(); };
    },
    cancel: async function () {
      if (!this.runId) return;
      var ok = await confirmModal('Cancel run?', 'Kill the running swarm subprocess?');
      if (!ok) return;
      await fetch('/api/swarm/runs/' + this.runId, { method: 'DELETE' });
      if (this._es) this._es.close();
      clearInterval(this._elapsedTimer);
      this.running = false; this.phase = 'cancelled';
      showToast('Run cancelled', 'info');
    },
    leaderboard: function () {
      return this.results.slice().sort(function (a, b) {
        if (a.status === b.status) return (a.duration_s || 0) - (b.duration_s || 0);
        return a.status === 'PASS' ? -1 : 1;
      });
    },
    winner: function () {
      var passes = this.results.filter(function (r) { return r.status === 'PASS'; });
      if (!passes.length) return null;
      return passes.reduce(function (best, r) {
        return (!best || r.duration_s < best.duration_s) ? r : best;
      }, null);
    }
  };
}

// -- Create Sandbox card --
function sandboxPanel() {
  return {
    image: 'acropensandboxdemo7075.azurecr.io/python:3.12-slim',
    timeout: 300,
    runtime_class: 'kata-vm-isolation',
    cpu: '500m',
    memory: '512Mi',
    submitting: false,
    lastCreated: null,
    create: async function () {
      this.submitting = true;
      var resp = await postJson('/api/sandboxes', {
        image: this.image, timeout: this.timeout,
        runtime_class: this.runtime_class, cpu: this.cpu, memory: this.memory,
        env: {}, entrypoint: ['/bin/bash']
      });
      this.submitting = false;
      if (resp.error) { showToast(resp.error, 'error'); return; }
      this.lastCreated = resp;
      var sbId = resp.id || resp.sandbox_id || '';
      showToast('Sandbox created: ' + sbId, 'success', {
        durationMs: 8000,
        action: sbId ? { label: 'Copy ID', onClick: function () { navigator.clipboard.writeText(sbId); } } : null
      });
    }
  };
}

// -- Kimi Chat card --
function chatPanel() {
  return {
    deployment: 'Kimi-K2.6',
    input: '',
    busy: false,
    messages: [],
    init: function () {},
    send: async function () {
      var text = this.input.trim();
      if (!text || this.busy) return;
      this.messages.push({ role: 'user', content: text });
      this.input = '';
      this.busy = true;
      // Push a placeholder assistant bubble with busy:true (spinner shown)
      this.messages.push({ role: 'assistant', content: null, busy: true, deployment: null });
      requestAnimationFrame(function () {
        var c = document.getElementById('chat-messages');
        if (c) c.scrollTop = c.scrollHeight;
      });
      var resp = await postJson('/api/kimi/chat', {
        // Exclude the busy placeholder from the API payload
        messages: this.messages
          .filter(function (m) { return !m.busy; })
          .map(function (m) { return { role: m.role, content: m.content }; }),
        deployment: this.deployment === 'auto' ? null : this.deployment
      });
      this.busy = false;
      // Update the placeholder in-place (do NOT push a new message)
      var placeholder = this.messages[this.messages.length - 1];
      if (resp.error) {
        placeholder.content = '(error: ' + resp.error + ')';
        placeholder.busy = false;
        showToast(resp.error, 'error');
        return;
      }
      placeholder.content = (resp.message && resp.message.content) || '(empty response)';
      placeholder.deployment = resp.deployment_used || null;
      placeholder.busy = false;
      requestAnimationFrame(function () {
        var c = document.getElementById('chat-messages');
        if (c) c.scrollTop = c.scrollHeight;
      });
    },
    extractCode: function (text) {
      var m = /```(?:python|py)?\s*\n([\s\S]+?)```/m.exec(text || '');
      return m ? m[1].trim() : null;
    },
    runInSandbox: async function (code) {
      if (!code) return;
      requestAnimationFrame(function () {
        var c = document.getElementById('chat-messages');
        if (c) c.scrollTop = c.scrollHeight;
      });
      var target = null;
      for (var i = this.messages.length - 1; i >= 0; i--) {
        if (this.messages[i].role === 'assistant' && (this.messages[i].content || '').indexOf(code) !== -1) {
          target = this.messages[i];
          break;
        }
      }
      if (target) {
        target.exec = { running: true, elapsed_s: 0 };
        this.messages = this.messages.slice();
      }
      showToast('Running in sandbox…', 'info');
      var startedAt = Date.now();
      var self = this;
      // P1-3: elapsed counter while Kata VM boots
      var progressTimer = setInterval(function () {
        if (target && target.exec && target.exec.running) {
          target.exec = Object.assign({}, target.exec, { elapsed_s: ((Date.now() - startedAt) / 1000).toFixed(0) });
          self.messages = self.messages.slice();
        }
      }, 500);
      var resp = await postJson('/api/sandbox/exec', { code: code, timeout_s: 90 });
      clearInterval(progressTimer);
      if (target) {
        target.exec = Object.assign({ running: false }, resp);
        this.messages = this.messages.slice();
      }
      if (resp.error) {
        showToast('✗ exec failed: ' + resp.error, 'error');
      } else if (resp.exit_code !== 0) {
        showToast('✗ exec failed: exit=' + resp.exit_code, 'error');
      } else {
        showToast('✓ exec done', 'success');
      }
      requestAnimationFrame(function () {
        var c = document.getElementById('chat-messages');
        if (c) c.scrollTop = c.scrollHeight;
      });
    },
    demoChartPrompt: function () {
      this.input = "Write a Python snippet that uses matplotlib to plot sin(x) and cos(x) from 0 to 4π on the same axes, with a legend, title 'Demo: sin & cos', and grid. Just the code in a python fenced block — no explanation.";
    },
    // P1-4: one-click sin/cos demo
    demoSinCosStatus: '',
    runSinCosDemo: async function () {
      this.demoSinCosStatus = 'Sending prompt…';
      this.input = "Write a Python snippet that uses matplotlib to plot sin(x) and cos(x) from 0 to 4π on the same axes, with a legend, title 'Demo: sin & cos', and grid. Just the code in a python fenced block — no explanation.";
      await this.send();
      this.demoSinCosStatus = 'Waiting for reply…';
      var deadline = Date.now() + 60000;
      while (Date.now() < deadline) {
        var last = this.messages[this.messages.length - 1];
        if (last && last.role === 'assistant' && !last.busy) break;
        await new Promise(function (r) { setTimeout(r, 300); });
      }
      var last2 = this.messages[this.messages.length - 1];
      var code2 = last2 ? this.extractCode(last2.content) : null;
      if (!code2) { this.demoSinCosStatus = 'No code found in reply.'; return; }
      this.demoSinCosStatus = 'Running in sandbox…';
      await this.runInSandbox(code2);
      this.demoSinCosStatus = '';
    }
  };
}


// -- Observability card --
function observabilityPanel() {
  return {
    pool: { name: 'kata', total: 0, allocated: 0, available: 0, pool_max: 10, buffer_min: 2 },
    events: [],
    lastPolledAt: null,
    lastPolledAgo: 0,
    _agoTimer: null,
    // C2: sparkline history (last 60 ticks of pool.available)
    sparkTicks: [],
    hubbleUrl: null,
    azMonitorUrl: null,
    sliderPoolMax: 10,
    sliderBufMin: 2,
    sliderBufMax: 4,
    _patchTimer: null,
    init: function () {
      this.refresh();
      setInterval(() => this.refresh(), 3000);
      this._agoTimer = setInterval(() => {
        if (this.lastPolledAt) {
          this.lastPolledAgo = Math.floor((Date.now() - this.lastPolledAt) / 1000);
        }
      }, 1000);
    },
    refresh: async function () {
      // C2: load link-out URLs from /api/config once
      if (!this._configLoaded) {
        this._configLoaded = true;
        var cfg = await fetchJson('/api/config');
        if (!cfg.error) {
          this.hubbleUrl = cfg.HUBBLE_UI_URL || null;
          this.azMonitorUrl = cfg.AZURE_MONITOR_URL || null;
          // TODO: backend C1 — /api/config must expose HUBBLE_UI_URL and AZURE_MONITOR_URL
        }
      }
      var p = await fetchJson('/api/pool/kata');
      if (!p.error) {
        this.pool = p;
        this.lastPolledAt = Date.now();
        this.lastPolledAgo = 0;
        // C2: accumulate sparkline ticks (max 60)
        this.sparkTicks.push(p.available != null ? p.available : 0);
        if (this.sparkTicks.length > 60) this.sparkTicks.shift();
        // sync sliders to live values on first load only (don't clobber user edits)
        if (!this._sliderInited) {
          this.sliderPoolMin = p.pool_min != null ? p.pool_min : 0;
          this.sliderPoolMax = p.pool_max != null ? p.pool_max : 10;
          this.sliderBufMin  = p.buffer_min != null ? p.buffer_min : 2;
          this.sliderBufMax  = p.buffer_max != null ? p.buffer_max : 4;
          this._sliderInited = true;
        }
      }
      var e = await fetchJson('/api/events?since=600&limit=20');
      if (!e.error) this.events = e.events || [];
    },
    // C2: build SVG polyline path from sparkTicks
    sparklinePath: function () {
      var ticks = this.sparkTicks;
      if (ticks.length < 2) return '';
      var W = 160, H = 30;
      var max = Math.max.apply(null, ticks) || 1;
      var pts = ticks.map(function (v, i) {
        var x = (i / (ticks.length - 1)) * W;
        var y = H - (v / max) * H;
        return x.toFixed(1) + ',' + y.toFixed(1);
      });
      return pts.join(' ');
    },
    gaugePercent: function () {
      var max = this.pool.pool_max || 1;
      var avail = (this.pool.available != null) ? this.pool.available : 0;
      return Math.min(100, Math.round((avail / max) * 100));
    },
    eventClass: function (ev) {
      var sev = ev.severity_class || '';
      if (sev === 'error') return 'evt-red';
      if (sev === 'warning') return 'evt-accent';
      if (sev === 'info') return 'evt-green';
      var r = ev.reason || '';
      if (r === 'Scheduled' || r === 'Started') return 'evt-green';
      if (r === 'Pulled') return 'evt-blue';
      if (r === 'FailedScheduling' || r === 'BackOff') return 'evt-red';
      if (r === 'TriggeredScaleUp') return 'evt-accent';
      return 'evt-gray';
    },
    // #19: debounced PATCH pool CR
    schedulePatch: function () {
      clearTimeout(this._patchTimer);
      var self = this;
      this._patchTimer = setTimeout(async function () {
        var name = self.pool.name || 'kata';
        var ok = await confirmModal(
          'Update pool ' + name + '?',
          'poolMin=' + self.sliderPoolMin + ' poolMax=' + self.sliderPoolMax +
          ' bufferMin=' + self.sliderBufMin + ' bufferMax=' + self.sliderBufMax
        );
        if (!ok) return;
        var resp = await fetchJson('/api/pool/' + name, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            poolMin: self.sliderPoolMin,
            poolMax: self.sliderPoolMax,
            bufferMin: self.sliderBufMin,
            bufferMax: self.sliderBufMax
          })
        });
        if (resp && resp.error) showToast('PATCH failed: ' + resp.error, 'error');
        else showToast('Pool updated', 'success');
        self.refresh();
      }, 500);
    }
  };
}

// -- Sandboxes table card --
function sandboxesTable() {
  return {
    rows: [],
    _prevIds: [],
    _userDeleted: new Set(),
    summary: {},
    init: function () {
      this.refresh();
      setInterval(() => this.refresh(), 5000);
    },
    refresh: async function () {
      var d = await fetchJson('/api/sandboxes');
      var newRows = [];
      if (Array.isArray(d)) { newRows = d; }
      else if (d && d.sandboxes) { newRows = d.sandboxes; }

      // P0-2: detect auto-expired sandboxes
      if (this._prevIds.length > 0) {
        var newIds = new Set(newRows.map(function (r) { return r.id; }));
        this._prevIds.forEach((id) => {
          if (!newIds.has(id) && !this._userDeleted.has(id)) {
            showToast('Sandbox ' + id.slice(0, 8) + '… auto-expired', 'info');
          }
        });
      }
      this._prevIds = newRows.map(function (r) { return r.id; });
      this.rows = newRows;

      var s = await fetchJson('/api/cluster/summary');
      if (!s.error) this.summary = s;
    },
    manualRefresh: async function () {
      await this.refresh();
      showToast('Sandboxes refreshed', 'info');
    },
    deleteRow: async function (id) {
      if (!id) return;
      var ok = await confirmModal('Delete sandbox?', 'Delete ' + id.substring(0, 12) + '...?');
      if (!ok) return;
      this._userDeleted.add(id);
      var resp = await fetch('/api/sandboxes/' + id, { method: 'DELETE' });
      if (resp.ok) { showToast('Deleted', 'success'); this.refresh(); }
      else showToast('Delete failed', 'error');
    },
    formatAge: function (s) { return formatAge(s); }
  };
}
// ── P2-2: Global keyboard shortcuts ──���───────────────────────────────────────
(function () {
  var gPending = false;
  var gTimer = null;
  var cardMap = {
    's': '.card-swarm',
    'k': '.card-kimi-chat',
    'c': '.card-cluster',
    'o': '.card-observability',
    'x': '.card-create-sandbox',
    't': '.card-sandboxes-table'
  };

  function focusCard(sel) {
    var el = document.querySelector(sel);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    var focusable = el.querySelector('button, input, select, textarea, [tabindex]');
    if (focusable) setTimeout(function () { focusable.focus(); }, 300);
  }

  document.addEventListener('keydown', function (e) {
    // Ignore when typing in inputs
    var tag = (e.target || {}).tagName || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

    var key = e.key;

    // Esc: close any open modal
    if (key === 'Escape') {
      document.getElementById('kbd-modal').style.display = 'none';
      document.getElementById('modal-backdrop').style.display = 'none';
      gPending = false; clearTimeout(gTimer);
      return;
    }

    // ?: open cheatsheet
    if (key === '?') {
      document.getElementById('kbd-modal').style.display = 'flex';
      return;
    }

    // g <letter>: navigate to card
    if (gPending) {
      clearTimeout(gTimer);
      gPending = false;
      if (cardMap[key]) { focusCard(cardMap[key]); }
      return;
    }
    if (key === 'g') {
      gPending = true;
      gTimer = setTimeout(function () { gPending = false; }, 1500);
    }
  });
})();

// ── P2-3: URL hash routing ────────────────────────────────────────────────────
// Push hash on swarm start or chat conversation; restore on load (best-effort).
// Alpine factories call pushHash() when they have an ID to share.
function pushHash(type, id) {
  if (!id) return;
  history.replaceState(null, '', '#/' + type + '/' + id);
}

// On page load, scroll to the relevant card if hash is present
(function () {
  var hash = location.hash; // e.g. #/swarm/abc123 or #/chat/conv456
  if (!hash) return;
  var parts = hash.replace('#/', '').split('/');
  var type = parts[0];
  var cardSel = { swarm: '.card-swarm', chat: '.card-kimi-chat' }[type];
  if (cardSel) {
    window.addEventListener('load', function () {
      var el = document.querySelector(cardSel);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }
})();

// -- VNC card (#18) --
function vncPanel() {
  return {
    vncImage: '',
    vncTimeout: 600,
    launching: false,
    vncUrl: null,
    sandboxId: null,
    error: null,
    init: async function () {
      // Load default VNC image from /api/config if available
      var cfg = await fetchJson('/api/config');
      if (!cfg.error && cfg.VNC_IMAGE) this.vncImage = cfg.VNC_IMAGE;
      // TODO: backend contract C1 — /api/config not ready yet if null
    },
    launch: async function () {
      this.error = null;
      this.launching = true;
      var body = { timeout_s: this.vncTimeout };
      if (this.vncImage) body.image = this.vncImage;
      var resp = await postJson('/api/sandbox/vnc', body);
      this.launching = false;
      if (resp.error) { this.error = resp.error; return; }
      this.sandboxId = resp.sandbox_id;
      this.vncUrl = resp.vnc_url;
      showToast('Desktop sandbox launched: ' + (this.sandboxId || '').slice(0, 8), 'success');
    },
    stop: async function () {
      if (!this.sandboxId) return;
      var ok = await confirmModal('Stop desktop sandbox?', 'This will delete sandbox ' + this.sandboxId.slice(0, 12) + '...');
      if (!ok) return;
      await fetch('/api/sandboxes/' + this.sandboxId, { method: 'DELETE' });
      this.vncUrl = null;
      this.sandboxId = null;
      showToast('Desktop sandbox stopped', 'info');
    }
  };
}

// -- History panel (C3) --
function historyPanel() {
  return {
    open: false,
    tab: 'chat',       // 'chat' | 'swarm' | 'sandbox'
    chatConvs: [],
    swarmRuns: [],
    sandboxHistory: [],
    loading: false,
    toggle: async function () {
      this.open = !this.open;
      if (this.open) await this.load();
    },
    load: async function () {
      this.loading = true;
      // C3: TODO — backend must ship these endpoints
      var c = await fetchJson('/api/history/chat/conversations');
      if (!c.error) this.chatConvs = Array.isArray(c) ? c : (c.conversations || []);
      var s = await fetchJson('/api/history/swarm?limit=20');
      if (!s.error) this.swarmRuns = Array.isArray(s) ? s : (s.runs || []);
      var b = await fetchJson('/api/history/sandbox?limit=50');
      if (!b.error) this.sandboxHistory = Array.isArray(b) ? b : (b.sandboxes || []);
      this.loading = false;
    }
  };
}
