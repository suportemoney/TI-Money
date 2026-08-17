"""Parse e processamento de menções @username em comentários do helpdesk."""

import re

from django.utils import timezone

from core.models import CustomUser
from helpdesk.models import TicketMention
from helpdesk.ticket_access import usuario_eh_operador_helpdesk

# Username estilo Instagram/WhatsApp após @
MENTION_RE = re.compile(r'(?<!\w)@([A-Za-z0-9_.+-]+)')

# Alias virtual do Assistente (não é CustomUser)
ASSISTENTE_ALIASES = frozenset({'assistente', 'ia', 'assist'})


def extrair_usernames_mencionados(texto: str) -> list[str]:
    """Extrai usernames únicos na ordem em que aparecem no texto."""
    if not texto:
        return []
    vistos = set()
    resultado = []
    for username in MENTION_RE.findall(texto):
        chave = username.lower()
        if chave not in vistos:
            vistos.add(chave)
            resultado.append(username)
    return resultado


def texto_menciona_assistente(texto: str) -> bool:
    """True se o texto contém @assistente (alias virtual)."""
    for username in extrair_usernames_mencionados(texto or ''):
        if username.lower() in ASSISTENTE_ALIASES:
            return True
    return False


def processar_mencoes(ticket, comment, autor) -> list:
    """
    Processa @mentions de um comentário criado por operador TI/admin.

    Concede acesso via co_authors, cria TicketMention e retorna usuários mencionados.
    Ignora o alias virtual @assistente.
    """
    if not autor or not usuario_eh_operador_helpdesk(autor):
        return []

    usernames = extrair_usernames_mencionados(comment.text or '')
    if not usernames:
        return []

    mencionados = []
    for username in usernames:
        if username.lower() in ASSISTENTE_ALIASES:
            continue
        user = CustomUser.objects.filter(username__iexact=username, is_active=True).first()
        if not user or user.pk == autor.pk:
            continue
        ticket.co_authors.add(user)
        TicketMention.objects.get_or_create(
            ticket=ticket,
            user=user,
            comment=comment,
            defaults={'mentioned_by': autor},
        )
        mencionados.append(user)
    return mencionados


def processar_mencoes_assistente(ticket, comment) -> list:
    """
    Processa @mentions em comentário do Assistente (author=None).
    Concede acesso (co_authors) a qualquer usuário ativo mencionado.
    """
    usernames = extrair_usernames_mencionados(comment.text or '')
    if not usernames:
        return []

    mencionados = []
    for username in usernames:
        if username.lower() in ASSISTENTE_ALIASES:
            continue
        user = CustomUser.objects.filter(username__iexact=username, is_active=True).first()
        if not user:
            continue
        ticket.co_authors.add(user)
        TicketMention.objects.get_or_create(
            ticket=ticket,
            user=user,
            comment=comment,
            defaults={'mentioned_by': None},
        )
        mencionados.append(user)
    return mencionados


def marcar_mencoes_vistas(ticket, user) -> int:
    """Marca menções não vistas do usuário no chamado (ao abrir o drawer)."""
    if not user or not user.is_authenticated:
        return 0
    return TicketMention.objects.filter(
        ticket=ticket,
        user=user,
        seen_at__isnull=True,
    ).update(seen_at=timezone.now())
