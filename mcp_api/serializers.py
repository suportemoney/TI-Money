"""Helpers de paginação e serialização segura (sem senhas/tokens)."""

import re

from django.db.models import Q


LIMITE_MAX = 50
LIMITE_PADRAO = 20


def parse_limit(request, default=LIMITE_PADRAO):
    try:
        limit = int(request.GET.get('limit') or default)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, LIMITE_MAX))


def iso(dt):
    if dt is None:
        return None
    return dt.isoformat()


def user_ref(user):
    if user is None:
        return None
    return {
        'id': user.pk,
        'username': user.username,
        'full_name': user.get_full_name() or user.username,
    }


def serialize_ticket(ticket, detalhe=False):
    data = {
        'id': ticket.pk,
        'title': ticket.title,
        'status': ticket.status,
        'status_display': ticket.get_status_display(),
        'priority': ticket.priority,
        'priority_display': ticket.get_priority_display() if ticket.priority else None,
        'category': ticket.category.name if ticket.category_id else None,
        'specific_category': ticket.specific_category.name if ticket.specific_category_id else None,
        'equipe': ticket.equipe.name if ticket.equipe_id else None,
        'requester_name': ticket.requester_name,
        'requester_user': user_ref(ticket.requester_user),
        'created_by': user_ref(ticket.created_by),
        'assigned_to': user_ref(ticket.assigned_to),
        'is_active': ticket.is_active,
        'is_archived': ticket.is_archived,
        'is_rejected': ticket.is_rejected,
        'created_at': iso(ticket.created_at),
        'updated_at': iso(ticket.updated_at),
        'resolved_at': iso(ticket.resolved_at),
    }
    if detalhe:
        data['description'] = ticket.description
        data['rejection_reason'] = ticket.rejection_reason
        data['resolved_by'] = user_ref(ticket.resolved_by)
    return data


def serialize_comment(comment):
    if comment.is_assistente:
        author = {'id': None, 'username': 'Assistente', 'full_name': 'Assistente'}
    else:
        author = user_ref(comment.author)
    return {
        'id': comment.pk,
        'ticket_id': comment.ticket_id,
        'author': author,
        'is_assistente': comment.is_assistente,
        'is_interno': bool(getattr(comment, 'is_interno', False)),
        'text': comment.text,
        'has_attachment': bool(comment.attachment),
        'is_active': comment.is_active,
        'created_at': iso(comment.created_at),
    }


def serialize_chip(chip):
    return {
        'id': chip.pk,
        'line_number': chip.line_number,
        'formatted_line_number': chip.formatted_line_number,
        'status': chip.status,
        'status_display': chip.get_status_display(),
        'usage_status': chip.usage_status,
        'usage_status_display': chip.get_usage_status_display(),
        'technology': chip.technology,
        'iccid': chip.iccid,
        'plan_type': chip.plan_type,
        'operator': chip.operator.name if chip.operator_id else None,
        'batch': chip.batch.label if chip.batch_id else None,
        'observacao': chip.observacao,
        'email_vinculado': chip.email_vinculado,
        'is_active': chip.is_active,
        'activated_at': iso(chip.activated_at) if chip.activated_at else None,
        'created_at': iso(chip.created_at),
        'updated_at': iso(chip.updated_at),
    }


def serialize_chip_movement(mov):
    return {
        'id': mov.pk,
        'chip_id': mov.chip_id,
        'action': mov.action,
        'action_display': mov.get_action_display(),
        'employee_name': mov.employee_name,
        'employee_user': user_ref(mov.employee_user),
        'registered_by': user_ref(mov.registered_by),
        'timestamp': iso(mov.timestamp),
    }


def serialize_equipment(eq):
    return {
        'id': eq.pk,
        'type': eq.type,
        'type_display': eq.get_type_display(),
        'tag': eq.tag,
        'serial_number': eq.serial_number,
        'brand_model': eq.brand_model,
        'status': eq.status,
        'status_display': eq.get_status_display(),
        'current_employee': eq.current_employee,
        'purchase_date': iso(eq.purchase_date) if eq.purchase_date else None,
        'warranty_end': iso(eq.warranty_end) if eq.warranty_end else None,
        'purchase_value': str(eq.purchase_value),
        'is_warranty_expired': eq.is_warranty_expired,
        'created_at': iso(eq.created_at),
        'updated_at': iso(eq.updated_at),
    }


def serialize_equipment_log(log):
    return {
        'id': log.pk,
        'equipment_id': log.equipment_id,
        'action': log.action,
        'action_display': log.get_action_display(),
        'employee_name': log.employee_name,
        'timestamp': iso(log.timestamp),
    }


def serialize_domain(domain):
    return {
        'id': domain.pk,
        'name': domain.name,
        'created_at': iso(domain.created_at),
    }


