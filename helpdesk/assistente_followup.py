"""
Follow-up automático do Assistente quando solicitante/criador NUNCA respondeu.

Escopo: chamados em que a IA mandou mensagem pública e o solicitante/criador
não enviou nenhuma mensagem depois (sem diálogo). Não se aplica a chamados
que já tiveram troca de mensagens com a IA.

- 5 min sem resposta → mensagem pública com @menção
- NÃO recusa/finaliza por falta de resposta (Novos ou Pendente).
  Fechar só com pedido do solicitante/criador ou de membro TI.

Também sincroniza chamados antigos (ex.: Novos com IA antes do deploy).
Disparado no poll HTMX (throttle), como o arquivamento.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

SEGUNDOS_MENCAO = 5 * 60
THROTTLE_SEGUNDOS = 30

_ultimo_run = None


def limpar_espera_assistente(ticket, *, save: bool = True) -> None:
    """Zera timers de follow-up (resposta do usuário, escalonamento, etc.)."""
    if ticket.assistente_aguardando_desde is None and ticket.assistente_followup_mencao_em is None:
        return
    ticket.assistente_aguardando_desde = None
    ticket.assistente_followup_mencao_em = None
    if save:
        ticket.save(update_fields=[
            'assistente_aguardando_desde',
            'assistente_followup_mencao_em',
            'updated_at',
        ])


def _ids_solicitante_criador(ticket) -> set[int]:
    ids = set()
    if ticket.requester_user_id:
        ids.add(ticket.requester_user_id)
    if ticket.created_by_id:
        ids.add(ticket.created_by_id)
    return ids


def primeira_mensagem_assistente_publica(ticket):
    """Primeira mensagem pública ativa do Assistente no chamado."""
    from helpdesk.models import Comment

    return (
        Comment.objects.filter(
            ticket=ticket,
            is_active=True,
            is_assistente=True,
            is_interno=False,
        )
        .order_by('created_at')
        .first()
    )


def solicitante_respondeu_apos_assistente(ticket, desde=None) -> bool:
    """
    True se solicitante/criador já mandou mensagem pública após a IA
    (configura 'teve diálogo' — exceção do follow-up automático).
    """
    from helpdesk.models import Comment

    ids = _ids_solicitante_criador(ticket)
    if not ids:
        return False

    qs = Comment.objects.filter(
        ticket=ticket,
        is_active=True,
        is_assistente=False,
        is_interno=False,
        author_id__in=ids,
    )
    if desde is not None:
        qs = qs.filter(created_at__gt=desde)
    else:
        primeira = primeira_mensagem_assistente_publica(ticket)
        if not primeira:
            return False
        qs = qs.filter(created_at__gt=primeira.created_at)
    return qs.exists()


def ticket_elegivel_followup_sem_resposta(ticket) -> bool:
    """IA falou em público e solicitante/criador nunca respondeu depois."""
    primeira = primeira_mensagem_assistente_publica(ticket)
    if not primeira:
        return False
    if solicitante_respondeu_apos_assistente(ticket, desde=primeira.created_at):
        return False
    return True


def marcar_espera_assistente(ticket) -> None:
    """
    Marca início da espera na 1ª mensagem pública do Assistente.
    Não reinicia em bolhas seguintes; não marca se já houve diálogo.
    """
    if ticket.assistente_aguardando_desde is not None:
        return
    if solicitante_respondeu_apos_assistente(ticket):
        return
    primeira = primeira_mensagem_assistente_publica(ticket)
    inicio = primeira.created_at if primeira else timezone.now()
    ticket.assistente_aguardando_desde = inicio
    ticket.assistente_followup_mencao_em = None
    ticket.save(update_fields=[
        'assistente_aguardando_desde',
        'assistente_followup_mencao_em',
        'updated_at',
    ])


def usuario_e_solicitante_ou_criador(ticket, user) -> bool:
    if not user or not getattr(user, 'pk', None):
        return False
    return user.pk in {
        ticket.requester_user_id,
        ticket.created_by_id,
    }


def _usuarios_para_cobrar(ticket) -> list:
    """Solicitante e/ou criador com conta ativa (para @menção)."""
    vistos = set()
    usuarios = []
    for user in (ticket.requester_user, ticket.created_by):
        if not user or not user.is_active:
            continue
        if user.pk in vistos:
            continue
        vistos.add(user.pk)
        usuarios.append(user)
    return usuarios


def _registrar_mencoes_assistente(ticket, comment, usuarios) -> list:
    """Cria TicketMention + co_authors para cobrança do Assistente."""
    from helpdesk.models import TicketMention

    mencionados = []
    for user in usuarios:
        ticket.co_authors.add(user)
        TicketMention.objects.get_or_create(
            ticket=ticket,
            user=user,
            comment=comment,
            defaults={'mentioned_by': None},
        )
        mencionados.append(user)
    return mencionados


def _enviar_cobranca_mencao(ticket) -> bool:
    """Envia @menção pedindo resposta. Retorna True se enviou."""
    from helpdesk.assistente_services import AssistenteServiceError, send_assistente_message
    from helpdesk.audit import log_comentario
    from helpdesk.models import Comment
    from helpdesk.notifications import (
        EVENTO_COMMENT,
        agendar_notificacao_chamado,
        agendar_notificacao_mencoes,
    )
    from helpdesk.views.kanban import adicionar_nao_lido

    usuarios = _usuarios_para_cobrar(ticket)
    if usuarios:
        tags = ' '.join(f'@{u.username}' for u in usuarios)
        texto = (
            f'{tags}, ainda estou aguardando sua resposta neste chamado para continuar. '
            'Pode responder por aqui, por favor?'
        )
    else:
        nome = (ticket.requester_name or 'solicitante').strip()
        texto = (
            f'{nome}, ainda estou aguardando sua resposta neste chamado para continuar. '
            'Pode responder por aqui, por favor?'
        )

    try:
        resultado = send_assistente_message(
            ticket.pk,
            texto,
            interno=False,
            followup_mencao=True,
        )
    except AssistenteServiceError:
        logger.exception('Falha ao enviar cobrança @ no ticket %s', ticket.pk)
        return False

    comment_id = resultado.get('comment_id')
    comment = Comment.objects.filter(pk=comment_id).first() if comment_id else None
    mencionados = []
    if comment and usuarios:
        mencionados = _registrar_mencoes_assistente(ticket, comment, usuarios)

    preview = texto[:120]
    try:
        meta = {
            'is_assistente': True,
            'followup': 'mencao_5min',
        }
        if mencionados:
            meta['acao_ui'] = 'MENTION'
            meta['mention_user_ids'] = [u.pk for u in mencionados]
        log_comentario(ticket, None, preview, metadata=meta)
    except Exception:
        pass
    try:
        adicionar_nao_lido(ticket, None, usuarios_extra=mencionados or None)
    except Exception:
        pass
    agendar_notificacao_chamado(ticket, None, EVENTO_COMMENT, preview)
    if mencionados:
        agendar_notificacao_mencoes(ticket, mencionados, preview)
    return True


def sincronizar_espera_tickets_sem_resposta() -> int:
    """
    Preenche assistente_aguardando_desde em Novos onde a IA falou e
    o solicitante/criador nunca respondeu (inclui chamados anteriores ao deploy).
    """
    from helpdesk.assistente_services import assistente_pode_atuar
    from helpdesk.models import Comment, Ticket

    # Tickets com pelo menos uma mensagem pública do Assistente
    com_ia = Comment.objects.filter(
        is_active=True,
        is_assistente=True,
        is_interno=False,
    ).values_list('ticket_id', flat=True).distinct()

    qs = (
        Ticket.objects.filter(
            pk__in=com_ia,
            is_active=True,
            is_archived=False,
            is_rejected=False,
            assistente_escalado=False,
            status=Ticket.StatusChoices.NEW,
        )
        .select_related('requester_user', 'created_by', 'assigned_to')
        .order_by('id')[:80]
    )

    sincronizados = 0
    for ticket in qs:
        try:
            if not assistente_pode_atuar(ticket):
                if ticket.assistente_aguardando_desde is not None:
                    limpar_espera_assistente(ticket)
                continue

            primeira = primeira_mensagem_assistente_publica(ticket)
            if not primeira:
                continue

            if solicitante_respondeu_apos_assistente(ticket, desde=primeira.created_at):
                # Teve diálogo — não entra no follow-up automático
                if ticket.assistente_aguardando_desde is not None:
                    limpar_espera_assistente(ticket)
                continue

            # Sem resposta: ancora o timer na 1ª mensagem da IA
            if ticket.assistente_aguardando_desde != primeira.created_at:
                ticket.assistente_aguardando_desde = primeira.created_at
                # Mantém menção se já cobrou depois da 1ª msg; senão zera
                if (
                    ticket.assistente_followup_mencao_em
                    and ticket.assistente_followup_mencao_em < primeira.created_at
                ):
                    ticket.assistente_followup_mencao_em = None
                ticket.save(update_fields=[
                    'assistente_aguardando_desde',
                    'assistente_followup_mencao_em',
                    'updated_at',
                ])
                sincronizados += 1
        except Exception:
            logger.exception('Falha ao sincronizar espera do ticket %s', ticket.pk)

    return sincronizados


def processar_followups_assistente(*, forcar: bool = False) -> dict:
    """
    1) Sincroniza Novos com IA sem resposta (legado + atuais)
    2) Aplica menção (5min). Não recusa/finaliza por silêncio.
    """
    global _ultimo_run

    agora = timezone.now()
    if not forcar and _ultimo_run and (agora - _ultimo_run).total_seconds() < THROTTLE_SEGUNDOS:
        return {'ok': True, 'skipped': True}

    _ultimo_run = agora

    from helpdesk.assistente_services import assistente_pode_atuar
    from helpdesk.models import Ticket

    sincronizados = sincronizar_espera_tickets_sem_resposta()

    mencoes = 0
    qs = (
        Ticket.objects.filter(
            is_active=True,
            is_archived=False,
            is_rejected=False,
            assistente_escalado=False,
            assistente_aguardando_desde__isnull=False,
            assistente_followup_mencao_em__isnull=True,
            status=Ticket.StatusChoices.NEW,
        )
        .select_related('requester_user', 'created_by', 'assigned_to')
        .order_by('assistente_aguardando_desde')[:40]
    )

    for ticket in qs:
        try:
            if not assistente_pode_atuar(ticket):
                limpar_espera_assistente(ticket)
                continue

            # Revalida: se já houve diálogo, sai
            if not ticket_elegivel_followup_sem_resposta(ticket):
                limpar_espera_assistente(ticket)
                continue

            espera = agora - ticket.assistente_aguardando_desde
            if espera < timedelta(seconds=SEGUNDOS_MENCAO):
                continue

            n = Ticket.objects.filter(
                pk=ticket.pk,
                assistente_followup_mencao_em__isnull=True,
                assistente_aguardando_desde=ticket.assistente_aguardando_desde,
                status=Ticket.StatusChoices.NEW,
            ).update(
                assistente_followup_mencao_em=agora,
                updated_at=agora,
            )
            if n != 1:
                continue
            ticket.assistente_followup_mencao_em = agora
            if _enviar_cobranca_mencao(ticket):
                mencoes += 1
            else:
                Ticket.objects.filter(pk=ticket.pk).update(
                    assistente_followup_mencao_em=None,
                    updated_at=timezone.now(),
                )
        except Exception:
            logger.exception('Erro no follow-up do Assistente ticket %s', ticket.pk)

    return {
        'ok': True,
        'sincronizados': sincronizados,
        'mencoes': mencoes,
        'recusas': 0,
    }
