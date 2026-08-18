from __future__ import annotations

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from core.templatetags.asset_version import url_com_versao, versao_assets
from helpdesk.mentions import MENTION_RE
from helpdesk.ticket_access import (
    usuario_pode_contestar_chamado,
    usuario_pode_gerenciar_comentarios,
    usuario_pode_ver_comentarios_internos,
)

register = template.Library()


@register.simple_tag
def helpdesk_static(path):
    """URL de static do helpdesk com ?v= para invalidar cache do browser."""
    return url_com_versao(path)


@register.simple_tag
def helpdesk_v():
    """Só o valor da versão (útil em hx-get e meta tags)."""
    return versao_assets()


@register.simple_tag(takes_context=True)
def pode_contestar_chamado(context, ticket):
    """Indica se o usuário logado pode contestar o chamado."""
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return False
    return usuario_pode_contestar_chamado(request.user, ticket)


@register.simple_tag(takes_context=True)
def usuario_pode_menu_comentario(context):
    """Staff/superuser — menu ⋮ editar/excluir no chat."""
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return False
    return usuario_pode_gerenciar_comentarios(request.user)


@register.simple_tag(takes_context=True)
def usuario_ve_comentarios_internos(context):
    """True se o usuário logado vê mensagens internas (TI/staff)."""
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return False
    return usuario_pode_ver_comentarios_internos(request.user)


@register.filter(name='highlight_mentions')
def highlight_mentions(text):
    """Destaca @username no texto do comentário (HTML seguro)."""
    if not text:
        return ''
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    escaped = escape(normalized)

    def _repl(match):
        username = match.group(1)
        return (
            f'<span class="font-semibold text-sky-600 bg-sky-50 px-0.5 rounded">'
            f'@{escape(username)}</span>'
        )

    # reaplicamos no texto já escapado; padrão não contém HTML
    highlighted = MENTION_RE.sub(_repl, escaped)
    return mark_safe(highlighted)


_RE_NEGRITO_DUPLO = re.compile(r'\*\*(.+?)\*\*')
_RE_NEGRITO_SIMPLES = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)')


@register.filter(name='negrito_asterisco')
def negrito_asterisco(text):
    """Converte *texto* e **texto** em negrito (HTML escapado)."""
    if not text:
        return ''
    escaped = escape(str(text))
    escaped = _RE_NEGRITO_DUPLO.sub(r'<strong>\1</strong>', escaped)
    escaped = _RE_NEGRITO_SIMPLES.sub(r'<strong>\1</strong>', escaped)
    return mark_safe(escaped)
