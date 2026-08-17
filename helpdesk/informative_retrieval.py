"""Retrieval de comunicados da Central Informativa para o Assistente."""

from __future__ import annotations

import re
from typing import Any

from django.db.models import Q
from django.utils import timezone

from helpdesk.models import InformativeMessage, Ticket


def _tokens(*textos: str) -> set[str]:
    tokens: set[str] = set()
    for texto in textos:
        if not texto:
            continue
        for raw in re.split(r'[^\w]+', str(texto).lower(), flags=re.UNICODE):
            tok = raw.strip('_')
            if len(tok) >= 3:
                tokens.add(tok)
    try:
        from integracoes.sinonimos_retrieval import expandir_tokens_com_sinonimos
        return expandir_tokens_com_sinonimos(tokens)
    except Exception:
        return tokens


def _chave_tokens(palavras_chave: str) -> set[str]:
    if not palavras_chave:
        return set()
    partes = re.split(r'[,;|/]+', palavras_chave)
    return _tokens(*partes)


def buscar_comunicados_relevantes(ticket: Ticket, *, limite: int = 5) -> list[dict[str, Any]]:
    """Busca comunicados ativos/vigentes por palavras-chave e texto do chamado."""
    agora = timezone.now()
    qs = InformativeMessage.objects.filter(
        arquivado=False,
        ativo=True,
    ).filter(
        Q(valido_ate__isnull=True) | Q(valido_ate__gte=agora),
    ).select_related('created_by').order_by('-created_at')[:80]

    tag_nome = ''
    if getattr(ticket, 'tag_id', None) and ticket.tag_id:
        try:
            tag_nome = ticket.tag.nome
        except Exception:
            tag_nome = ''

    query_tokens = _tokens(
        ticket.title or '',
        ticket.description or '',
        tag_nome,
        ticket.category.name if ticket.category_id else '',
        ticket.specific_category.name if ticket.specific_category_id else '',
    )
    if not query_tokens:
        return []

    ranqueados: list[tuple[int, InformativeMessage]] = []
    for msg in qs:
        chave = _chave_tokens(msg.palavras_chave or '')
        texto_tok = _tokens(msg.text or '')
        score = 0
        # Palavras-chave estruturadas têm peso maior
        score += 3 * len(query_tokens & chave)
        score += len(query_tokens & texto_tok)
        if score > 0:
            ranqueados.append((score, msg))

    ranqueados.sort(key=lambda x: (-x[0], -x[1].pk))
    resultados = []
    for score, msg in ranqueados[:limite]:
        resultados.append({
            'id': msg.pk,
            'texto': (msg.text or '')[:800],
            'palavras_chave': msg.palavras_chave or '',
            'valido_ate': msg.valido_ate.isoformat() if msg.valido_ate else None,
            'score': score,
            'autor': msg.created_by.username if msg.created_by_id else '',
        })
    return resultados


def formatar_comunicados_para_contexto(itens: list[dict[str, Any]]) -> str:
    if not itens:
        return ''
    linhas = [
        'Comunicados vigentes da Central Informativa (prevalecem sobre passos genéricos):'
    ]
    for item in itens:
        chave = item.get('palavras_chave') or '—'
        linhas.append(f"- [#{item['id']}] palavras-chave: {chave}\n  {item['texto']}")
    return '\n'.join(linhas)
