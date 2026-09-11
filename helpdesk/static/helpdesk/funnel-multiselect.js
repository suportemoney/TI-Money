/**
 * Seletor multi-select do funil (filtro do kanban e formulários).
 */
(function (global) {
    if (global.__funnelMsLoaded) {
        if (typeof global.initFunnelMultiSelect === 'function') {
            global.initFunnelMultiSelect();
        }
        return;
    }
    global.__funnelMsLoaded = true;

    function valoresSelecionados(root) {
        return Array.from(root.querySelectorAll('[data-funnel-option]:checked')).map(function (el) {
            return el.value;
        });
    }

    function atualizarResumo(root) {
        var summary = root.querySelector('[data-funnel-summary]');
        if (!summary) return;
        var placeholder = root.getAttribute('data-placeholder') || 'Selecionar';
        var marcados = Array.from(root.querySelectorAll('[data-funnel-option]:checked'));
        summary.innerHTML = '';
        if (!marcados.length) {
            summary.textContent = placeholder;
            summary.classList.add('text-slate-500');
            summary.classList.remove('text-slate-800');
            return;
        }
        summary.classList.remove('text-slate-500');
        summary.classList.add('text-slate-800');
        var maxChips = 2;
        marcados.slice(0, maxChips).forEach(function (el) {
            var chip = document.createElement('span');
            chip.className = 'inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold bg-fuchsia-100 text-fuchsia-800 border border-fuchsia-200';
            chip.textContent = '#' + (el.getAttribute('data-label') || el.value);
            summary.appendChild(chip);
        });
        if (marcados.length > maxChips) {
            var extra = document.createElement('span');
            extra.className = 'text-[11px] font-semibold text-fuchsia-700';
            extra.textContent = '+' + (marcados.length - maxChips);
            summary.appendChild(extra);
        }
    }

    function aplicarFiltro(root) {
        if (root.getAttribute('data-mode') !== 'filter') return;
        var params = new URLSearchParams(window.location.search);
        params.delete('tag');
        valoresSelecionados(root).forEach(function (v) {
            params.append('tag', v);
        });
        var qs = params.toString();
        var path = window.location.pathname + (qs ? '?' + qs : '');
        history.replaceState(null, '', path);

        var board = document.getElementById('kanban-board-container');
        if (!board || !global.htmx) {
            window.location.href = path;
            return;
        }
        var hxGet = board.getAttribute('hx-get') || '';
        var asset = '';
        try {
            asset = new URL(hxGet, window.location.origin).searchParams.get('v') || '';
        } catch (e) {}
        if (asset) params.set('v', asset);
        var url = hxGet.split('?')[0] + '?' + params.toString();
        board.setAttribute('hx-get', url);
        global.htmx.ajax('GET', url, { target: '#kanban-board-container', swap: 'innerHTML' });
    }

    function bind(root) {
        if (root.dataset.funnelReady === '1') return;
        root.dataset.funnelReady = '1';
        var toggle = root.querySelector('[data-funnel-toggle]');
        var panel = root.querySelector('[data-funnel-panel]');
        var search = root.querySelector('[data-funnel-search]');
        var clearBtn = root.querySelector('[data-funnel-clear]');

        if (toggle && panel) {
            toggle.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                document.querySelectorAll('[data-funnel-panel]').forEach(function (p) {
                    if (p !== panel) p.classList.add('hidden');
                });
                panel.classList.toggle('hidden');
                if (!panel.classList.contains('hidden')) {
                    var rect = toggle.getBoundingClientRect();
                    panel.style.position = 'fixed';
                    panel.style.left = Math.max(8, rect.left) + 'px';
                    panel.style.top = (rect.bottom + 4) + 'px';
                    panel.style.width = Math.max(rect.width, 256) + 'px';
                    panel.style.zIndex = '90';
                    if (search) search.focus();
                }
            });
        }

        if (search) {
            search.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') e.preventDefault();
            });
            search.addEventListener('input', function () {
                var q = (search.value || '').toLowerCase().trim();
                root.querySelectorAll('[data-funnel-row]').forEach(function (row) {
                    var txt = row.getAttribute('data-search') || '';
                    row.classList.toggle('hidden', q && txt.indexOf(q) === -1);
                });
            });
        }

        root.querySelectorAll('[data-funnel-option]').forEach(function (cb) {
            cb.addEventListener('change', function () {
                atualizarResumo(root);
                aplicarFiltro(root);
            });
        });

        if (clearBtn) {
            clearBtn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                root.querySelectorAll('[data-funnel-option]:checked').forEach(function (cb) {
                    cb.checked = false;
                });
                atualizarResumo(root);
                aplicarFiltro(root);
            });
        }

        atualizarResumo(root);
    }

    function initAll(scope) {
        var raiz = scope && scope.querySelectorAll ? scope : document;
        raiz.querySelectorAll('[data-funnel-ms]').forEach(bind);
    }

    document.addEventListener('click', function (e) {
        document.querySelectorAll('[data-funnel-ms]').forEach(function (root) {
            if (!root.contains(e.target)) {
                var panel = root.querySelector('[data-funnel-panel]');
                if (panel) panel.classList.add('hidden');
            }
        });
    });

    document.body.addEventListener('htmx:afterSwap', function (e) {
        initAll(e.target);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { initAll(); });
    } else {
        initAll();
    }

    global.initFunnelMultiSelect = initAll;
})(window);
