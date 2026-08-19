"""Renderização leve e segura de Markdown para o chat de memória."""

from __future__ import annotations

import html
import re

from django.utils.safestring import mark_safe

_RE_BOLD = re.compile(r'\*\*(.+?)\*\*')
_RE_ITALIC = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)')
_RE_CODE = re.compile(r'`([^`]+)`')
_RE_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')
# Mesmo padrão do helpdesk (@username) — texto já escapado
_RE_MENTION = re.compile(r'(?<!\w)@([A-Za-z0-9_.+-]+)')


def _inline(texto: str) -> str:
    """Aplica formatação inline em texto já escapado."""
    texto = _RE_CODE.sub(r'<code class="memoria-md-code">\1</code>', texto)
    texto = _RE_LINK.sub(
        r'<a href="\2" class="memoria-md-link" target="_blank" rel="noopener noreferrer">\1</a>',
        texto,
    )
    texto = _RE_BOLD.sub(r'<strong>\1</strong>', texto)
    texto = _RE_ITALIC.sub(r'<em>\1</em>', texto)
    # Destaca @menção (Assistente e TI usam o mesmo visual do chat)
    texto = _RE_MENTION.sub(
        r'<span class="font-semibold text-sky-600 bg-sky-50 px-0.5 rounded">@\1</span>',
        texto,
    )
    return texto


def _eh_sep_tabela(linha: str) -> bool:
    """Linha tipo |---|:---| de tabela markdown."""
    s = (linha or '').strip()
    if '|' not in s:
        return False
    miolo = s.strip('|')
    celulas = [c.strip().replace(' ', '') for c in miolo.split('|')]
    if not celulas:
        return False
    return all(re.fullmatch(r':?-{3,}:?', c or '') for c in celulas)


def _celulas_tabela(linha: str) -> list[str]:
    s = (linha or '').strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def render_markdown_leve(texto: str) -> str:
    """
    Converte Markdown simples em HTML seguro (escapa primeiro).
    Suporta: negrito, itálico, código inline, links https, títulos, listas,
    tabelas pipe e parágrafos.
    """
    if not texto:
        return ''

    linhas = texto.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    blocos: list[str] = []
    i = 0
    n = len(linhas)

    while i < n:
        linha = linhas[i]
        stripped = linha.strip()

        if not stripped:
            i += 1
            continue

        # Título # ## ###
        m_h = re.match(r'^(#{1,3})\s+(.+)$', stripped)
        if m_h:
            nivel = len(m_h.group(1))
            conteudo = _inline(html.escape(m_h.group(2).strip()))
            blocos.append(f'<h{nivel} class="memoria-md-h">{conteudo}</h{nivel}>')
            i += 1
            continue

        # Tabela pipe: cabeçalho + separador |---|
        if '|' in stripped and i + 1 < n and _eh_sep_tabela(linhas[i + 1]):
            cab = _celulas_tabela(stripped)
            i += 2
            corpo: list[list[str]] = []
            while i < n:
                s = linhas[i].strip()
                if not s or '|' not in s or _eh_sep_tabela(s):
                    break
                if re.match(r'^(#{1,3}\s+|\d+\.\s+|[-*•]\s+)', s):
                    break
                corpo.append(_celulas_tabela(s))
                i += 1
            thead = ''.join(
                f'<th>{_inline(html.escape(c))}</th>' for c in cab
            )
            rows = []
            for row in corpo:
                while len(row) < len(cab):
                    row.append('')
                tds = ''.join(
                    f'<td>{_inline(html.escape(c))}</td>' for c in row[: len(cab)]
                )
                rows.append(f'<tr>{tds}</tr>')
            blocos.append(
                '<div class="memoria-md-table-wrap">'
                f'<table class="memoria-md-table"><thead><tr>{thead}</tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>'
            )
            continue

        # Lista numerada
        if re.match(r'^\d+\.\s+', stripped):
            itens: list[str] = []
            while i < n:
                s = linhas[i].strip()
                m = re.match(r'^\d+\.\s+(.+)$', s)
                if not m:
                    break
                itens.append(f'<li>{_inline(html.escape(m.group(1).strip()))}</li>')
                i += 1
            blocos.append(f'<ol class="memoria-md-list">{"".join(itens)}</ol>')
            continue

        # Lista com marcadores
        if re.match(r'^[-*•]\s+', stripped):
            itens = []
            while i < n:
                s = linhas[i].strip()
                m = re.match(r'^[-*•]\s+(.+)$', s)
                if not m:
                    break
                itens.append(f'<li>{_inline(html.escape(m.group(1).strip()))}</li>')
                i += 1
            blocos.append(f'<ul class="memoria-md-list">{"".join(itens)}</ul>')
            continue

        # Parágrafo (linhas até linha em branco ou início de outro bloco)
        partes: list[str] = []
        while i < n:
            s = linhas[i].strip()
            if not s:
                break
            if re.match(r'^(#{1,3}\s+|\d+\.\s+|[-*•]\s+)', s):
                break
            if '|' in s and i + 1 < n and _eh_sep_tabela(linhas[i + 1]):
                break
            partes.append(_inline(html.escape(s)))
            i += 1
        if partes:
            blocos.append(f'<p class="memoria-md-p">{"<br>".join(partes)}</p>')

    return ''.join(blocos) or f'<p class="memoria-md-p">{_inline(html.escape(texto.strip()))}</p>'


def markdown_leve_safe(texto: str):
    """Versão mark_safe para templates Django."""
    return mark_safe(render_markdown_leve(texto or ''))
