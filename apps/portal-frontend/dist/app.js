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

function showToast(text, kind) {
  kind = kind || 'info';
  var stack = document.getElementById('toast-stack');
  if (!stack) return;
  var t = el('div', { cls: 'toast ' + kind, text: text });
  stack.appendChild(t);
  setTimeout(function () { try { stack.removeChild(t); } catch(x) {} }, 4000);
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
      showToast('Sandbox created: ' + (resp.id || resp.sandbox_id || '(see table)'), 'success');
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
      // Scroll to bottom so result will be visible
      requestAnimationFrame(function () {
        var c = document.getElementById('chat-messages');
        if (c) c.scrollTop = c.scrollHeight;
      });
      // Find the last assistant message whose content contains this code snippet
      var target = null;
      for (var i = this.messages.length - 1; i >= 0; i--) {
        if (this.messages[i].role === 'assistant' && (this.messages[i].content || '').indexOf(code) !== -1) {
          target = this.messages[i];
          break;
        }
      }
      if (target) {
        target.exec = { running: true };
        this.messages = this.messages.slice(); // trigger Alpine reactivity
      }
      showToast('Running in sandbox…', 'info');
      var resp = await postJson('/api/sandbox/exec', { code: code, timeout_s: 90 });
      if (target) {
        target.exec = Object.assign({ running: false }, resp);
        this.messages = this.messages.slice(); // trigger Alpine reactivity
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
    }
  };
}

// -- Observability card --
function observabilityPanel() {
  return {
    pool: { name: 'kata', total: 0, allocated: 0, available: 0, pool_max: 10, buffer_min: 2 },
    events: [],
    init: function () {
      this.refresh();
      setInterval(() => this.refresh(), 3000);
    },
    refresh: async function () {
      var p = await fetchJson('/api/pool/kata');
      if (!p.error) this.pool = p;
      var e = await fetchJson('/api/events?since=600&limit=20');
      if (!e.error) this.events = e.events || [];
    },
    gaugePercent: function () {
      var max = this.pool.pool_max || 1;
      var avail = (this.pool.available != null) ? this.pool.available : 0;
      return Math.min(100, Math.round((avail / max) * 100));
    },
    eventClass: function (reason) {
      if (reason === 'Scheduled' || reason === 'Started') return 'evt-green';
      if (reason === 'Pulled') return 'evt-blue';
      if (reason === 'FailedScheduling') return 'evt-red';
      if (reason === 'TriggeredScaleUp') return 'evt-accent';
      return 'evt-gray';
    }
  };
}

// -- Sandboxes table card --
function sandboxesTable() {
  return {
    rows: [],
    summary: {},
    init: function () {
      this.refresh();
      setInterval(() => this.refresh(), 5000);
    },
    refresh: async function () {
      var d = await fetchJson('/api/sandboxes');
      if (Array.isArray(d)) { this.rows = d; }
      else if (d && d.sandboxes) { this.rows = d.sandboxes; }
      var s = await fetchJson('/api/cluster/summary');
      if (!s.error) this.summary = s;
    },
    deleteRow: async function (id) {
      if (!id) return;
      var ok = await confirmModal('Delete sandbox?', 'Delete ' + id.substring(0, 12) + '...?');
      if (!ok) return;
      var resp = await fetch('/api/sandboxes/' + id, { method: 'DELETE' });
      if (resp.ok) { showToast('Deleted', 'success'); this.refresh(); }
      else showToast('Delete failed', 'error');
    },
    formatAge: function (s) { return formatAge(s); }
  };
}