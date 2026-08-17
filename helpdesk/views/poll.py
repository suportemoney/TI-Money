import json

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.permissions import MODULO_HELPDESK, requer_modulo
from helpdesk.models import Ticket

_CHAVE_SESSAO_POLL = 'helpdesk_poll_desde'
_CHAVE_SESSAO_LETREIRO = 'helpdesk_letreiro_ids'


def _resolver_acao_poll(log):
    """Mapeia registro de auditoria para tipo de evento do frontend."""
    if not log:
        return 'UPDATED'

    acao = log.acao
    metadata = log.metadata or {}

    if acao == 'COMMENT':
        # Comentário com @menção — cliente decide o áudio pelo mention_user_ids
        if metadata.get('acao_ui') == 'MENTION' or metadata.get('mention_user_ids'):
            return 'MENTION'
        return 'COMMENT'
    if acao == 'STATUS_CHANGED':
        return 'STATUS_CHANGED'
    if acao == 'CREATED':
        return 'CREATED'

    if acao == 'UPDATED':
        if 'priority' in metadata:
            return 'PRIORITY_CHANGED'
        if 'specific_category' in metadata:
            return 'TRIAGE_CHANGED'
        if 'status' in metadata:
            return 'STATUS_CHANGED'

    return acao


@requer_modulo(MODULO_HELPDESK)
def poll_ticket_updates(request):
    """
    Verificação leve via HTMX (hx-trigger="every Ns").
    Requisição curta — não mantém socket aberto nem bloqueia worker do Gunicorn.
    """
    agora = timezone.now()
    Ticket.archive_old_tickets()
    try:
        from helpdesk.assistente_followup import processar_followups_assistente
        processar_followups_assistente()
    except Exception:
        # Poll não pode quebrar por falha no follow-up
        pass
    try:
        from helpdesk.informative_services import arquivar_comunicados_expirados
        arquivar_comunicados_expirados(agora=agora)
    except Exception:
        pass
    since_raw = request.session.get(_CHAVE_SESSAO_POLL)

    tem_mudanca = False
    since = None
    if since_raw:
        since = parse_datetime(since_raw)
        if since is not None:
            if timezone.is_naive(since):
                since = timezone.make_aware(since)
            tem_mudanca = Ticket.objects.filter(updated_at__gt=since).exists()

    request.session[_CHAVE_SESSAO_POLL] = agora.isoformat()
    request.session.modified = True

    letreiro_mudou = False
    try:
        from helpdesk.informative_services import assinatura_letreiro
        atual_letreiro = assinatura_letreiro()
        anterior_letreiro = request.session.get(_CHAVE_SESSAO_LETREIRO)
        request.session[_CHAVE_SESSAO_LETREIRO] = atual_letreiro
        request.session.modified = True
        if anterior_letreiro is not None and list(anterior_letreiro) != list(atual_letreiro):
            letreiro_mudou = True
    except Exception:
        letreiro_mudou = False

    if tem_mudanca and since is not None:
        from django.contrib.contenttypes.models import ContentType
        from core.models import RegistroAcao

        ticket_ct = ContentType.objects.get_for_model(Ticket)
        latest_log = RegistroAcao.objects.filter(
            modulo=RegistroAcao.ModuloChoices.HELPDESK,
            timestamp__gt=since,
            content_type=ticket_ct,
        ).order_by('-timestamp').first()

        actor_id = latest_log.actor_id if latest_log else None
        acao = _resolver_acao_poll(latest_log)
        ticket_id = latest_log.object_id if latest_log else None
        descricao = latest_log.descricao[:200] if latest_log else ''
        metadata = (latest_log.metadata or {}) if latest_log else {}

        trigger_data = {
            'ticketUpdated': {
                'actor_id': actor_id,
                'acao': acao,
                'ticket_id': ticket_id,
                'descricao': descricao,
                'mention_user_ids': metadata.get('mention_user_ids') or [],
                # Comentário interno: frontend não toca som para quem não vê interno
                'is_interno': bool(metadata.get('is_interno')),
            }
        }
        if letreiro_mudou:
            trigger_data['letreiroUpdated'] = True
        return HttpResponse(status=200, headers={'HX-Trigger': json.dumps(trigger_data)})
    if letreiro_mudou:
        return HttpResponse(
            status=200,
            headers={'HX-Trigger': json.dumps({'letreiroUpdated': True})},
        )
    return HttpResponse(status=204)
