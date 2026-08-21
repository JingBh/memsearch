/**
 * memsearch-dsh — browser half (skill review panel).
 *
 * A non-blocking dock strip above the composer that lists MemSearch skill
 * candidates distilled from memory journals and lets the human review or
 * install them:
 *
 *   - data:   GET  /memsearch-dsh/skill-candidates
 *   - review: POST /memsearch-dsh/skill-review  { sessionId, name, action: 'review' }
 *             queues a user message into the live agent's inbox; the agent
 *             reviews the candidate on the next turn (non-blocking).
 *   - install:POST /memsearch-dsh/skill-review  { sessionId, name, action: 'install' }
 *             runs `memsearch skills install` in the background to the
 *             resolved target (paths config, else ~/.agents/skills).
 *
 * The bundle is a prebuilt client-module artifact: it registers its factory
 * with `window.__ModuleLoader__.load({ id, factory })` (lazy CJS table —
 * nothing runs until the shell materializes the module), and exports the
 * Cordis client plugin shape (`inject` + `apply`). The only external module it
 * requires is `react`, which the web shell provides.
 *
 * @module @zilliz/memsearch-dsh/client
 */
window.__ModuleLoader__.load({
  id: '@zilliz/memsearch-dsh',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })

    var React = require('react')
    var useState = React.useState
    var useEffect = React.useEffect
    var useCallback = React.useCallback

    var NS = 'memsearch-skill-review'
    var CSS_TAG = 'memsearch-skill-review-css'

    var CSS =
      '.msr-root{' +
        '--msr-bg:var(--dsw-alias-bg-layer-1,#1c1f26);' +
        '--msr-bg-2:var(--dsw-alias-bg-layer-2,#242830);' +
        '--msr-border:var(--dsw-alias-border-l1,rgba(148,163,184,.18));' +
        '--msr-text:var(--dsw-alias-label-primary,#e2e8f0);' +
        '--msr-text-2:var(--dsw-alias-label-secondary,#94a3b8);' +
        '--msr-brand:var(--dsw-alias-brand-primary,#3b82f6);' +
        '--msr-success:var(--dsw-alias-state-success-primary,#22c55e);' +
        '--msr-warn:var(--dsw-alias-state-warn-primary,#f59e0b);' +
        '--msr-error:var(--dsw-alias-state-error-primary,#ef4444);' +
        'font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;' +
      '}' +
      '.msr-bar{display:flex;align-items:center;gap:8px;width:100%;padding:6px 10px;box-sizing:border-box;' +
        'border:1px solid var(--msr-border);border-radius:8px;background:var(--msr-bg);color:var(--msr-text);' +
        'font-size:13px;line-height:1.4;}' +
      '.msr-badge{display:inline-flex;align-items:center;gap:5px;background:color-mix(in srgb,var(--msr-brand) 16%,transparent);' +
        'color:var(--msr-brand);border-radius:999px;padding:2px 9px;font-size:12px;font-weight:600;white-space:nowrap;}' +
      '.msr-count{font-weight:700;color:var(--msr-warn);}' +
      '.msr-spacer{flex:1;}' +
      '.msr-btn{border:1px solid var(--msr-border);background:var(--msr-bg-2);color:var(--msr-text);border-radius:6px;' +
        'padding:3px 10px;font-size:12px;cursor:pointer;font-family:inherit;white-space:nowrap;}' +
      '.msr-btn:hover{border-color:var(--msr-brand);color:var(--msr-brand);}' +
      '.msr-btn.primary{background:var(--msr-brand);border-color:var(--msr-brand);color:#fff;font-weight:600;}' +
      '.msr-btn.primary:hover{filter:brightness(1.1);color:#fff;}' +
      '.msr-btn.danger{color:var(--msr-error);}' +
      '.msr-btn.danger:hover{border-color:var(--msr-error);color:var(--msr-error);}' +
      '.msr-btn.ghost{background:transparent;}' +
      '.msr-btn:disabled{opacity:.55;cursor:default;}' +
      '.msr-panel{margin-top:6px;border:1px solid var(--msr-border);border-radius:10px;background:var(--msr-bg);' +
        'color:var(--msr-text);overflow:hidden;}' +
      '.msr-panel-head{display:flex;align-items:center;gap:8px;padding:8px 12px;font-size:12px;color:var(--msr-text-2);' +
        'border-bottom:1px solid var(--msr-border);}' +
      '.msr-panel-title{font-weight:700;color:var(--msr-text);font-size:13px;}' +
      '.msr-list{padding:4px;}' +
      '.msr-item{display:flex;align-items:flex-start;gap:10px;padding:9px 10px;border-radius:8px;}' +
      '.msr-item:hover{background:var(--msr-bg-2);}' +
      '.msr-item+.msr-item{border-top:1px solid var(--msr-border);}' +
      '.msr-item-main{flex:1;min-width:0;}' +
      '.msr-item-name{font-weight:650;font-size:13px;display:flex;align-items:center;gap:8px;}' +
      '.msr-tag{font-size:10px;font-weight:700;letter-spacing:.04em;padding:1px 7px;border-radius:999px;text-transform:uppercase;}' +
      '.msr-tag.candidate{background:color-mix(in srgb,var(--msr-warn) 18%,transparent);color:var(--msr-warn);}' +
      '.msr-tag.installed{background:color-mix(in srgb,var(--msr-success) 18%,transparent);color:var(--msr-success);}' +
      '.msr-item-desc{font-size:12px;color:var(--msr-text-2);margin-top:3px;}' +
      '.msr-item-meta{font-size:11px;color:var(--msr-text-2);margin-top:3px;opacity:.85;}' +
      '.msr-item-meta code{background:var(--msr-bg-2);border:1px solid var(--msr-border);border-radius:4px;' +
        'padding:0 4px;font-size:10px;}' +
      '.msr-item-actions{display:flex;gap:6px;flex-shrink:0;align-items:center;}' +
      '.msr-note{padding:8px 12px;border-top:1px solid var(--msr-border);font-size:11px;color:var(--msr-text-2);' +
        'display:flex;align-items:center;gap:6px;}' +
      '.msr-toast{margin-top:6px;padding:7px 12px;border-radius:8px;font-size:12px;border:1px solid var(--msr-border);' +
        'background:var(--msr-bg-2);color:var(--msr-text);}' +
      '.msr-toast.ok{border-color:color-mix(in srgb,var(--msr-success) 45%,transparent);color:var(--msr-success);}' +
      '.msr-toast.warn{border-color:color-mix(in srgb,var(--msr-warn) 45%,transparent);color:var(--msr-warn);}' +
      '.msr-toast.err{border-color:color-mix(in srgb,var(--msr-error) 45%,transparent);color:var(--msr-error);}'

    /** Insert the panel stylesheet once per page. */
    function ensureCss() {
      if (typeof document === 'undefined') return
      if (document.querySelector('style[data-plugin-css="' + CSS_TAG + '"]')) return
      var tag = document.createElement('style')
      tag.dataset.pluginCss = CSS_TAG
      tag.textContent = CSS
      document.head.appendChild(tag)
    }

    /** The dock strip: candidate count + expandable review list. */
    function SkillReviewPanel(props) {
      var sessionId = props.sessionId
      var candidates = useState(null) // null = loading
      var setCandidates = candidates[1]
      var open = useState(false)
      var setOpen = open[1]
      var dismissed = useState({})
      var setDismissed = dismissed[1]
      var toast = useState(null)
      var setToast = toast[1]
      var busy = useState(null)
      var setBusy = busy[1]

      var load = useCallback(function () {
        fetch('/memsearch-dsh/skill-candidates?sessionId=' + encodeURIComponent(sessionId))
          .then(function (res) { return res.json() })
          .then(function (data) {
            setCandidates(Array.isArray(data.candidates) ? data.candidates : [])
          })
          .catch(function () { setCandidates([]) })
      }, [sessionId])

      useEffect(function () { load() }, [load])

      useEffect(function () {
        if (toast[0] === null) return
        var t = setTimeout(function () { setToast(null) }, 4000)
        return function () { clearTimeout(t) }
      }, [toast[0]])

      var act = function (name, action) {
        setBusy(name)
        fetch('/memsearch-dsh/skill-review', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sessionId: sessionId, name: name, action: action }),
        })
          .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data } }) })
          .then(function (r) {
            if (!r.ok || !r.data.ok) throw new Error(r.data.error || 'request failed')
            if (action === 'review') {
              setToast({ kind: 'ok', text: 'Review queued for ' + name + ' — the agent will pick it up on the next turn.' })
            } else {
              setToast({ kind: 'ok', text: 'Installing ' + name + ' to ' + r.data.target + ' in the background.' })
              setTimeout(load, 2500)
            }
          })
          .catch(function (err) {
            setToast({ kind: 'err', text: String(err && err.message ? err.message : err) })
          })
          .finally(function () { setBusy(null) })
      }

      var h = React.createElement
      var visible = (candidates[0] || []).filter(function (c) { return !dismissed[0][c.name] })
      var pending = visible.filter(function (c) { return c.status === 'candidate' })
      var installed = visible.length - pending.length
      var loading = candidates[0] === null

      var bar = h(
        'div', { className: 'msr-bar' },
        h('span', { className: 'msr-badge' }, 'MemSearch'),
        loading
          ? h('span', null, 'Loading skill candidates…')
          : h('span', null,
              h('span', { className: 'msr-count' }, String(pending.length)),
              ' skill candidate' + (pending.length === 1 ? '' : 's') + ' awaiting review',
              h('span', { style: { color: 'var(--msr-text-2)', fontSize: 12 } },
                ' (installed ' + installed + ' · total ' + visible.length + ')')),
        h('span', { className: 'msr-spacer' }),
        h('button', { className: 'msr-btn', onClick: function () { setOpen(!open[0]) } },
          open[0] ? 'Collapse' : 'Review'),
        h('button', { className: 'msr-btn ghost', onClick: function () { setDismissed({}); load() } }, 'Refresh')
      )

      var items = visible.map(function (c) {
        return h(
          'div', { className: 'msr-item', key: c.name },
          h('div', { className: 'msr-item-main' },
            h('div', { className: 'msr-item-name' },
              c.name,
              h('span', { className: 'msr-tag ' + c.status }, c.status)),
            h('div', { className: 'msr-item-desc' }, c.description),
            h('div', { className: 'msr-item-meta' },
              c.sources.length > 0
                ? h('span', null,
                    'from ',
                    c.sources.slice(0, 3).map(function (s, i) { return h('code', { key: i }, s) }),
                    c.sources.length > 3 ? h('span', null, ' +' + (c.sources.length - 3)) : null)
                : null,
              ' · seen ' + c.occurrences + (c.occurrences === 1 ? ' time' : ' times'),
              c.installedPaths.length > 0 ? ' · installed to ' + c.installedPaths[0] : null),
            c.reason ? h('div', { className: 'msr-item-meta', style: { opacity: 0.9 } }, c.reason) : null
          ),
          h('div', { className: 'msr-item-actions' },
            c.status === 'candidate'
              ? h('button', {
                  className: 'msr-btn primary',
                  disabled: busy[0] === c.name,
                  onClick: function () { act(c.name, 'review') },
                }, 'Review')
              : null,
            c.status === 'candidate'
              ? h('button', {
                  className: 'msr-btn',
                  disabled: busy[0] === c.name,
                  onClick: function () { act(c.name, 'install') },
                }, 'Install')
              : null,
            h('button', {
              className: 'msr-btn ghost danger',
              onClick: function () {
                setDismissed(function (d) {
                  var next = {}
                  for (var k in d) next[k] = d[k]
                  next[c.name] = true
                  return next
                })
              },
            }, 'Dismiss')
          )
        )
      })

      var panel = open[0]
        ? h(
            'div', { className: 'msr-panel' },
            h('div', { className: 'msr-panel-head' },
              h('span', { className: 'msr-panel-title' }, 'Skill candidates (.memsearch/skill-candidates/)'),
              h('span', { className: 'msr-spacer' }),
              h('span', null, 'Review opens in the conversation · Install runs in the background')),
            visible.length === 0
              ? h('div', { className: 'msr-item', style: { color: 'var(--msr-text-2)' } },
                  loading ? 'Loading…' : 'No skill candidates.')
              : h('div', { className: 'msr-list' }, items),
            h('div', { className: 'msr-note' },
              'Installation is a manual step (memsearch skills install) and is never automatic. Target directory follows plugins.dsh.memory_to_skill.paths, defaulting to ~/.agents/skills.')
          )
        : null

      return h(
        'div', { className: 'msr-root' },
        bar,
        panel,
        toast[0] ? h('div', { className: 'msr-toast ' + toast[0].kind }, toast[0].text) : null
      )
    }

    exports.inject = ['slots']

    exports.apply = function apply(ctx) {
      var slots = ctx.get('slots')
      if (slots === undefined) return
      ensureCss()
      slots.inject('conversation.input.dock', function () {
        return slots.register(
          { name: 'conversation.input.dock', id: 'skill-review' },
          function (props) {
            return React.createElement(SkillReviewPanel, { sessionId: props.sessionId })
          },
        )
      })
    }

    return module.exports
  },
})
