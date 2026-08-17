"""Endpoints temporários de diagnóstico (staff/superuser). Remover depois do incidente."""
from __future__ import annotations

import traceback
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpResponse, HttpResponseForbidden
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET


def _autorizado(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


@login_required
@require_GET
def diag_last_500(request):
    """Mostra o último traceback gravado pelo middleware."""
    if not _autorizado(request.user):
        return HttpResponseForbidden('Só staff/superuser.')
    caminho = Path(settings.BASE_DIR) / 'logs' / 'last_500.txt'
    if not caminho.exists():
        return HttpResponse(
            'Arquivo logs/last_500.txt ainda não existe.\n'
            'Abra /helpdesk/ (para gerar o 500) e recarregue esta página.\n',
            content_type='text/plain; charset=utf-8',
        )
    return HttpResponse(
        caminho.read_text(encoding='utf-8', errors='replace'),
        content_type='text/plain; charset=utf-8',
    )


@login_required
@require_GET
def diag_helpdesk_check(request):
    """Executa checagens passo a passo e devolve texto com o ponto de falha."""
    if not _autorizado(request.user):
        return HttpResponseForbidden('Só staff/superuser.')

    linhas: list[str] = []

    def ok(msg: str):
        linhas.append(f'OK  {msg}')

    def falha(msg: str, exc: BaseException | None = None):
        linhas.append(f'FAIL {msg}')
        if exc is not None:
            linhas.append(traceback.format_exc())

    # 1) Colunas da Central Informativa
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'helpdesk_informativemessage'
                ORDER BY column_name
                """
            )
            cols = cur.fetchall()
        ok(f'colunas InformativeMessage ({len(cols)}):')
        for nome, tipo in cols:
            linhas.append(f'     - {nome}: {tipo}')
        nomes = {c[0] for c in cols}
        for esperado in ('arquivado', 'letreiro', 'valido_ate', 'arquivado_em', 'arquivado_por_id'):
            if esperado not in nomes:
                linhas.append(f'FAIL coluna ausente: {esperado}')
    except Exception as exc:
        falha('ler information_schema', exc)

    # 2) Query letreiro
    try:
        from helpdesk.informative_services import mensagens_letreiro_vigentes
        msgs = mensagens_letreiro_vigentes()
        ok(f'mensagens_letreiro_vigentes → {len(msgs)} item(ns)')
    except Exception as exc:
        falha('mensagens_letreiro_vigentes', exc)

    # 3) Arquivar expirados
    try:
        from helpdesk.informative_services import arquivar_comunicados_expirados
        ids = arquivar_comunicados_expirados()
        ok(f'arquivar_comunicados_expirados → {len(ids)} id(s)')
    except Exception as exc:
        falha('arquivar_comunicados_expirados', exc)

    # 4) Context processor
    try:
        from helpdesk.context_processors import helpdesk_permissoes
        ctx = helpdesk_permissoes(request)
        ok(f'helpdesk_permissoes keys={sorted(ctx.keys())}')
    except Exception as exc:
        falha('helpdesk_permissoes', exc)

    # 5) get_context_data do Kanban
    try:
        from helpdesk.views.kanban import KanbanView
        view = KanbanView()
        view.request = request
        view.kwargs = {}
        context = view.get_context_data()
        ok(f'KanbanView.get_context_data → tickets_new={len(context.get("tickets_new", []))}')
    except Exception as exc:
        falha('KanbanView.get_context_data', exc)
        context = None

    # 6) Render do template kanban
    if context is not None:
        try:
            context.setdefault('view', view)
            html = render_to_string('helpdesk/kanban.html', context, request=request)
            ok(f'render kanban.html → {len(html)} chars')
        except Exception as exc:
            falha('render helpdesk/kanban.html', exc)

    # 7) Template tags / includes isolados
    try:
        render_to_string('helpdesk/_letreiro.html', {'letreiro_mensagens': []}, request=request)
        ok('render _letreiro.html')
    except Exception as exc:
        falha('render _letreiro.html', exc)

    try:
        render_to_string('helpdesk/_informativo_expire_modal.html', {}, request=request)
        ok('render _informativo_expire_modal.html')
    except Exception as exc:
        falha('render _informativo_expire_modal.html', exc)

    linhas.append('')
    linhas.append('Fim do diagnóstico.')
    return HttpResponse('\n'.join(linhas) + '\n', content_type='text/plain; charset=utf-8')