def serialize_email_account(account):
    chip = account.chip
    return {
        'id': account.pk,
        'username': account.username,
        'domain': account.domain.name if account.domain_id else None,
        'address': account.address,
        'employee_name': account.employee_name,
        'status': account.status,
        'status_display': account.get_status_display(),
        'chip_id': chip.pk if chip else None,
        'line_number': chip.line_number if chip else None,
        'formatted_line_number': chip.formatted_line_number if chip else None,
        'created_at': iso(account.created_at),
        'updated_at': iso(account.updated_at),
    }


def serialize_user(user):
    return {
        'id': user.pk,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'full_name': user.get_full_name() or user.username,
        'email': user.email,
        'role': user.role,
        'role_display': user.get_role_display(),
        'equipes': [{'id': e.pk, 'name': e.name} for e in user.equipes.all()],
        'is_active': user.is_active,
        'is_staff': user.is_staff,
        'last_login': iso(user.last_login),
        'created_at': iso(user.created_at),
        'updated_at': iso(user.updated_at),
    }


def serialize_equipe(equipe):
    return {
        'id': equipe.pk,
        'name': equipe.name,
        'is_active': equipe.is_active,
        'created_at': iso(equipe.created_at),
    }


def serialize_acao(reg):
    return {
        'id': reg.pk,
        'modulo': reg.modulo,
        'acao': reg.acao,
        'descricao': reg.descricao,
        'actor': user_ref(reg.actor),
        'object_repr': reg.object_repr,
        'object_id': reg.object_id,
        'content_type': reg.content_type.model if reg.content_type_id else None,
        'metadata': reg.metadata or {},
        'timestamp': iso(reg.timestamp),
    }


def _tokens_busca(q: str) -> list[str]:
    """Tokens úteis (len>=3) para busca por nome composto."""
    return [
        t for t in re.split(r'[^\w]+', (q or '').strip(), flags=re.UNICODE)
        if len(t) >= 3
    ]


def filtro_q_usuario(qs, q):
    if not q:
        return qs
    filtro = (
        Q(username__icontains=q)
        | Q(first_name__icontains=q)
        | Q(last_name__icontains=q)
        | Q(email__icontains=q)
    )
    if q.isdigit():
        filtro |= Q(pk=int(q))
    tokens = _tokens_busca(q)
    if tokens:
        filtro |= Q(first_name__icontains=tokens[0]) | Q(last_name__icontains=tokens[0])
        if len(tokens) >= 2:
            filtro |= (
                Q(first_name__icontains=tokens[0]) & Q(last_name__icontains=tokens[1])
            )
    return qs.filter(filtro)


def filtro_q_email(qs, q):
    """
    Busca e-mail por endereço, username, domínio ou nome.
    Aceita sobrenome extra (ex.: 'Vitoria Silva Camargo' acha 'Vitoria Silva'
    e username vitoriacamargo...).
    """
    termo = (q or '').strip()
    if not termo:
        return qs
    filtro = (
        Q(username__icontains=termo)
        | Q(employee_name__icontains=termo)
        | Q(domain__name__icontains=termo)
    )
    if '@' in termo:
        user_part, _, domain_part = termo.partition('@')
        if user_part:
            filtro |= Q(username__icontains=user_part)
        if domain_part:
            filtro |= Q(domain__name__icontains=domain_part)
    if termo.isdigit():
        filtro |= Q(pk=int(termo))
    tokens = _tokens_busca(termo)
    if tokens:
        for tok in tokens:
            filtro |= Q(username__icontains=tok) | Q(employee_name__icontains=tok)
        if len(tokens) >= 2:
            q_nome = Q()
            for tok in tokens[:2]:
                q_nome &= Q(employee_name__icontains=tok)
            filtro |= q_nome
            filtro |= Q(username__icontains=''.join(tokens[:2]))
            filtro |= Q(username__icontains=tokens[0] + tokens[-1])
    return qs.filter(filtro)


def serialize_chunk(chunk, detalhe=False):
    """Serializa chunk de aprendizado do Assistente (sem dados sensíveis)."""
    data = {
        'id': chunk.pk,
        'titulo': chunk.titulo,
        'categoria_hint': chunk.categoria_hint,
        'origem': chunk.origem,
        'ativo': chunk.ativo,
        'tags': chunk.tags if isinstance(chunk.tags, list) else [],
        'fonte_ticket_ids': chunk.fonte_ticket_ids if isinstance(chunk.fonte_ticket_ids, list) else [],
        'criado_em': iso(chunk.criado_em),
        'atualizado_em': iso(chunk.atualizado_em),
    }
    if detalhe:
        data['conteudo'] = chunk.conteudo
    else:
        data['conteudo_preview'] = (chunk.conteudo or '')[:180]
    return data

