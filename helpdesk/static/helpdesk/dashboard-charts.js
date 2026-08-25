(function () {
    'use strict';

    const CORES_RESULTADO = ['#16a34a', '#dc2626', '#2563eb', '#94a3b8'];
    const CORES_STATUS = ['#3b82f6', '#22c55e', '#f59e0b', '#64748b', '#8b5cf6'];
    const CORES_PRIORIDADE = ['#22c55e', '#f59e0b', '#f97316', '#dc2626', '#94a3b8'];
    const CORES_RECUSA = ['#dc2626', '#ef4444', '#f87171', '#fb7185', '#f43f5e', '#e11d48'];
    const CORES_RESOLUCAO = ['#16a34a', '#22c55e', '#4ade80', '#86efac', '#059669', '#10b981'];

    let instancias = [];

    function destruirGraficos() {
        instancias.forEach(function (chart) {
            try {
                chart.destroy();
            } catch (e) { /* ignora gráfico já destruído */ }
        });
        instancias = [];
    }

    function lerDados() {
        const el = document.getElementById('helpdesk-dashboard-charts-data');
        if (!el) return null;
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            return null;
        }
    }

    function opcoesBarraHorizontal() {
        return {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
            },
            scales: {
                x: {
                    beginAtZero: true,
                    ticks: { precision: 0 },
                    grid: { color: '#f1f5f9' },
                },
                y: {
                    grid: { display: false },
                    ticks: { autoSkip: false },
                },
            },
        };
    }

    function criarBarra(canvasId, serie, cores) {
        const canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') return;
        const labels = (serie && serie.labels) || [];
        const values = (serie && serie.values) || [];
        if (!labels.length) return;
        instancias.push(new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: labels.map(function (_, i) {
                        return cores[i % cores.length];
                    }),
                    borderRadius: 6,
                    maxBarThickness: 28,
                }],
            },
            options: opcoesBarraHorizontal(),
        }));
    }

    function criarDoughnut(canvasId, serie, cores) {
        const canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') return;
        const labels = (serie && serie.labels) || [];
        const values = (serie && serie.values) || [];
        instancias.push(new Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: cores,
                    borderWidth: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom' },
                },
            },
        }));
    }

    function initHelpdeskCharts() {
        if (typeof Chart === 'undefined') return;
        const data = lerDados();
        if (!data) return;
        destruirGraficos();
        criarDoughnut('chart-helpdesk-resultado', data.resultado, CORES_RESULTADO);
        criarBarra('chart-helpdesk-status', data.status, CORES_STATUS);
        criarBarra('chart-helpdesk-prioridade', data.prioridade, CORES_PRIORIDADE);
        criarBarra('chart-helpdesk-motivos-recusa', data.motivos_recusa, CORES_RECUSA);
        criarBarra('chart-helpdesk-motivos-resolucao', data.motivos_resolucao, CORES_RESOLUCAO);
    }

    function boot() {
        if (typeof Chart === 'undefined') {
            setTimeout(boot, 50);
            return;
        }
        initHelpdeskCharts();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    document.body.addEventListener('htmx:afterSwap', function (e) {
        if (e.detail && e.detail.target && e.detail.target.id === 'dashboard-metrics-container') {
            initHelpdeskCharts();
        }
    });
})();
