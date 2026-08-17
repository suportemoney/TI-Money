import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from core.permissions import MODULO_HELPDESK, requer_modulo
from helpdesk.informative_services import (
    arquivar_comunicados_expirados,
    comunicados_recem_expirados,
    gerar_palavras_chave,
    mensagens_letreiro_vigentes,
    validade_padrao,
)
from helpdesk.models import InformativeMessage
from helpdesk.ticket_access import (
    filtrar_mensagens_informativas,
    usuario_pode_gerenciar_informativos,
)


def _com_trigger_letreiro(response):
    """Pede ao header para recarregar o letreiro sem F5."""
    response['HX-Trigger'] = json.dumps({'letreiroUpdated': True})
    return response


@login_required
def informative_center(request):
    """Retorna o Drawer inicial da Central Informativa."""
    return render(
        request,
        'helpdesk/informative/_drawer.html',
        {
            'pode_gerenciar_informativos': usuario_pode_gerenciar_informativos(
                request.user
            ),
        },
    )


@login_required
def informative_list(request):
    """Retorna a lista de mensagens (chat)."""
    arquivar_comunicados_expirados()
    qs = filtrar_mensagens_informativas(request.user)
    messages = qs.select_related('created_by', 'arquivado_por').prefetch_related(
        'acknowledged_by'
    )
    return render(
        request,
        'helpdesk/informative/_list.html',
        {
            'messages': messages,
            'pode_gerenciar_informativos': usuario_pode_gerenciar_informativos(
                request.user
            ),
        },
    )


@login_required
def informative_create(request):
    """Cria uma nova mensagem informativa (validade 2h, keywords automáticas)."""
    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        letreiro = request.POST.get('letreiro') in ('1', 'true', 'on', 'yes')
        if text:
            InformativeMessage.objects.create(
                text=text,
                created_by=request.user,
                palavras_chave=gerar_palavras_chave(text)[:400],
                valido_ate=validade_padrao(),
                letreiro=letreiro,
                ativo=True,
                arquivado=False,
            )
            return _com_trigger_letreiro(informative_list(request))
    return informative_list(request)


@login_required
@require_POST
def informative_acknowledge(request, message_id):
    """Alterna o status de OK do usuário na mensagem."""
    qs = filtrar_mensagens_informativas(request.user)
    message = get_object_or_404(qs, pk=message_id)

    if request.user in message.acknowledged_by.all():
        message.acknowledged_by.remove(request.user)
    else:
        message.acknowledged_by.add(request.user)

    return informative_list(request)


@login_required
@require_POST
def informative_archive(request, message_id):
    """Arquiva manualmente (só TI/staff/superuser)."""
    if not usuario_pode_gerenciar_informativos(request.user):
        return HttpResponseForbidden('Sem permissão para arquivar.')
    message = get_object_or_404(InformativeMessage, pk=message_id)
    if not message.arquivado:
        message.marcar_arquivado(por=request.user)
    return _com_trigger_letreiro(informative_list(request))


@login_required
@require_POST
def informative_extend(request, message_id):
    """Prorroga validade em +2h e desarquiva (só TI/staff/superuser)."""
    if not usuario_pode_gerenciar_informativos(request.user):
        return HttpResponseForbidden('Sem permissão para prorrogar.')
    message = get_object_or_404(InformativeMessage, pk=message_id)
    message.prorrogar(horas=2)

    # Remove da lista de pendentes do modal na sessão
    pendentes = request.session.get('informativos_expirados_pendentes') or []
    if message_id in pendentes:
        request.session['informativos_expirados_pendentes'] = [
            i for i in pendentes if i != message_id
        ]
        request.session.modified = True

    if request.headers.get('HX-Request') and request.POST.get('from_modal'):
        return _com_trigger_letreiro(HttpResponse(status=204))
    return _com_trigger_letreiro(informative_list(request))


@login_required
@require_POST
def informative_dismiss_expire(request, message_id):
    """TI mantém arquivado — só remove do modal da sessão."""
    if not usuario_pode_gerenciar_informativos(request.user):
        return HttpResponseForbidden('Sem permissão.')
    pendentes = request.session.get('informativos_expirados_pendentes') or []
    if message_id in pendentes:
        request.session['informativos_expirados_pendentes'] = [
            i for i in pendentes if i != message_id
        ]
        request.session.modified = True
    return HttpResponse(status=204)


@login_required
def informative_expired_pending(request):
    """
    JSON com comunicados recém-expirados para o modal TI.
    Também arquiva expirados e atualiza a sessão.
    """
    if not usuario_pode_gerenciar_informativos(request.user):
        return JsonResponse({'items': []})

    novos = arquivar_comunicados_expirados()
    pendentes = list(request.session.get('informativos_expirados_pendentes') or [])
    for pk in novos:
        if pk not in pendentes:
            pendentes.append(pk)
    # Mantém só os ainda na janela recente
    recentes = {m.pk: m for m in comunicados_recem_expirados()}
    pendentes = [pk for pk in pendentes if pk in recentes]
    request.session['informativos_expirados_pendentes'] = pendentes
    request.session.modified = True

    items = []
    for pk in pendentes:
        msg = recentes.get(pk)
        if not msg:
            continue
        items.append({
            'id': msg.pk,
            'text': (msg.text or '')[:280],
            'created_by': (
                (msg.created_by.get_full_name() or msg.created_by.username)
                if msg.created_by_id else ''
            ),
        })
    return JsonResponse({'items': items})


@login_required
@requer_modulo(MODULO_HELPDESK)
@require_GET
def informative_letreiro(request):
    """Partial do letreiro para atualização HTMX (sem F5)."""
    return render(
        request,
        'helpdesk/_letreiro_inner.html',
        {'letreiro_mensagens': mensagens_letreiro_vigentes()},
    )
