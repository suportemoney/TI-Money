"""Envio de notificações Web Push para eventos de chamados."""

import json
import logging
import time

from django.conf import settings
from django.db.models import Q

from helpdesk.models import PushSubscription, Ticket
from helpdesk.ticket_access import usuario_eh_operador_helpdesk, usuario_pode_acessar_chamado

logger = logging.getLogger(__name__)

# Tipos de evento reconhecidos pelo frontend e pelo poll
EVENTO_COMMENT = 'COMMENT'
EVENTO_STATUS_CHANGED = 'STATUS_CHANGED'
EVENTO_PRIORITY_CHANGED = 'PRIORITY_CHANGED'
EVENTO_TRIAGE_CHANGED = 'TRIAGE_CHANGED'
EVENTO_CREATED = 'CREATED'
EVENTO_MENTION = 'MENTION'

_TITULOS_EVENTO = {
    EVENTO_COMMENT: 'Novo comentário',
    EVENTO_STATUS_CHANGED: 'Chamado movido',
    EVENTO_PRIORITY_CHANGED: 'Prioridade alterada',
    EVENTO_TRIAGE_CHANGED: 'Triagem alterada',
    EVENTO_CREATED: 'Novo chamado',
    EVENTO_MENTION: 'Você foi mencionado',
}


def _vapid_configurado() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def destinatarios_notificacao(ticket: Ticket, actor, *, somente_nao_operadores: bool = False):
    """
    Usuários com acesso ao chamado que devem receber push (exceto o ator).

    - Broadcast em chamados sem assigned: apenas ADMIN / IT_USER (não SUPERVISOR).
    - Silêncio TI↔TI: se o ator é operador, remove outros operadores (exceto finalize
      que já usa somente_nao_operadores).
    - somente_nao_operadores: usado na finalização — nunca notifica operadores.
    """
    from core.models import CustomUser

    candidatos = []
    if ticket.created_by_id:
        candidatos.append(ticket.created_by_id)
    if ticket.requester_user_id:
        candidatos.append(ticket.requester_user_id)
    if ticket.assigned_to_id:
        candidatos.append(ticket.assigned_to_id)
    else:
        # Sem atribuição (ex.: coluna Novos): notifica só equipe TI / admin
        roles_operadores = [
            CustomUser.RoleChoices.ADMIN,
            CustomUser.RoleChoices.IT_USER,
        ]
        operadores_ids = list(CustomUser.objects.filter(
            Q(role__in=roles_operadores) | Q(is_superuser=True),
            is_active=True,
        ).values_list('pk', flat=True))
        candidatos.extend(operadores_ids)

    co_author_ids = list(ticket.co_authors.values_list('pk', flat=True))
    user_ids = set(candidatos + co_author_ids)

    actor_id = getattr(actor, 'pk', None) if actor else None
    if actor_id:
        user_ids.discard(actor_id)

    if not user_ids:
        return []

    usuarios = list(CustomUser.objects.filter(pk__in=user_ids, is_active=True))
    resultado = [u for u in usuarios if usuario_pode_acessar_chamado(u, ticket)]

    if somente_nao_operadores:
        return [u for u in resultado if not usuario_eh_operador_helpdesk(u)]

    # Membro TI / admin não notifica outro TI / admin em eventos gerais
    if actor and usuario_eh_operador_helpdesk(actor):
        resultado = [u for u in resultado if not usuario_eh_operador_helpdesk(u)]

    return resultado


def destinatarios_finalizacao(ticket: Ticket, actor):
    """Finalizados: notifica apenas quem não é membro TI / administrador."""
    return destinatarios_notificacao(ticket, actor, somente_nao_operadores=True)


def _url_chamado(ticket_id: int) -> str:
    return f'/helpdesk/?ticket={ticket_id}'


