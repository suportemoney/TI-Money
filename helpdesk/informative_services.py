"""Serviços da Central Informativa: keywords, validade, archive e letreiro."""

from __future__ import annotations

import re
from datetime import timedelta

from django.utils import timezone

from helpdesk.models import InformativeMessage

# Stopwords PT básicas (tokens curtos já caem pelo filtro >= 3)
_STOPWORDS = frozenset({
    'para', 'com', 'sem', 'por', 'uma', 'uns', 'umas', 'dos', 'das', 'nos',
    'nas', 'pelo', 'pela', 'pelos', 'pelas', 'que', 'não', 'nao', 'mais',
    'como', 'este', 'esta', 'esse', 'essa', 'isto', 'isso', 'aqui', 'ali',
    'quando', 'onde', 'qual', 'quais', 'quem', 'seu', 'sua', 'seus', 'suas',
    'nosso', 'nossa', 'eles', 'elas', 'você', 'voce', 'vocês', 'voces',
    'está', 'esta', 'estão', 'estao', 'foi', 'ser', 'ter', 'há', 'hao',
    'também', 'tambem', 'ainda', 'já', 'ja', 'só', 'so', 'muito', 'pouco',
    'sobre', 'entre', 'após', 'apos', 'antes', 'depois', 'hoje', 'agora',
    'favor', 'preciso', 'precisa', 'podem', 'pode', 'fazer', 'feito',
})

VALIDADE_HORAS = 2
EXPIRADOS_JANELA_MIN = 15  # modal de prorrogação


def gerar_palavras_chave(texto: str, *, max_tokens: int = 12) -> str:
    """Extrai palavras-chave do texto (tokens >=3, sem stopwords)."""
    tokens: list[str] = []
    visto: set[str] = set()
    for raw in re.split(r'[^\w]+', (texto or '').lower(), flags=re.UNICODE):
        tok = raw.strip('_')
        if len(tok) < 3 or tok in _STOPWORDS or tok.isdigit() and len(tok) < 3:
            continue
        if tok in visto:
            continue
        visto.add(tok)
        tokens.append(tok)
        if len(tokens) >= max_tokens:
            break
    return ', '.join(tokens)


def validade_padrao(*, agora=None):
    agora = agora or timezone.now()
    return agora + timedelta(hours=VALIDADE_HORAS)


def arquivar_comunicados_expirados(*, agora=None) -> list[int]:
    """
    Arquiva comunicados com validade vencida.
    Retorna IDs recém-arquivados nesta execução (para modal TI).
    """
    agora = agora or timezone.now()
    qs = InformativeMessage.objects.filter(
        arquivado=False,
        valido_ate__isnull=False,
        valido_ate__lt=agora,
    )
    ids: list[int] = []
    for msg in qs.iterator():
        msg.marcar_arquivado(por=None, agora=agora)
        ids.append(msg.pk)
    return ids


def mensagens_letreiro_vigentes():
    """Comunicados vigentes marcados para o letreiro neon."""
    agora = timezone.now()
    return (
        InformativeMessage.objects.filter(
            letreiro=True,
            arquivado=False,
            ativo=True,
            valido_ate__gte=agora,
        )
        .select_related('created_by')
        .order_by('-created_at')[:20]
    )


def comunicados_recem_expirados(*, minutos: int = EXPIRADOS_JANELA_MIN):
    """Arquivados por expiração nos últimos N minutos (sem arquivado_por humano)."""
    agora = timezone.now()
    desde = agora - timedelta(minutes=minutos)
    return (
        InformativeMessage.objects.filter(
            arquivado=True,
            arquivado_por__isnull=True,
            arquivado_em__gte=desde,
        )
        .select_related('created_by')
        .order_by('-arquivado_em')[:10]
    )