def enviar_push_usuario(
    user,
    titulo: str,
    corpo: str,
    url: str,
    tag: str,
    tipo: str = '',
    *,
    is_interno: bool = False,
    ticket_status: str = '',
) -> None:
    """Envia push para todas as subscriptions ativas do usuário."""
    if not _vapid_configurado():
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning('pywebpush não instalado — push ignorado.')
        return

    payload = json.dumps({
        'title': titulo,
        'body': corpo,
        'url': url,
        'tag': tag,
        'tipo': tipo or '',
        'is_interno': bool(is_interno),
        'ticket_status': ticket_status or '',
    })

    vapid_claims = {'sub': settings.VAPID_ADMIN_EMAIL}

    subscriptions = PushSubscription.objects.filter(user=user, is_active=True)
    for sub in subscriptions:
        subscription_info = {
            'endpoint': sub.endpoint,
            'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=vapid_claims,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status in (404, 410):
                sub.is_active = False
                sub.save(update_fields=['is_active'])
            else:
                logger.warning('Falha ao enviar push para subscription %s: %s', sub.pk, exc)
        except Exception as exc:
            logger.warning('Erro inesperado ao enviar push: %s', exc)


def notificar_evento_chamado(
    ticket: Ticket,
    actor,
    tipo: str,
    mensagem: str,
    *,
    somente_nao_operadores: bool = False,
) -> None:
    """Dispara push para stakeholders do chamado conforme o tipo de evento."""
    titulo_base = _TITULOS_EVENTO.get(tipo, 'Atualização no helpdesk')
    titulo = f'{titulo_base}: #{ticket.pk}'
    corpo = f'{ticket.title}\n{mensagem}' if mensagem else ticket.title
    url = _url_chamado(ticket.pk)
    # Tag única por evento — mesma tag faz Chrome/Windows substituir sem novo toast
    tag = f'helpdesk-{ticket.pk}-{tipo}-{int(time.time() * 1000)}'

    destinatarios = destinatarios_notificacao(
        ticket, actor, somente_nao_operadores=somente_nao_operadores,
    )
    for usuario in destinatarios:
        enviar_push_usuario(
            usuario, titulo, corpo, url, tag, tipo=tipo,
            ticket_status=ticket.status,
        )


def notificar_usuarios_direto(
    ticket: Ticket,
    usuarios,
    tipo: str,
    mensagem: str,
    *,
    is_interno: bool = False,
) -> None:
    """Push para lista explícita (ex.: mencões @) — ignora filtro TI↔TI."""
    titulo_base = _TITULOS_EVENTO.get(tipo, 'Atualização no helpdesk')
    titulo = f'{titulo_base}: #{ticket.pk}'
    corpo = f'{ticket.title}\n{mensagem}' if mensagem else ticket.title
    url = _url_chamado(ticket.pk)
    tag = f'helpdesk-{ticket.pk}-{tipo}-{int(time.time() * 1000)}'
    for usuario in usuarios:
        enviar_push_usuario(
            usuario, titulo, corpo, url, tag, tipo=tipo,
            is_interno=is_interno,
            ticket_status=ticket.status,
        )


def agendar_notificacao_chamado(
    ticket: Ticket,
    actor,
    tipo: str,
    mensagem: str,
    *,
    somente_nao_operadores: bool = False,
) -> None:
    """Agenda push após commit da transação atual."""
    from django.db import transaction

    ticket_id = ticket.pk

    def _disparar():
        from django.db import close_old_connections
        close_old_connections()
        try:
            ticket_atual = Ticket.objects.get(pk=ticket_id)
            notificar_evento_chamado(
                ticket_atual,
                actor,
                tipo,
                mensagem,
                somente_nao_operadores=somente_nao_operadores,
            )
        except Ticket.DoesNotExist:
            pass
        finally:
            close_old_connections()

    def _disparar_async():
        import threading
        threading.Thread(target=_disparar, daemon=True).start()

    transaction.on_commit(_disparar_async)


def agendar_notificacao_mencoes(
    ticket: Ticket,
    usuarios,
    mensagem: str,
    *,
    is_interno: bool = False,
) -> None:
    """
    Agenda push de menção para usuários específicos após o commit.

    Sempre notifica quem foi @mencionado — inclusive outro membro TI / admin.
    Som de menção no cliente é silenciado se is_interno ou chamado finalizado.
    """
    from django.db import transaction

    ticket_id = ticket.pk
    user_ids = [u.pk for u in usuarios if u is not None]
    interno = bool(is_interno)

    if not user_ids:
        return

    def _disparar():
        from django.db import close_old_connections
        from core.models import CustomUser
        close_old_connections()
        try:
            ticket_atual = Ticket.objects.get(pk=ticket_id)
            users = list(CustomUser.objects.filter(pk__in=user_ids, is_active=True))
            if users:
                notificar_usuarios_direto(
                    ticket_atual, users, EVENTO_MENTION, mensagem,
                    is_interno=interno,
                )
        except Ticket.DoesNotExist:
            pass
        finally:
            close_old_connections()

    def _disparar_async():
        import threading
        threading.Thread(target=_disparar, daemon=True).start()

    transaction.on_commit(_disparar_async)
