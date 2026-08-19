"""Serviços de escrita/leitura do Assistente (MCP e runtime Django)."""

from __future__ import annotations

import logging
import mimetypes
import os
import re
from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from helpdesk.models import Comment, Ticket, TicketAttachment, TicketSpecificCategory
from helpdesk.ticket_access import usuario_eh_operador_helpdesk


logger = logging.getLogger(__name__)

PRIORIDADES = {c.value for c in Ticket.PriorityChoices}
STATUS_VALIDOS = {c.value for c in Ticket.StatusChoices}


class AssistenteServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def ticket_assumido_pela_ti(ticket: Ticket) -> bool:
    """
    TI assume o atendimento quando há técnico e o chamado NÃO está em Novos.
    Voltar para Novos (mesmo com assigned_to residual) libera o Assistente.
    """
    if ticket.status == Ticket.StatusChoices.NEW:
        return False
    return usuario_eh_operador_helpdesk(ticket.assigned_to)


def ticket_tem_orientacao_interna_pendente(ticket: Ticket) -> bool:
    """
    Último comentário ativo é interno de humano (TI/staff).
    Nesse caso o Assistente deve ler e agir mesmo se já tiver escalado.
    """
    ultimo = (
        Comment.objects.filter(ticket=ticket, is_active=True)
        .order_by('-created_at')
        .only('is_interno', 'is_assistente', 'author_id')
        .first()
    )
    if not ultimo:
        return False
    return bool(ultimo.is_interno and not ultimo.is_assistente and ultimo.author_id)


def assistente_motivo_bloqueio(ticket: Ticket) -> str | None:
    """Retorna o motivo se o Assistente não pode atuar; None se pode."""
    from integracoes.models import AssistenteConfig

    config = AssistenteConfig.get_solo()
    if not config.ativo:
        return 'assistente_inativo'
    if not ticket.is_active or ticket.is_archived:
        return 'ticket_inativo_ou_arquivado'
    if ticket.status == Ticket.StatusChoices.RESOLVED:
        return 'ticket_resolvido'
    # Orientação interna da TI libera atuação mesmo após escalar/assumir
    orientacao_interna = ticket_tem_orientacao_interna_pendente(ticket)
    if ticket.assistente_escalado and not orientacao_interna:
        return 'assistente_escalado'
    if ticket_assumido_pela_ti(ticket) and not orientacao_interna:
        return 'assumido_pela_ti'
    # Bloqueia só chamado interno: solicitante vinculado é operador TI
    if ticket.requester_user_id and usuario_eh_operador_helpdesk(ticket.requester_user):
        return 'solicitante_eh_operador_ti'
    return None


def assistente_pode_atuar(ticket: Ticket) -> bool:
    """Regras para o Assistente continuar conversando no chamado."""
    return assistente_motivo_bloqueio(ticket) is None


_MAX_CHARS_BOLHA = 350

# Rótulos meta que a IA às vezes coloca no texto (não devem ir ao solicitante)
_RE_ROTULO_MENSAGEM = re.compile(
    r'(?im)(?:^|\n)\s*\*{0,2}\s*(?:\d+[ªºa]?\.?\s*)?mensagem(?:\s*\d+)?\s*:?\s*\*{0,2}\s*',
)
_RE_LINHA_PENSAMENTO = re.compile(
    r'(?i)^\s*(?:'
    r'ok[,.]?\s+.+|'
    r'vou\s+\w+.*|'
    r'sem\s+chips?\s+ainda.*|'
    r'(?:pensamento|racioc[ií]nio|nota\s+interna|plano(?:\s+de\s+a[cç][aã]o)?)\s*:?\s*.*|'
    r'analisando(?:\s+o)?\s+chamado.*|'
    r'agora\s+vou\s+.*|'
    r'primeiro\s+vou\s+.*|'
    r'deixa\s+eu\s+.*|'
    r'certo[,.]?\s+vou\s+.*'
    r')\s*$'
)


def limpar_texto_para_solicitante(texto: str) -> str:
    """
    Remove raciocínio/meta da IA, deixando só a fala ao solicitante.
    Ex.: remove 'Ok, vou...' e rótulos '**1ª mensagem:**'.
    """
    texto = (texto or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not texto:
        return ''

    # Se há "1ª mensagem:" / "**2ª mensagem:**", descarta o preâmbulo e junta os corpos
    if _RE_ROTULO_MENSAGEM.search(texto):
        partes = _RE_ROTULO_MENSAGEM.split(texto)
        corpos = [p.strip() for p in partes[1:] if p and p.strip()]
        if corpos:
            texto = '\n\n'.join(corpos)
        else:
            # só rótulo sem corpo
            texto = _RE_ROTULO_MENSAGEM.sub('', texto).strip()

    # Remove linhas de pensamento no início (e linhas em branco extras)
    linhas = texto.split('\n')
    while linhas:
        primeira = linhas[0].strip()
        if not primeira:
            linhas.pop(0)
            continue
        if _RE_LINHA_PENSAMENTO.match(primeira):
            linhas.pop(0)
            continue
        break

    # Remove linhas que são só rótulo residual no meio
    limpas = []
    for ln in linhas:
        s = ln.strip()
        if re.match(r'(?i)^\*{0,2}\s*(?:\d+[ªºa]?\.?\s*)?mensagem(?:\s*\d+)?\s*:?\s*\*{0,2}$', s):
            continue
        limpas.append(ln)

    return '\n'.join(limpas).strip()


def _partir_texto_assistente(texto: str, max_chars: int = _MAX_CHARS_BOLHA) -> list[str]:
    """Parte texto longo em várias bolhas (parágrafos / pedaços ~max_chars)."""
    texto = (texto or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not texto:
        return []

    # Parágrafos separados por linha em branco
    paragrafos = [p.strip() for p in texto.split('\n\n') if p.strip()]
    if len(paragrafos) <= 1 and len(texto) <= max_chars:
        return [texto]

    if len(paragrafos) <= 1:
        # Um bloco longo: quebra por linhas ou por tamanho
        linhas = [ln.strip() for ln in texto.split('\n') if ln.strip()]
        paragrafos = linhas if len(linhas) > 1 else [texto]

    partes: list[str] = []
    atual = ''
    for trecho in paragrafos:
        if len(trecho) > max_chars:
            if atual:
                partes.append(atual)
                atual = ''
            # Pedacos forçados por tamanho
            resto = trecho
            while len(resto) > max_chars:
                corte = resto.rfind(' ', 0, max_chars + 1)
                if corte < max_chars // 2:
                    corte = max_chars
                partes.append(resto[:corte].strip())
                resto = resto[corte:].strip()
            if resto:
                atual = resto
            continue
        candidato = f'{atual}\n\n{trecho}'.strip() if atual else trecho
        if atual and len(candidato) > max_chars:
            partes.append(atual)
            atual = trecho
        else:
            atual = candidato
    if atual:
        partes.append(atual)
    return partes or [texto]


def _notificar_comentario_assistente(ticket: Ticket, texto: str) -> None:
    """Audit + badge + push para comentário do Assistente (uma vez por lote)."""
    preview = (texto or '')[:120]
    try:
        from helpdesk.audit import log_comentario
        log_comentario(ticket, None, preview, metadata={'is_assistente': True})
    except Exception:
        pass
    try:
        from helpdesk.views.kanban import adicionar_nao_lido
        adicionar_nao_lido(ticket, None)
    except Exception:
        pass
    try:
        from helpdesk.notifications import EVENTO_COMMENT, agendar_notificacao_chamado
        agendar_notificacao_chamado(ticket, None, EVENTO_COMMENT, preview)
    except Exception:
        pass


def _texto_repetido(novo: str, anteriores: list[str], limiar: float = 0.88) -> bool:
    """True se o texto for igual, contido ou muito parecido com algum anterior."""
    from difflib import SequenceMatcher

    norm_novo = re.sub(r'\s+', ' ', (novo or '').lower()).strip()
    if not norm_novo:
        return False
    for antigo in anteriores:
        norm_antigo = re.sub(r'\s+', ' ', (antigo or '').lower()).strip()
        if not norm_antigo:
            continue
        if norm_novo == norm_antigo:
            return True
        if len(norm_novo) > 40 and norm_novo in norm_antigo:
            return True
        if len(norm_antigo) > 40 and norm_antigo in norm_novo:
            return True
        # Variações mínimas de redação também contam como repetição
        if min(len(norm_novo), len(norm_antigo)) > 60:
            if SequenceMatcher(None, norm_novo, norm_antigo).ratio() >= limiar:
                return True
    return False


def send_assistente_message(
    ticket_id: int,
    text: str,
    *,
    interno: bool = False,
    followup_mencao: bool = False,
    permitir_repeticao: bool = False,
) -> dict:
    interno = bool(interno)
    followup_mencao = bool(followup_mencao)
    # Interna à TI: não aplicar limpeza agressiva (pode ser orientação técnica)
    if interno:
        texto = (text or '').strip()
    else:
        texto = limpar_texto_para_solicitante(text)
        # Pública deve ser breve — corta em ~2 frases / 280 chars
        if len(texto) > 280:
            corte = texto[:280]
            ponto = max(corte.rfind('.'), corte.rfind('!'), corte.rfind('?'))
            texto = (corte[: ponto + 1] if ponto >= 80 else corte).strip()
    if not texto:
        raise AssistenteServiceError(
            'Texto do comentário é obrigatório'
            + (
                '.'
                if interno
                else ' (após remover raciocínio interno não restou mensagem ao solicitante).'
            )
        )
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    # Anti-repetição: vale para os dois canais, comparando dentro do mesmo canal
    if not permitir_repeticao:
        recentes = list(
            Comment.objects.filter(
                ticket=ticket,
                is_active=True,
                is_assistente=True,
                is_interno=interno,
            )
            .order_by('-created_at')
            .values_list('text', flat=True)[:8]
        )
        if _texto_repetido(texto, recentes):
            raise AssistenteServiceError(
                'Mensagem praticamente igual a uma que você já enviou neste chamado. '
                'Não repita nem peça de novo algo já informado: use o que já está no '
                'histórico e avance no atendimento (ou envie apenas o que mudou).'
            )

    pedacos = _partir_texto_assistente(texto)
    # Mensagem interna: uma bolha só (orientação à TI)
    if interno and len(pedacos) > 1:
        pedacos = ['\n\n'.join(pedacos)]

    comment_ids: list[int] = []
    comments = []
    for pedaco in pedacos:
        comment = Comment.objects.create(
            ticket=ticket,
            author=None,
            text=pedaco,
            is_assistente=True,
            is_interno=interno,
        )
        comment_ids.append(comment.pk)
        comments.append(comment)

    ticket.updated_at = timezone.now()
    ticket.save(update_fields=['updated_at'])

    mencionados = []
    try:
        from helpdesk.mentions import processar_mencoes_assistente
        for comment in comments:
            mencionados.extend(processar_mencoes_assistente(ticket, comment))
    except Exception:
        logger.exception('Falha ao processar menções do Assistente no ticket %s', ticket_id)

    vistos_mencao = set()
    mencionados_unicos = []
    for u in mencionados:
        if u.pk not in vistos_mencao:
            vistos_mencao.add(u.pk)
            mencionados_unicos.append(u)
    mencionados = mencionados_unicos

    # Pública: notifica solicitante; interna: só badge para TI (sem push ao solicitante)
    if not interno:
        # Cobrança @ (5min): o follow-up notifica com menções (evita log duplicado)
        if not followup_mencao:
            _notificar_comentario_assistente(ticket, pedacos[0] if pedacos else texto)
            try:
                from helpdesk.assistente_followup import marcar_espera_assistente
                marcar_espera_assistente(ticket)
            except Exception:
                pass
    else:
        try:
            from helpdesk.audit import log_comentario
            log_comentario(
                ticket,
                None,
                (pedacos[0] if pedacos else texto)[:120],
                metadata={'is_assistente': True, 'is_interno': True},
            )
        except Exception:
            pass
        try:
            from helpdesk.views.kanban import adicionar_nao_lido_operadores
            adicionar_nao_lido_operadores(ticket, None)
        except Exception:
            pass

    if mencionados:
        preview = (pedacos[0] if pedacos else texto)[:120]
        try:
            from helpdesk.views.kanban import adicionar_nao_lido
            adicionar_nao_lido(ticket, None, usuarios_extra=mencionados)
        except Exception:
            pass
        try:
            from helpdesk.notifications import agendar_notificacao_mencoes
            prefixo = '[Interno] ' if interno else ''
            agendar_notificacao_mencoes(ticket, mencionados, f'{prefixo}{preview}')
        except Exception:
            pass

    return {
        'ok': True,
        'comment_id': comment_ids[0] if comment_ids else None,
        'comment_ids': comment_ids,
        'ticket_id': ticket.pk,
        'text': pedacos[0] if len(pedacos) == 1 else '\n\n'.join(pedacos),
        'bolhas': len(pedacos),
        'is_interno': interno,
        'followup_mencao': followup_mencao,
        'mencionados': [u.username for u in mencionados],
    }


_MAX_OPCOES_QUESTIONARIO = 6
_MIN_OPCOES_QUESTIONARIO = 2
_MAX_CHARS_ESCLARECIMENTO = 1200
_IDS_OPCAO = ('a', 'b', 'c', 'd', 'e', 'f')


def _normalizar_opcoes_questionario(opcoes) -> list[dict]:
    """Valida e normaliza lista de opções (2–6) com id estável."""
    if not isinstance(opcoes, (list, tuple)):
        raise AssistenteServiceError('Informe uma lista de opções.')
    limpas: list[str] = []
    for item in opcoes:
        if isinstance(item, dict):
            label = str(item.get('label') or item.get('texto') or '').strip()
        else:
            label = str(item or '').strip()
        if label:
            limpas.append(label[:200])
    if len(limpas) < _MIN_OPCOES_QUESTIONARIO:
        raise AssistenteServiceError(
            f'Informe pelo menos {_MIN_OPCOES_QUESTIONARIO} opções.'
        )
    if len(limpas) > _MAX_OPCOES_QUESTIONARIO:
        limpas = limpas[:_MAX_OPCOES_QUESTIONARIO]
    return [
        {'id': _IDS_OPCAO[i], 'label': label}
        for i, label in enumerate(limpas)
    ]


def _fechar_questionarios_abertos(ticket: Ticket) -> None:
    """Marca questionários públicos abertos como expirados (só um ativo)."""
    abertos = Comment.objects.filter(
        ticket=ticket,
        is_active=True,
        is_assistente=True,
        is_interno=False,
        structured_payload__type='questionario',
        structured_payload__status='aberto',
    )
    for c in abertos:
        payload = dict(c.structured_payload or {})
        payload['status'] = 'expirado'
        c.structured_payload = payload
        c.save(update_fields=['structured_payload'])


def enviar_pergunta_opcoes(
    ticket_id: int,
    pergunta: str,
    opcoes,
    *,
    contexto_curto: str = '',
) -> dict:
    """
    Envia pergunta pública com opções clicáveis (questionário).
    O solicitante/criador escolhe na UI; a escolha vira comentário canônico.
    """
    pergunta = limpar_texto_para_solicitante(pergunta)
    if not pergunta:
        raise AssistenteServiceError('Informe a pergunta do questionário.')
    if len(pergunta) > 500:
        pergunta = pergunta[:500].rstrip()

    opcoes_norm = _normalizar_opcoes_questionario(opcoes)
    contexto = limpar_texto_para_solicitante(contexto_curto or '')
    if len(contexto) > 280:
        contexto = contexto[:280].rstrip()

    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    _fechar_questionarios_abertos(ticket)

    linhas_opcoes = [
        f"{o['id'].upper()}) {o['label']}" for o in opcoes_norm
    ]
    texto = pergunta
    if contexto:
        texto = f'{contexto}\n\n{pergunta}'
    texto = f"{texto}\n\n" + '\n'.join(linhas_opcoes)

    payload = {
        'type': 'questionario',
        'pergunta': pergunta,
        'contexto_curto': contexto,
        'opcoes': opcoes_norm,
        'status': 'aberto',
        'escolhida_id': None,
        'respondido_em': None,
        'respondido_por_id': None,
    }

    comment = Comment.objects.create(
        ticket=ticket,
        author=None,
        text=texto,
        is_assistente=True,
        is_interno=False,
        structured_payload=payload,
    )
    ticket.updated_at = timezone.now()
    ticket.save(update_fields=['updated_at'])

    try:
        _notificar_comentario_assistente(ticket, pergunta)
        from helpdesk.assistente_followup import marcar_espera_assistente
        marcar_espera_assistente(ticket)
    except Exception:
        logger.exception(
            'Falha ao notificar questionário do Assistente no ticket %s',
            ticket_id,
        )

    return {
        'ok': True,
        'comment_id': comment.pk,
        'ticket_id': ticket.pk,
        'type': 'questionario',
        'status': 'aberto',
        'opcoes': opcoes_norm,
        'text': texto,
    }


def enviar_esclarecimento(
    ticket_id: int,
    texto: str,
    *,
    lacunas: list | None = None,
) -> dict:
    """
    Envia bloco público de esclarecimento (limite maior que mensagem breve).
    Use quando faltar informação ou o relato estiver vago.
    """
    texto_limpo = limpar_texto_para_solicitante(texto)
    if not texto_limpo:
        raise AssistenteServiceError('Informe o texto de esclarecimento.')
    if len(texto_limpo) > _MAX_CHARS_ESCLARECIMENTO:
        texto_limpo = texto_limpo[:_MAX_CHARS_ESCLARECIMENTO].rstrip()

    lacunas_norm: list[str] = []
    if lacunas:
        for item in lacunas:
            s = str(item or '').strip()
            if s:
                lacunas_norm.append(s[:200])
        lacunas_norm = lacunas_norm[:8]

    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    # Fecha esclarecimentos anteriores abertos
    abertos = Comment.objects.filter(
        ticket=ticket,
        is_active=True,
        is_assistente=True,
        is_interno=False,
        structured_payload__type='esclarecimento',
        structured_payload__status='aberto',
    )
    for c in abertos:
        payload_ant = dict(c.structured_payload or {})
        payload_ant['status'] = 'expirado'
        c.structured_payload = payload_ant
        c.save(update_fields=['structured_payload'])

    corpo = texto_limpo
    if lacunas_norm:
        bullets = '\n'.join(f'- {l}' for l in lacunas_norm)
        corpo = f'{texto_limpo}\n\n**O que preciso saber:**\n{bullets}'

    payload = {
        'type': 'esclarecimento',
        'texto': texto_limpo,
        'lacunas': lacunas_norm,
        'status': 'aberto',
    }

    comment = Comment.objects.create(
        ticket=ticket,
        author=None,
        text=corpo,
        is_assistente=True,
        is_interno=False,
        structured_payload=payload,
    )
    ticket.updated_at = timezone.now()
    ticket.save(update_fields=['updated_at'])

    try:
        _notificar_comentario_assistente(ticket, texto_limpo[:120])
        from helpdesk.assistente_followup import marcar_espera_assistente
        marcar_espera_assistente(ticket)
    except Exception:
        logger.exception(
            'Falha ao notificar esclarecimento do Assistente no ticket %s',
            ticket_id,
        )

    return {
        'ok': True,
        'comment_id': comment.pk,
        'ticket_id': ticket.pk,
        'type': 'esclarecimento',
        'status': 'aberto',
        'text': corpo,
        'chars': len(corpo),
    }


def responder_opcao_questionario(
    ticket: Ticket,
    comment: Comment,
    opcao_id: str,
    user,
) -> dict:
    """
    Registra a escolha do usuário em um questionário aberto e cria comentário canônico.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        raise AssistenteServiceError('Usuário não autenticado.', 403)
    if comment.ticket_id != ticket.pk:
        raise AssistenteServiceError('Comentário não pertence a este chamado.', 404)
    if not comment.is_assistente or comment.is_interno:
        raise AssistenteServiceError('Este comentário não é um questionário público.')

    payload = dict(comment.structured_payload or {})
    if payload.get('type') != 'questionario':
        raise AssistenteServiceError('Este comentário não é um questionário.')
    if payload.get('status') != 'aberto':
        raise AssistenteServiceError('Este questionário já foi respondido ou expirou.')

    opcao_id = (opcao_id or '').strip().lower()
    opcoes = payload.get('opcoes') or []
    escolhida = next((o for o in opcoes if str(o.get('id', '')).lower() == opcao_id), None)
    if not escolhida:
        raise AssistenteServiceError('Opção inválida para este questionário.')

    agora = timezone.now()
    payload['status'] = 'respondido'
    payload['escolhida_id'] = escolhida['id']
    payload['respondido_em'] = agora.isoformat()
    payload['respondido_por_id'] = user.pk
    comment.structured_payload = payload
    comment.save(update_fields=['structured_payload'])

    label = escolhida.get('label') or ''
    oid = str(escolhida['id']).upper()
    texto_user = f'Opção selecionada: {oid} — {label}'

    resposta = Comment.objects.create(
        ticket=ticket,
        author=user,
        text=texto_user,
        is_assistente=False,
        is_interno=False,
        structured_payload={
            'type': 'resposta_questionario',
            'questionario_comment_id': comment.pk,
            'escolhida_id': escolhida['id'],
            'label': label,
        },
    )
    ticket.updated_at = agora
    ticket.save(update_fields=['updated_at'])

    try:
        from helpdesk.assistente_followup import (
            limpar_espera_assistente,
            usuario_e_solicitante_ou_criador,
        )
        if usuario_e_solicitante_ou_criador(ticket, user):
            limpar_espera_assistente(ticket)
    except Exception:
        pass

    return {
        'ok': True,
        'questionario_comment_id': comment.pk,
        'resposta_comment_id': resposta.pk,
        'escolhida_id': escolhida['id'],
        'label': label,
        'text': texto_user,
    }


def ultimo_esclarecimento_ou_questionario_aberto(ticket: Ticket) -> Comment | None:
    """Último bloco aberto (questionário ou esclarecimento) para o painel do drawer."""
    candidatos = (
        Comment.objects.filter(
            ticket=ticket,
            is_active=True,
            is_assistente=True,
            is_interno=False,
        )
        .exclude(structured_payload=None)
        .order_by('-created_at')[:30]
    )
    for c in candidatos:
        payload = c.structured_payload or {}
        tipo = payload.get('type')
        status = payload.get('status', 'aberto')
        if tipo in ('questionario', 'esclarecimento') and status == 'aberto':
            return c
    return None


def set_ticket_priority(ticket_id: int, priority: str) -> dict:
    priority = (priority or '').strip().upper()
    if priority not in PRIORIDADES:
        raise AssistenteServiceError(f'Prioridade inválida. Use: {", ".join(sorted(PRIORIDADES))}.')
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)
    antes = ticket.priority
    ticket.priority = priority
    ticket.save(update_fields=['priority', 'updated_at'])
    try:
        from helpdesk.audit import log_prioridade_alterada
        log_prioridade_alterada(ticket, None, antes, priority)
    except Exception:
        pass
    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'priority_antes': antes,
        'priority': ticket.priority,
    }


def set_ticket_status(ticket_id: int, status: str, *, via_assistente: bool = False) -> dict:
    status = (status or '').strip().upper()
    if status not in STATUS_VALIDOS:
        raise AssistenteServiceError(f'Status inválido. Use: {", ".join(sorted(STATUS_VALIDOS))}.')
    # Assistente não usa Pendente — só TI após Em Atendimento
    if via_assistente and status == Ticket.StatusChoices.PENDING:
        raise AssistenteServiceError(
            'O Assistente não pode mover para Pendente. '
            'Use IN_PROGRESS, RESOLVED ou escalar_para_ti.'
        )
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)
    antes = ticket.status
    ticket.status = status
    update_fields = ['status', 'updated_at']
    if status == Ticket.StatusChoices.RESOLVED and not ticket.resolved_at:
        ticket.resolved_at = timezone.now()
        update_fields.append('resolved_at')
    if status != Ticket.StatusChoices.RESOLVED:
        if ticket.is_rejected:
            ticket.is_rejected = False
            update_fields.append('is_rejected')
        if ticket.rejection_reason:
            ticket.rejection_reason = ''
            update_fields.append('rejection_reason')
        if antes == Ticket.StatusChoices.RESOLVED:
            ticket.resolved_at = None
            ticket.resolved_by = None
            ticket.assistente_escalado = False
            update_fields.extend(['resolved_at', 'resolved_by', 'assistente_escalado'])
    ticket.save(update_fields=list(dict.fromkeys(update_fields)))
    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'status_antes': antes,
        'status': ticket.status,
    }


def escalar_para_ti(ticket_id: int, motivo: str = '') -> dict:
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)
    ticket.assistente_escalado = True
    ticket.assistente_aguardando_desde = None
    ticket.assistente_followup_mencao_em = None
    update_fields = [
        'assistente_escalado',
        'assistente_aguardando_desde',
        'assistente_followup_mencao_em',
        'updated_at',
    ]
    # Não move para PENDING — só TI coloca Pendente a partir de Em Atendimento
    ticket.save(update_fields=update_fields)

    motivo_limpo = (motivo or '').strip()
    # Mensagem pública breve (máx. 2 frases)
    texto_publico = (
        'Encaminhei este chamado para a equipe de TI analisar. '
        'Um técnico assumirá o atendimento em breve.'
    )
    # Evita duas bolhas públicas seguidas dizendo a mesma coisa ao solicitante
    ultima_publica = (
        Comment.objects.filter(
            ticket=ticket, is_active=True, is_assistente=True, is_interno=False,
        )
        .order_by('-created_at')
        .first()
    )
    reaproveita = bool(
        ultima_publica
        and (timezone.now() - ultima_publica.created_at) < timedelta(minutes=5)
        and not Comment.objects.filter(
            ticket=ticket,
            is_active=True,
            is_assistente=False,
            is_interno=False,
            created_at__gt=ultima_publica.created_at,
        ).exists()
    )
    if reaproveita:
        comment = ultima_publica
    else:
        comment = Comment.objects.create(
            ticket=ticket,
            author=None,
            text=texto_publico,
            is_assistente=True,
            is_interno=False,
        )
        _notificar_comentario_assistente(ticket, texto_publico)

    comment_interno_id = None
    if motivo_limpo:
        texto_interno = (
            f'[ESCALONAMENTO] Diagnóstico e próximos passos para a TI:\n{motivo_limpo}'
        )
        c_int = Comment.objects.create(
            ticket=ticket,
            author=None,
            text=texto_interno,
            is_assistente=True,
            is_interno=True,
        )
        comment_interno_id = c_int.pk
        try:
            from helpdesk.views.kanban import adicionar_nao_lido_operadores
            adicionar_nao_lido_operadores(ticket, None)
        except Exception:
            pass

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'assistente_escalado': True,
        'status': ticket.status,
        'comment_id': comment.pk,
        'comment_interno_id': comment_interno_id,
    }


def listar_categorias_especificas() -> dict:
    cats = list(
        TicketSpecificCategory.objects.filter(is_active=True)
        .order_by('name')
        .values('id', 'name')
    )
    return {'ok': True, 'count': len(cats), 'results': list(cats)}


def triar_chamado(
    ticket_id: int,
    priority: str,
    specific_category_id: int | None = None,
) -> dict:
    """Define prioridade e categoria específica (triagem), sem forçar mudança de coluna."""
    priority = (priority or '').strip().upper()
    if priority not in PRIORIDADES:
        raise AssistenteServiceError(f'Prioridade inválida. Use: {", ".join(sorted(PRIORIDADES))}.')

    ticket = Ticket.objects.select_related('specific_category').filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)
    if ticket.status == Ticket.StatusChoices.RESOLVED:
        raise AssistenteServiceError('Não é possível triar chamado resolvido.')

    cat_id = specific_category_id
    if cat_id is not None and cat_id != '':
        try:
            cat_id = int(cat_id)
        except (TypeError, ValueError):
            raise AssistenteServiceError('specific_category_id inválido.')
        if not TicketSpecificCategory.objects.filter(pk=cat_id, is_active=True).exists():
            raise AssistenteServiceError('Categoria específica não encontrada ou inativa.', 404)
    else:
        cat_id = None

    prioridade_antes = ticket.priority
    cat_antes = ticket.specific_category
    ticket.priority = priority
    ticket.specific_category_id = cat_id
    ticket.save(update_fields=['priority', 'specific_category', 'updated_at'])
    ticket.refresh_from_db()

    try:
        from helpdesk.audit import log_prioridade_alterada, log_triagem_alterada
        if prioridade_antes != priority:
            log_prioridade_alterada(ticket, None, prioridade_antes, priority)
        if (cat_antes.pk if cat_antes else None) != ticket.specific_category_id:
            log_triagem_alterada(ticket, None, cat_antes, ticket.specific_category)
    except Exception:
        pass

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'priority': ticket.priority,
        'specific_category_id': ticket.specific_category_id,
        'specific_category': ticket.specific_category.name if ticket.specific_category_id else None,
        'status': ticket.status,
    }


def recusar_chamado(ticket_id: int, motivo: str) -> dict:
    """Recusa o chamado (título/descrição incorretos etc.) e encerra o Assistente."""
    motivo_limpo = (motivo or '').strip()
    if not motivo_limpo:
        raise AssistenteServiceError('Motivo da recusa é obrigatório.')

    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)
    if ticket.status == Ticket.StatusChoices.RESOLVED and ticket.is_rejected:
        raise AssistenteServiceError('Chamado já está recusado.')

    ticket.status = Ticket.StatusChoices.RESOLVED
    ticket.is_rejected = True
    ticket.rejection_reason = motivo_limpo
    ticket.assistente_escalado = True
    ticket.assistente_aguardando_desde = None
    ticket.assistente_followup_mencao_em = None
    if not ticket.resolved_at:
        ticket.resolved_at = timezone.now()
    ticket.save(update_fields=[
        'status', 'is_rejected', 'rejection_reason', 'assistente_escalado',
        'assistente_aguardando_desde', 'assistente_followup_mencao_em',
        'resolved_at', 'updated_at',
    ])

    if motivo_limpo.lower() == 'sem resposta':
        texto = (
            'Chamado encerrado por falta de resposta.\n'
            'Motivo: Sem resposta.\n\n'
            'Se o problema continuar, abra um novo chamado e responda às perguntas '
            'do Assistente para podermos ajudar.'
        )
    else:
        texto = (
            f'Chamado recusado.\nMotivo: {motivo_limpo}\n\n'
            'Por favor, abra um novo chamado com título e descrição que correspondam '
            'ao problema real.'
        )

    comment = Comment.objects.create(
        ticket=ticket,
        author=None,
        text=texto,
        is_assistente=True,
    )
    _notificar_comentario_assistente(ticket, texto)
    try:
        from helpdesk.audit import log_chamado_recusado
        log_chamado_recusado(ticket, None, motivo_limpo)
    except Exception:
        pass

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'is_rejected': True,
        'status': ticket.status,
        'comment_id': comment.pk,
        'motivo': motivo_limpo,
    }


def limpar_recusa_chamado(ticket_id: int) -> dict:
    """Remove recusa (badge/motivo) e reabre se ainda estiver Resolvido."""
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    estava_recusado = bool(ticket.is_rejected or (ticket.rejection_reason or '').strip())
    status_antes = ticket.status
    motivo_antes = ticket.rejection_reason or ''

    ticket.is_rejected = False
    ticket.rejection_reason = ''
    ticket.assistente_escalado = False
    ticket.assistente_aguardando_desde = None
    ticket.assistente_followup_mencao_em = None
    campos = [
        'is_rejected',
        'rejection_reason',
        'assistente_escalado',
        'assistente_aguardando_desde',
        'assistente_followup_mencao_em',
        'updated_at',
    ]
    if ticket.status == Ticket.StatusChoices.RESOLVED:
        ticket.status = Ticket.StatusChoices.IN_PROGRESS
        campos.append('status')
        ticket.resolved_at = None
        ticket.resolved_by = None
        campos.extend(['resolved_at', 'resolved_by'])
    ticket.save(update_fields=list(dict.fromkeys(campos)))

    send_assistente_message(
        ticket_id,
        'Recusa removida. Chamado segue em atendimento.',
        interno=True,
        permitir_repeticao=True,
    )
    if estava_recusado:
        try:
            send_assistente_message(
                ticket_id,
                'Este chamado continua em atendimento. Desconsidere o encerramento anterior.',
                interno=False,
                permitir_repeticao=True,
            )
        except AssistenteServiceError:
            pass
    try:
        from helpdesk.audit import log_edicao
        log_edicao(
            ticket,
            None,
            {
                'is_rejected': {'antes': estava_recusado, 'depois': False},
                'rejection_reason': {'antes': motivo_antes[:200], 'depois': ''},
                'status': {'antes': status_antes, 'depois': ticket.status},
            },
            'Recusa removida pelo Assistente.',
        )
    except Exception:
        pass

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'is_rejected': False,
        'status': ticket.status,
        'status_antes': status_antes,
        'estava_recusado': estava_recusado,
    }


def _mime_e_ext(nome: str, content_type: str | None = None) -> tuple[str, str]:
    ext = os.path.splitext(nome or '')[1].lower()
    mime = (content_type or '').split(';')[0].strip().lower()
    # Mapeia extensão → mime (Windows às vezes não conhece webp)
    por_ext = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
        '.bmp': 'image/bmp',
        '.pdf': 'application/pdf',
    }
    if ext in por_ext:
        mime = por_ext[ext]
    elif not mime or mime == 'application/octet-stream':
        guessed, _ = mimetypes.guess_type(nome or '')
        mime = (guessed or 'application/octet-stream').split(';')[0].strip().lower()
    return mime, ext


def _eh_imagem(ext: str, mime: str) -> bool:
    return ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'] or (
        mime or ''
    ).startswith('image/')


def _eh_pdf(ext: str, mime: str) -> bool:
    return ext == '.pdf' or (mime or '') == 'application/pdf'


def _normalizar_imagem_para_visao(raw: bytes, mime: str, ext: str) -> tuple[bytes, str]:
    """
    Converte qualquer formato (webp/png/gif/bmp) para JPEG RGB e reduz se grande.
    APIs de visão costumam falhar com webp ou mime octet-stream.
    """
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        if getattr(img, 'n_frames', 1) > 1:
            img.seek(0)
        img = img.convert('RGB')
        # Limita dimensão para caber no payload e acelerar OCR
        max_lado = 1600
        w, h = img.size
        if max(w, h) > max_lado:
            img.thumbnail((max_lado, max_lado), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85, optimize=True)
        return buf.getvalue(), 'image/jpeg'
    except Exception as exc:
        # Se Pillow falhar e já for jpeg/png reconhecido, devolve original
        if mime in ('image/jpeg', 'image/png') and raw:
            return raw, mime
        raise AssistenteServiceError(
            f'Não foi possível processar a imagem ({ext or mime}): {exc}'
        ) from exc


def listar_anexos_ticket(ticket_id: int) -> dict:
    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    resultados: list[dict[str, Any]] = []
    for att in ticket.attachments.all().order_by('created_at'):
        mime, ext = _mime_e_ext(att.file_name or att.file.name)
        resultados.append({
            'ref': f'ticket:{att.pk}',
            'origem': 'ticket',
            'id': att.pk,
            'nome': att.file_name or os.path.basename(att.file.name),
            'ext': ext,
            'mime': mime,
            'is_image': _eh_imagem(ext, mime),
            'is_pdf': _eh_pdf(ext, mime),
            'url': att.file.url if att.file else None,
        })

    for c in (
        Comment.objects.filter(ticket=ticket, is_active=True)
        .exclude(attachment='')
        .exclude(attachment=None)
        .order_by('created_at')
    ):
        if not c.attachment:
            continue
        nome = os.path.basename(c.attachment.name)
        mime, ext = _mime_e_ext(nome)
        resultados.append({
            'ref': f'comment:{c.pk}',
            'origem': 'comment',
            'id': c.pk,
            'nome': nome,
            'ext': ext,
            'mime': mime,
            'is_image': c.is_image if hasattr(c, 'is_image') else _eh_imagem(ext, mime),
            'is_pdf': _eh_pdf(ext, mime),
            'url': c.attachment.url,
            'comment_id': c.pk,
        })

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'count': len(resultados),
        'results': resultados,
    }


def _resolver_anexo(ticket_id: int, attachment_ref: str):
    """Retorna (file_field, nome, mime) ou levanta AssistenteServiceError."""
    ref = (attachment_ref or '').strip()
    if not ref:
        raise AssistenteServiceError('Informe attachment_ref (ex.: ticket:12 ou comment:34).')

    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    if ref.startswith('ticket:'):
        try:
            pk = int(ref.split(':', 1)[1])
        except ValueError:
            raise AssistenteServiceError('attachment_ref inválido.')
        att = TicketAttachment.objects.filter(pk=pk, ticket_id=ticket_id).first()
        if not att or not att.file:
            raise AssistenteServiceError('Anexo do ticket não encontrado.', 404)
        nome = att.file_name or os.path.basename(att.file.name)
        mime, ext = _mime_e_ext(nome)
        return att.file, nome, mime, ext

    if ref.startswith('comment:'):
        try:
            pk = int(ref.split(':', 1)[1])
        except ValueError:
            raise AssistenteServiceError('attachment_ref inválido.')
        comment = Comment.objects.filter(pk=pk, ticket_id=ticket_id, is_active=True).first()
        if not comment or not comment.attachment:
            raise AssistenteServiceError('Anexo do comentário não encontrado.', 404)
        nome = os.path.basename(comment.attachment.name)
        mime, ext = _mime_e_ext(nome)
        return comment.attachment, nome, mime, ext

    # Compat: só número → tenta ticket attachment
    if ref.isdigit():
        return _resolver_anexo(ticket_id, f'ticket:{ref}')

    raise AssistenteServiceError('attachment_ref deve ser ticket:<id> ou comment:<id>.')


def _ler_bytes_anexo(file_field) -> bytes:
    try:
        file_field.open('rb')
        return file_field.read()
    finally:
        try:
            file_field.close()
        except Exception:
            pass


def descrever_imagem_anexo(ticket_id: int, attachment_ref: str) -> dict:
    """
    Lê imagem: tenta visão multimodal se houver; senão OCR local → texto para DeepSeek.
    """
    from integracoes.llm import LlmError, chat_completion_vision, obter_integracao_visao
    from integracoes.texto_local import extrair_texto_imagem_bytes, formatar_resultado_ocr

    file_field, nome, mime, ext = _resolver_anexo(ticket_id, attachment_ref)
    if not _eh_imagem(ext, mime or ''):
        raise AssistenteServiceError('O anexo não é uma imagem.')

    raw = _ler_bytes_anexo(file_field)
    if not raw:
        raise AssistenteServiceError('Arquivo de imagem vazio.')

    # Normaliza para JPEG (visão e OCR)
    raw_jpeg, mime_jpeg = _normalizar_imagem_para_visao(raw, mime or '', ext or '')
    if len(raw_jpeg) > 4 * 1024 * 1024:
        raise AssistenteServiceError(
            'Imagem ainda grande demais após compressão. Peça um print menor.'
        )

    metodo = 'ocr_local'
    descricao = ''
    integracao_visao = obter_integracao_visao()
    if integracao_visao:
        prompt = (
            'Descreva em português, de forma objetiva e útil para suporte de TI, o que aparece nesta imagem. '
            'Inclua textos visíveis (OCR), URLs, logins, nomes de campanha, ramais, erros e menus. '
            'Identifique o sistema com clareza quando der: '
            'MoneyConsig (sistema.moneypromotora.com.br, rankings, abas loja/presença) '
            'OU Discador JoyTec (ramal web, campanhas, disponibilidade, JOYTEC no nome da campanha/login). '
            'Se NÃO for possível afirmar qual sistema é, diga explicitamente: '
            '"sistema não identificado com segurança no print". '
            'Não invente MoneyConsig se a tela parecer discador/telefonia.'
        )
        try:
            descricao = chat_completion_vision(prompt, raw_jpeg, mime_jpeg or 'image/jpeg')
            metodo = f'visao:{integracao_visao.provider}'
        except LlmError as exc:
            logger.warning(
                'Visão falhou (%s); caindo para OCR local. Motivo: %s',
                integracao_visao.provider,
                exc,
            )

    if not descricao:
        try:
            texto = extrair_texto_imagem_bytes(raw_jpeg)
            descricao = formatar_resultado_ocr(texto, origem='imagem')
            metodo = 'ocr_local'
        except Exception as exc:
            logger.exception('OCR local falhou para anexo %s', attachment_ref)
            raise AssistenteServiceError(
                f'Não foi possível ler a imagem (OCR local): {exc}. '
                'Continue com título, descrição e categoria do chamado; '
                'não peça ao solicitante descrever o print se o texto já for suficiente.'
            ) from exc

    return {
        'ok': True,
        'ticket_id': ticket_id,
        'ref': attachment_ref,
        'nome': nome,
        'descricao': descricao,
        'metodo': metodo,
    }


def extrair_texto_pdf_anexo(ticket_id: int, attachment_ref: str) -> dict:
    """Extrai texto de PDF (nativo ou OCR local) para enviar ao LLM só-texto."""
    from integracoes.texto_local import extrair_texto_pdf_bytes, formatar_resultado_ocr

    file_field, nome, mime, ext = _resolver_anexo(ticket_id, attachment_ref)
    if not _eh_pdf(ext, mime or ''):
        raise AssistenteServiceError('O anexo não é um PDF.')

    raw = _ler_bytes_anexo(file_field)
    if not raw:
        raise AssistenteServiceError('Arquivo PDF vazio.')
    if len(raw) > 20 * 1024 * 1024:
        raise AssistenteServiceError('PDF maior que 20MB.')

    try:
        texto, metodo = extrair_texto_pdf_bytes(raw)
    except Exception as exc:
        logger.exception('Falha ao extrair PDF %s', attachment_ref)
        raise AssistenteServiceError(f'Falha ao ler PDF: {exc}') from exc

    origem = metodo if metodo in ('pdf_texto', 'pdf_ocr') else 'pdf_texto'
    return {
        'ok': True,
        'ticket_id': ticket_id,
        'ref': attachment_ref,
        'nome': nome,
        'descricao': formatar_resultado_ocr(texto, origem=origem),
        'metodo': metodo,
        'tem_texto': bool((texto or '').strip()),
    }


def ler_anexo_como_texto(ticket_id: int, attachment_ref: str) -> dict:
    """Imagem (visão/OCR) ou PDF → texto para o Assistente."""
    file_field, nome, mime, ext = _resolver_anexo(ticket_id, attachment_ref)
    if _eh_imagem(ext, mime or ''):
        return descrever_imagem_anexo(ticket_id, attachment_ref)
    if _eh_pdf(ext, mime or ''):
        return extrair_texto_pdf_anexo(ticket_id, attachment_ref)
    raise AssistenteServiceError(
        f'Anexo não suportado para leitura de texto ({ext or mime or nome}). '
        'Aceitos: imagem (jpg/png/webp/gif) ou PDF.'
    )


def _dados_chip_consulta(chip, employee_name=None, ultima_entrega=None, match=''):
    """Serializa chip para o Assistente, incluindo e-mail vinculado se houver."""
    email = getattr(chip, 'email_vinculado', None)
    return {
        'id': chip.pk,
        'line_number': chip.line_number,
        'formatted_line_number': chip.formatted_line_number,
        'status': chip.status,
        'usage_status': chip.usage_status,
        'operator': chip.operator.name if chip.operator_id else None,
        'employee_name': employee_name,
        'ultima_entrega': ultima_entrega,
        'email': email.address if email else None,
        'email_employee_name': email.employee_name if email else None,
        'match': match,
    }


def _titular_movimento_chip(chip):
    """Última entrega/transferência = titular atual (igual ao grid de chips)."""
    from chips.models import ChipMovement

    return (
        ChipMovement.objects.filter(
            chip=chip,
            action__in=[
                ChipMovement.ActionChoices.DELIVERY,
                ChipMovement.ActionChoices.TRANSFER,
            ],
        )
        .order_by('-timestamp')
        .first()
    )


def _normalizar_digitos_linha(valor: str) -> str:
    """Mantém só dígitos; remove DDI 55 se sobrar 12–13 dígitos BR."""
    digits = ''.join(c for c in (valor or '') if c.isdigit())
    if len(digits) in (12, 13) and digits.startswith('55'):
        digits = digits[2:]
    return digits


def consultar_chips(q: str, limit: int = 20) -> dict:
    """Busca chips por linha, observação ou titular atual (entrega/transferência)."""
    from chips.models import Chip, ChipMovement

    termo = (q or '').strip()
    if not termo:
        raise AssistenteServiceError('Informe um termo de busca (nome do consultor ou número).')

    limit = max(1, min(int(limit or 20), 50))
    resultados: list[dict] = []
    visto: set[int] = set()
    digitos = _normalizar_digitos_linha(termo)

    # 1) Titular atual: última DELIVERY/TRANSFER cujo nome contém o termo
    movs = (
        ChipMovement.objects.filter(
            action__in=[
                ChipMovement.ActionChoices.DELIVERY,
                ChipMovement.ActionChoices.TRANSFER,
            ],
            employee_name__icontains=termo,
        )
        .select_related(
            'chip',
            'chip__operator',
            'chip__email_vinculado',
            'chip__email_vinculado__domain',
        )
        .order_by('-timestamp')[:120]
    )
    for mov in movs:
        chip = mov.chip
        if chip.pk in visto:
            continue
        # Só conta se este movimento ainda for o titular atual do chip
        titular = _titular_movimento_chip(chip)
        if not titular or titular.pk != mov.pk:
            continue
        visto.add(chip.pk)
        resultados.append(_dados_chip_consulta(
            chip,
            employee_name=titular.employee_name,
            ultima_entrega=titular.timestamp.isoformat() if titular.timestamp else None,
            match='titular_atual',
        ))
        if len(resultados) >= limit:
            break

    # 2) Busca por número (com/sem máscara/DDI), observação ou ICCID
    if len(resultados) < limit:
        filtros = (
            Q(line_number__icontains=termo)
            | Q(observacao__icontains=termo)
            | Q(iccid__icontains=termo)
        )
        if digitos and digitos != termo:
            filtros = filtros | Q(line_number__icontains=digitos)
        # Sufixo curto (últimos 4–8) ajuda quando o usuário manda o número parcial
        if digitos and 4 <= len(digitos) <= 11:
            filtros = filtros | Q(line_number__endswith=digitos[-8:] if len(digitos) >= 8 else digitos)

        qs = (
            Chip.objects.filter(filtros)
            .select_related('operator', 'email_vinculado', 'email_vinculado__domain')
            .order_by('-updated_at')[:limit]
        )
        for chip in qs:
            if chip.pk in visto:
                continue
            visto.add(chip.pk)
            last = _titular_movimento_chip(chip)
            resultados.append(_dados_chip_consulta(
                chip,
                employee_name=last.employee_name if last else None,
                ultima_entrega=(
                    last.timestamp.isoformat() if last and last.timestamp else None
                ),
                match='linha_ou_obs',
            ))
            if len(resultados) >= limit:
                break

    em_uso = [r for r in resultados if r.get('usage_status') == Chip.UsageChoices.IN_USE]
    return {
        'ok': True,
        'q': termo,
        'q_digits': digitos or None,
        'count': len(resultados),
        'em_uso_count': len(em_uso),
        'results': resultados,
        'orientacao': (
            'Liste TODOS os chips em uso do titular na pergunta (não omita). '
            'Se o solicitante informar outro número: consultar_chips com esse número, '
            'compare o employee_name com o nome esperado e questione a inconsistência '
            '(enviar_pergunta_opcoes / enviar_esclarecimento) antes de escalar_para_ti. '
            'Se já tiver 2+ em uso, confirme qual pede código antes de chip novo.'
            if resultados
            else (
                'Nenhum chip encontrado com este termo. Se o solicitante passou um número, '
                'tente consultar_chips só com os dígitos do número; se achar titular diferente '
                'do nome do chamado, questione antes de escalar.'
            )
        ),
    }


def _resolver_chip(*, chip_id=None, line_number: str = ''):
    """Resolve chip por id ou número da linha."""
    from chips.models import Chip

    if chip_id:
        try:
            return Chip.objects.select_related('operator', 'batch').get(pk=int(chip_id))
        except (Chip.DoesNotExist, TypeError, ValueError):
            raise AssistenteServiceError(f'Chip id={chip_id} não encontrado.', status_code=404)

    digits = ''.join(c for c in (line_number or '') if c.isdigit())
    termo = (line_number or '').strip()
    if not termo:
        raise AssistenteServiceError('Informe chip_id ou line_number.')

    chip = (
        Chip.objects.select_related('operator', 'batch')
        .filter(Q(line_number=termo) | Q(line_number=digits))
        .first()
    )
    if not chip:
        raise AssistenteServiceError(f'Chip linha "{termo}" não encontrado.', status_code=404)
    return chip


def atualizar_status_chip(chip_id=None, line_number: str = '', status: str = '') -> dict:
    """Altera status do chip (ACTIVE|BANNED|CANCELED|LOST|OTHER) com auditoria."""
    from chips.audit import log_chip_atualizado
    from chips.models import Chip
    from chips.services import registrar_bloqueio

    status_limpo = (status or '').strip().upper()
    validos = {c.value for c in Chip.StatusChoices}
    if status_limpo not in validos:
        raise AssistenteServiceError(
            f'Status inválido: {status}. Use: {", ".join(sorted(validos))}.'
        )

    chip = _resolver_chip(chip_id=chip_id, line_number=line_number)
    antes = Chip.objects.get(pk=chip.pk)
    status_antes = chip.status

    if status_limpo == Chip.StatusChoices.BANNED and status_antes != Chip.StatusChoices.BANNED:
        registrar_bloqueio(chip, actor=None)
    else:
        chip.status = status_limpo
        if status_antes == Chip.StatusChoices.BANNED and status_limpo != Chip.StatusChoices.BANNED:
            chip.last_blocked_at = None
        chip.save()

    chip.refresh_from_db()
    log_chip_atualizado(chip, None, antes)
    return {
        'ok': True,
        'chip': {
            'id': chip.pk,
            'line_number': chip.line_number,
            'status': chip.status,
            'usage_status': chip.usage_status,
            'observacao': chip.observacao,
        },
    }


def atualizar_observacao_chip(chip_id=None, line_number: str = '', observacao: str = '') -> dict:
    """Atualiza observação operacional do chip."""
    from chips.audit import log_chip_atualizado
    from chips.models import Chip
    from core.audit import registrar_acao
    from core.models import RegistroAcao
    from core.permissions import MODULO_CHIPS

    chip = _resolver_chip(chip_id=chip_id, line_number=line_number)
    antes = Chip.objects.get(pk=chip.pk)
    chip.observacao = observacao if observacao is not None else ''
    chip.save()
    log_chip_atualizado(chip, None, antes)
    registrar_acao(
        modulo=MODULO_CHIPS,
        acao=RegistroAcao.AcaoChoices.UPDATED,
        descricao=f'Observação do chip {chip.line_number} atualizada via Assistente/MCP.',
        actor=None,
        obj=chip,
        metadata={'observacao': (chip.observacao or '')[:500]},
    )
    return {
        'ok': True,
        'chip': {
            'id': chip.pk,
            'line_number': chip.line_number,
            'status': chip.status,
            'observacao': chip.observacao,
        },
    }


def consultar_equipamento(q: str, limit: int = 20) -> dict:
    """Busca patrimônio por tag, serial, modelo ou colaborador."""
    from equipment.models import Equipment
    from mcp_api.serializers import serialize_equipment

    termo = (q or '').strip()
    if not termo:
        raise AssistenteServiceError('Informe tag, serial, modelo ou nome do colaborador.')

    limit = max(1, min(int(limit or 20), 50))
    filtro = (
        Q(tag__icontains=termo)
        | Q(serial_number__icontains=termo)
        | Q(brand_model__icontains=termo)
        | Q(current_employee__icontains=termo)
    )
    if termo.isdigit():
        filtro |= Q(pk=int(termo))
    qs = Equipment.objects.filter(filtro).order_by('-updated_at')[:limit]
    itens = [serialize_equipment(e) for e in qs]
    return {'ok': True, 'q': termo, 'count': len(itens), 'results': itens}


def consultar_email(q: str, limit: int = 20) -> dict:
    """Busca e-mail corporativo por username, domínio ou colaborador."""
    from emails.models import EmailAccount
    from mcp_api.serializers import filtro_q_email, serialize_email_account

    termo = (q or '').strip()
    if not termo:
        raise AssistenteServiceError('Informe username, domínio ou nome do colaborador.')

    limit = max(1, min(int(limit or 20), 50))
    qs = filtro_q_email(
        EmailAccount.objects.select_related('domain').prefetch_related('chips'),
        termo,
    ).order_by('-updated_at')[: max(limit * 3, 40)]
    itens = [serialize_email_account(a) for a in qs]
    tokens = [t.lower() for t in re.split(r'[^\w]+', termo, flags=re.UNICODE) if len(t) >= 3]
    termo_l = termo.lower()

    def _score(item: dict) -> int:
        nome = (item.get('employee_name') or '').lower()
        user = (item.get('username') or '').lower()
        addr = (item.get('address') or '').lower()
        score = 0
        if termo_l in nome or termo_l in user or termo_l in addr:
            score += 20
        for tok in tokens:
            if tok in nome:
                score += 5
            if tok in user:
                score += 4
        return score

    itens.sort(key=_score, reverse=True)
    itens = itens[:limit]
    return {'ok': True, 'q': termo, 'count': len(itens), 'results': itens}


def consultar_usuario(q: str, limit: int = 15) -> dict:
    """Busca usuários CRM por username/nome/e-mail."""
    from core.models import CustomUser
    from mcp_api.serializers import filtro_q_usuario, serialize_user

    termo = (q or '').strip()
    if not termo:
        raise AssistenteServiceError('Informe username ou nome para buscar.')

    limit = max(1, min(int(limit or 15), 40))
    qs = filtro_q_usuario(
        CustomUser.objects.prefetch_related('equipes').order_by('username'),
        termo,
    ).distinct()[:limit]
    itens = []
    for u in qs:
        item = serialize_user(u)
        item['eh_membro_ti'] = usuario_eh_operador_helpdesk(u)
        itens.append(item)
    return {'ok': True, 'q': termo, 'count': len(itens), 'results': itens}


def atualizar_solicitante(
    ticket_id: int,
    user_id: int | None = None,
    nome_livre: str = '',
) -> dict:
    """
    Corrige o solicitante do chamado.
    - user_id: vincula usuário do sistema (tem acesso)
    - nome_livre: nome sem conta (sem requester_user)
    """
    from core.models import CustomUser
    from helpdesk.audit import log_edicao

    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)
    if not assistente_pode_atuar(ticket):
        raise AssistenteServiceError('Assistente não pode alterar este chamado agora.')

    antes_nome = ticket.requester_name
    antes_user_id = ticket.requester_user_id

    if user_id is not None and str(user_id).strip() != '':
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            raise AssistenteServiceError('user_id inválido.')
        user = CustomUser.objects.filter(pk=uid, is_active=True).first()
        if not user:
            raise AssistenteServiceError('Usuário não encontrado ou inativo.', 404)
        if usuario_eh_operador_helpdesk(user):
            raise AssistenteServiceError(
                'Não defina membro da TI como solicitante. Peça o nome de quem sofreu o problema.'
            )
        ticket.requester_user = user
        ticket.requester_name = (user.get_full_name() or user.username)[:150]
        modo = 'usuario_sistema'
    else:
        nome = (nome_livre or '').strip()
        if not nome:
            raise AssistenteServiceError(
                'Informe user_id (usuário do sistema) ou nome_livre.'
            )
        ticket.requester_user = None
        ticket.requester_name = nome[:150]
        modo = 'nome_livre'

    ticket.save(update_fields=['requester_name', 'requester_user', 'updated_at'])
    Comment.objects.create(
        ticket=ticket,
        author=None,
        text=(
            f'Solicitante atualizado pelo Assistente de '
            f'"{antes_nome}" para "{ticket.requester_name}"'
            f'{" (usuário do sistema)" if ticket.requester_user_id else " (nome livre)"}.'
        ),
        is_assistente=True,
    )
    try:
        log_edicao(
            ticket,
            None,
            {
                'requester_name': {'antes': antes_nome, 'depois': ticket.requester_name},
                'requester_user_id': {'antes': antes_user_id, 'depois': ticket.requester_user_id},
            },
            f'Solicitante corrigido pelo Assistente para {ticket.requester_name}.',
        )
    except Exception:
        pass

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'modo': modo,
        'requester_name': ticket.requester_name,
        'requester_user_id': ticket.requester_user_id,
        'antes': {'requester_name': antes_nome, 'requester_user_id': antes_user_id},
    }


def atualizar_descricao_chamado(
    ticket_id: int,
    description: str,
    title: str | None = None,
) -> dict:
    """Melhora título e/ou descrição do chamado (após confirmar o contexto)."""
    from helpdesk.audit import log_edicao

    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)
    if not assistente_pode_atuar(ticket):
        raise AssistenteServiceError('Assistente não pode alterar este chamado agora.')

    desc = (description or '').strip()
    if not desc:
        raise AssistenteServiceError('Informe a nova descrição.')
    if len(desc) > 8000:
        raise AssistenteServiceError('Descrição muito longa (máx. 8000 caracteres).')

    antes_desc = ticket.description
    antes_title = ticket.title
    campos = ['description', 'updated_at']
    ticket.description = desc

    titulo_novo = None
    if title is not None and str(title).strip():
        titulo_novo = str(title).strip()[:200]
        ticket.title = titulo_novo
        campos.append('title')

    ticket.save(update_fields=campos)
    Comment.objects.create(
        ticket=ticket,
        author=None,
        text='Descrição do chamado atualizada pelo Assistente para ficar mais clara.',
        is_assistente=True,
    )
    meta = {'description': {'antes': (antes_desc or '')[:200], 'depois': desc[:200]}}
    if titulo_novo is not None:
        meta['title'] = {'antes': antes_title, 'depois': titulo_novo}
    try:
        log_edicao(ticket, None, meta, 'Descrição/título atualizados pelo Assistente.')
    except Exception:
        pass

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'title': ticket.title,
        'description': ticket.description,
    }


# --- Discador (JoyTec) ---


def _resolver_discador(slug: str = 'joytec'):
    from discador.models import Discador
    from discador.services import get_or_create_joytec

    slug_limpo = (slug or 'joytec').strip().lower() or 'joytec'
    if slug_limpo == 'joytec':
        return get_or_create_joytec()
    discador = Discador.objects.filter(slug=slug_limpo, is_active=True).first()
    if not discador:
        raise AssistenteServiceError(f'Discador "{slug_limpo}" não encontrado.', 404)
    return discador


def _serialize_ramal(ramal) -> dict:
    acesso = None
    try:
        acesso = ramal.acesso
    except Exception:
        acesso = None
    return {
        'id': ramal.pk,
        'numero': ramal.numero,
        'status': ramal.status,
        'status_display': ramal.get_status_display(),
        'consome_licenca': ramal.consome_licenca,
        'tem_acesso': acesso is not None,
        'acesso_id': acesso.pk if acesso else None,
        'titular': (acesso.nome_exibicao if acesso else None),
        'login_discador': (acesso.login_discador if acesso else None),
    }


def _serialize_acesso(acesso) -> dict:
    return {
        'id': acesso.pk,
        'titular_nome': acesso.titular_nome,
        'titular': acesso.nome_exibicao,
        'titular_user_id': acesso.titular_user_id,
        'login_discador': acesso.login_discador,
        'tipo': acesso.tipo,
        'tipo_display': acesso.get_tipo_display(),
        'status': acesso.status,
        'ramal_id': acesso.ramal_id,
        'ramal': acesso.ramal.numero if acesso.ramal_id else None,
        'campanha_id': acesso.campanha_id,
        'campanha': acesso.campanha.nome if acesso.campanha_id else None,
    }


def consultar_licencas_discador(slug: str = 'joytec') -> dict:
    """KPIs de licenças: contratadas, livres (FREE), disponíveis no contrato, etc."""
    from discador.services import kpis_licencas

    discador = _resolver_discador(slug)
    kpis = kpis_licencas(discador)
    return {
        'ok': True,
        'discador': discador.nome,
        'slug': discador.slug,
        'contratadas': kpis['contratadas'],
        'consumidas': kpis['consumidas'],
        'em_uso': kpis['em_uso'],
        'ramais_livres': kpis['livres'],
        'nao_configurados': kpis['nao_configurados'],
        'licencas_disponiveis_contrato': kpis['disponiveis'],
        'estourado': kpis['estourado'],
        'no_limite': kpis['no_limite'],
        'custo_mensal': str(kpis['custo_mensal']),
        'orientacao': (
            'Inventário LOCAL do CRM (não é a JoyTec). '
            'ramais_livres = FREE (ainda consomem licença). '
            'licencas_disponiveis_contrato = slots novos no contrato. '
            'Assistente: só consultar e avisar a TI (mensagem interna) qual FREE usar '
            'ou se precisa comprar mais. Criar/liberar acesso é manual (TI/MCP inventário).'
        ),
    }


def listar_ramais_discador(
    status: str = '',
    slug: str = 'joytec',
    limit: int = 40,
) -> dict:
    """Lista ramais; status opcional: FREE|IN_USE|NOT_CONFIGURED."""
    from discador.models import Ramal

    discador = _resolver_discador(slug)
    limit = max(1, min(int(limit or 40), 80))
    qs = (
        Ramal.objects.filter(discador=discador)
        .select_related('acesso', 'acesso__campanha', 'acesso__titular_user')
        .order_by('numero')
    )
    status_limpo = (status or '').strip().upper()
    if status_limpo:
        validos = {c.value for c in Ramal.StatusChoices}
        if status_limpo not in validos:
            raise AssistenteServiceError(
                f'Status inválido. Use: {", ".join(sorted(validos))}.'
            )
        qs = qs.filter(status=status_limpo)

    itens = [_serialize_ramal(r) for r in qs[:limit]]
    return {
        'ok': True,
        'discador': discador.nome,
        'slug': discador.slug,
        'status': status_limpo or None,
        'count': len(itens),
        'results': itens,
    }


def consultar_acesso_discador(q: str, slug: str = 'joytec', limit: int = 20) -> dict:
    """Busca acessos por titular, login ou número do ramal."""
    from discador.models import AcessoDiscador

    termo = (q or '').strip()
    if not termo:
        raise AssistenteServiceError('Informe nome do titular, login ou ramal.')

    discador = _resolver_discador(slug)
    limit = max(1, min(int(limit or 20), 40))
    qs = (
        AcessoDiscador.objects.filter(discador=discador)
        .select_related('ramal', 'campanha', 'titular_user')
        .filter(
            Q(titular_nome__icontains=termo)
            | Q(login_discador__icontains=termo)
            | Q(ramal__numero__icontains=termo)
            | Q(titular_user__username__icontains=termo)
            | Q(titular_user__first_name__icontains=termo)
            | Q(titular_user__last_name__icontains=termo)
        )
        .order_by('titular_nome', 'login_discador')[:limit]
    )
    itens = [_serialize_acesso(a) for a in qs]
    return {
        'ok': True,
        'discador': discador.nome,
        'q': termo,
        'count': len(itens),
        'results': itens,
    }


def listar_campanhas_discador(slug: str = 'joytec', so_ativas: bool = True) -> dict:
    from discador.models import Campanha

    discador = _resolver_discador(slug)
    qs = Campanha.objects.filter(discador=discador).order_by('nome')
    if so_ativas:
        qs = qs.filter(is_active=True)
    itens = [
        {'id': c.pk, 'nome': c.nome, 'is_active': c.is_active}
        for c in qs[:80]
    ]
    return {
        'ok': True,
        'discador': discador.nome,
        'count': len(itens),
        'results': itens,
    }


def liberar_acesso_discador(acesso_id: int, actor=None) -> dict:
    """Remove o acesso e deixa o ramal em FREE (ainda consome licença)."""
    from django.core.exceptions import ValidationError

    from discador.models import AcessoDiscador
    from discador.services import excluir_acesso

    acesso = (
        AcessoDiscador.objects.select_related('ramal', 'campanha')
        .filter(pk=acesso_id)
        .first()
    )
    if not acesso:
        raise AssistenteServiceError('Acesso não encontrado.', 404)
    ramal_numero = acesso.ramal.numero
    ramal_id = acesso.ramal_id
    titular = acesso.nome_exibicao
    try:
        excluir_acesso(acesso=acesso, actor=actor)
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    return {
        'ok': True,
        'acesso_id': acesso_id,
        'titular': titular,
        'ramal_id': ramal_id,
        'ramal': ramal_numero,
        'status_ramal': 'FREE',
        'orientacao': (
            'Ramal liberado (FREE). Ainda consome licença. '
            'Para liberar slot do contrato use liberar_licenca_ramal.'
        ),
    }


def liberar_licenca_ramal(
    ramal_id: int | None = None,
    ramal_numero: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Marca ramal como NOT_CONFIGURED (deixa de consumir licença). Exige sem acesso."""
    from django.core.exceptions import ValidationError

    from discador.models import Ramal
    from discador.services import atualizar_ramal

    discador = _resolver_discador(slug)
    ramal = _buscar_ramal(discador, ramal_id=ramal_id, ramal_numero=ramal_numero)
    try:
        atualizar_ramal(
            ramal=ramal,
            numero=ramal.numero,
            status=Ramal.StatusChoices.NOT_CONFIGURED,
            actor=actor,
        )
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    ramal.refresh_from_db()
    return {
        'ok': True,
        'ramal_id': ramal.pk,
        'ramal': ramal.numero,
        'status': ramal.status,
        'consome_licenca': ramal.consome_licenca,
    }


def criar_acesso_discador(
    titular_nome: str,
    login_discador: str,
    tipo: str = 'CONSULTOR',
    ramal_id: int | None = None,
    ramal_numero: str = '',
    campanha_id: int | None = None,
    campanha_nome: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Cria acesso em ramal FREE/NOT_CONFIGURED (escolhe FREE se ramal omitido)."""
    from django.core.exceptions import ValidationError

    from discador.models import AcessoDiscador, Campanha, Ramal
    from discador.services import criar_acesso, kpis_licencas

    discador = _resolver_discador(slug)
    titular = (titular_nome or '').strip()
    login = (login_discador or '').strip()
    if not login:
        raise AssistenteServiceError('login_discador é obrigatório.')

    tipo_limpo = (tipo or 'CONSULTOR').strip().upper()
    tipos = {c.value for c in AcessoDiscador.TipoChoices}
    if tipo_limpo not in tipos:
        raise AssistenteServiceError(f'Tipo inválido. Use: {", ".join(sorted(tipos))}.')

    campanha = _buscar_campanha(discador, campanha_id=campanha_id, campanha_nome=campanha_nome)
    if ramal_id or (ramal_numero or '').strip():
        ramal = _buscar_ramal(discador, ramal_id=ramal_id, ramal_numero=ramal_numero)
    else:
        ramal = (
            Ramal.objects.filter(discador=discador, status=Ramal.StatusChoices.FREE)
            .filter(acesso__isnull=True)
            .order_by('numero')
            .first()
        )
        if not ramal:
            # Tenta NOT_CONFIGURED se houver slot no contrato
            kpis = kpis_licencas(discador)
            if kpis['disponiveis'] <= 0:
                raise AssistenteServiceError(
                    'Sem ramais livres e sem licenças disponíveis no contrato. '
                    'Libere um acesso/licença ou peça aumento de contrato à TI.'
                )
            ramal = (
                Ramal.objects.filter(
                    discador=discador,
                    status=Ramal.StatusChoices.NOT_CONFIGURED,
                )
                .order_by('numero')
                .first()
            )
        if not ramal:
            raise AssistenteServiceError(
                'Não há ramal FREE/NOT_CONFIGURED disponível. Cadastre um ramal ou libere um.'
            )

    try:
        acesso = criar_acesso(
            discador=discador,
            titular_nome=titular,
            titular_user=None,
            login_discador=login,
            ramal=ramal,
            campanha=campanha,
            tipo=tipo_limpo,
            actor=actor,
        )
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc

    return {
        'ok': True,
        'acesso': _serialize_acesso(acesso),
        'licencas': consultar_licencas_discador(slug),
    }


def _buscar_ramal(discador, *, ramal_id=None, ramal_numero: str = ''):
    from discador.models import Ramal

    if ramal_id:
        ramal = Ramal.objects.filter(pk=ramal_id, discador=discador).first()
        if not ramal:
            raise AssistenteServiceError('Ramal não encontrado.', 404)
        return ramal
    numero = (ramal_numero or '').strip()
    if not numero:
        raise AssistenteServiceError('Informe ramal_id ou ramal_numero.')
    ramal = Ramal.objects.filter(discador=discador, numero__iexact=numero).first()
    if not ramal:
        raise AssistenteServiceError(f'Ramal "{numero}" não encontrado.', 404)
    return ramal


def _buscar_campanha(discador, *, campanha_id=None, campanha_nome: str = '', so_ativas: bool = True):
    from discador.models import Campanha

    qs = Campanha.objects.filter(discador=discador)
    if so_ativas:
        qs = qs.filter(is_active=True)
    if campanha_id:
        campanha = qs.filter(pk=campanha_id).first()
        if not campanha:
            raise AssistenteServiceError('Campanha não encontrada.', 404)
        return campanha
    nome = (campanha_nome or '').strip()
    if not nome:
        raise AssistenteServiceError('Informe campanha_id ou campanha_nome.')
    campanha = qs.filter(nome__iexact=nome).first()
    if not campanha:
        campanha = qs.filter(nome__icontains=nome).first()
    if not campanha:
        raise AssistenteServiceError(f'Campanha "{nome}" não encontrada.', 404)
    return campanha


def _msg_validacao(exc) -> str:
    if hasattr(exc, 'messages'):
        return '; '.join(str(m) for m in exc.messages)
    return str(exc)


def _status_ramal(texto: str, padrao: str = '') -> str:
    """Aceita IN_USE/FREE/NOT_CONFIGURED ou rótulos em português."""
    from discador.models import Ramal

    bruto = (texto or '').strip().upper()
    mapa = {
        'IN_USE': Ramal.StatusChoices.IN_USE,
        'EM USO': Ramal.StatusChoices.IN_USE,
        'EM_USO': Ramal.StatusChoices.IN_USE,
        'FREE': Ramal.StatusChoices.FREE,
        'LIVRE': Ramal.StatusChoices.FREE,
        'NOT_CONFIGURED': Ramal.StatusChoices.NOT_CONFIGURED,
        'NAO CONFIGURADO': Ramal.StatusChoices.NOT_CONFIGURED,
        'NÃO CONFIGURADO': Ramal.StatusChoices.NOT_CONFIGURED,
        'NAO_CONFIGURADO': Ramal.StatusChoices.NOT_CONFIGURED,
        'INATIVO': Ramal.StatusChoices.NOT_CONFIGURED,
        'INATIVAR': Ramal.StatusChoices.NOT_CONFIGURED,
    }
    if not bruto:
        return padrao
    if bruto in mapa:
        return mapa[bruto]
    validos = {c.value for c in Ramal.StatusChoices}
    if bruto in validos:
        return bruto
    raise AssistenteServiceError(
        f'Status inválido. Use: {", ".join(sorted(validos))}.'
    )


def atualizar_acesso_discador(
    acesso_id: int,
    titular_nome: str | None = None,
    login_discador: str | None = None,
    tipo: str | None = None,
    ramal_id: int | None = None,
    ramal_numero: str = '',
    campanha_id: int | None = None,
    campanha_nome: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Edita acesso JoyTec; campos omitidos permanecem iguais."""
    from django.core.exceptions import ValidationError

    from discador.models import AcessoDiscador
    from discador.services import atualizar_acesso

    acesso = (
        AcessoDiscador.objects.select_related('ramal', 'campanha', 'discador')
        .filter(pk=acesso_id)
        .first()
    )
    if not acesso:
        raise AssistenteServiceError('Acesso não encontrado.', 404)
    discador = acesso.discador
    ramal = acesso.ramal
    if ramal_id or (ramal_numero or '').strip():
        ramal = _buscar_ramal(discador, ramal_id=ramal_id, ramal_numero=ramal_numero)
    campanha = acesso.campanha
    if campanha_id or (campanha_nome or '').strip():
        campanha = _buscar_campanha(
            discador, campanha_id=campanha_id, campanha_nome=campanha_nome, so_ativas=False,
        )
    tipo_limpo = acesso.tipo
    if tipo:
        tipo_limpo = (tipo or '').strip().upper()
        tipos = {c.value for c in AcessoDiscador.TipoChoices}
        if tipo_limpo not in tipos:
            raise AssistenteServiceError(f'Tipo inválido. Use: {", ".join(sorted(tipos))}.')
    nome = acesso.titular_nome if titular_nome is None else (titular_nome or '').strip()
    login = acesso.login_discador if login_discador is None else (login_discador or '').strip()
    if not login:
        raise AssistenteServiceError('login_discador é obrigatório.')
    try:
        acesso = atualizar_acesso(
            acesso=acesso,
            titular_nome=nome,
            titular_user=acesso.titular_user,
            login_discador=login,
            ramal=ramal,
            campanha=campanha,
            tipo=tipo_limpo,
            actor=actor,
        )
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    return {'ok': True, 'acesso': _serialize_acesso(acesso)}


def criar_ramal_discador(
    numero: str,
    status: str = 'NOT_CONFIGURED',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Cadastra ramal. Default Não configurado (não consome licença)."""
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError

    from discador.services import criar_ramal

    discador = _resolver_discador(slug)
    numero_limpo = (numero or '').strip()
    if not numero_limpo:
        raise AssistenteServiceError('numero do ramal é obrigatório.')
    status_limpo = _status_ramal(status, padrao='NOT_CONFIGURED')
    try:
        ramal = criar_ramal(
            discador=discador, numero=numero_limpo, status=status_limpo, actor=actor,
        )
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    except IntegrityError as exc:
        raise AssistenteServiceError(f'Ramal "{numero_limpo}" já existe.') from exc
    return {'ok': True, 'ramal': _serialize_ramal(ramal)}


def atualizar_ramal_discador(
    ramal_id: int | None = None,
    ramal_numero: str = '',
    numero: str = '',
    status: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Edita número/status do ramal. Inativar = status NOT_CONFIGURED."""
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError

    from discador.services import atualizar_ramal

    discador = _resolver_discador(slug)
    ramal = _buscar_ramal(discador, ramal_id=ramal_id, ramal_numero=ramal_numero)
    novo_numero = (numero or '').strip() or ramal.numero
    novo_status = _status_ramal(status, padrao=ramal.status) if status else ramal.status
    try:
        ramal = atualizar_ramal(
            ramal=ramal, numero=novo_numero, status=novo_status, actor=actor,
        )
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    except IntegrityError as exc:
        raise AssistenteServiceError(f'Ramal "{novo_numero}" já existe.') from exc
    return {'ok': True, 'ramal': _serialize_ramal(ramal)}


def excluir_ramal_discador(
    ramal_id: int | None = None,
    ramal_numero: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Remove ramal do cadastro (precisa estar sem acesso)."""
    from django.core.exceptions import ValidationError

    from discador.services import excluir_ramal

    discador = _resolver_discador(slug)
    ramal = _buscar_ramal(discador, ramal_id=ramal_id, ramal_numero=ramal_numero)
    numero = ramal.numero
    pk = ramal.pk
    try:
        excluir_ramal(ramal=ramal, actor=actor)
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    return {'ok': True, 'ramal_id': pk, 'ramal': numero, 'excluido': True}


def criar_campanha_discador(nome: str, slug: str = 'joytec', actor=None) -> dict:
    """Cria campanha ativa."""
    from django.db import IntegrityError

    from discador.services import criar_campanha

    discador = _resolver_discador(slug)
    nome_limpo = (nome or '').strip()
    if not nome_limpo:
        raise AssistenteServiceError('nome da campanha é obrigatório.')
    try:
        campanha = criar_campanha(discador=discador, nome=nome_limpo, is_active=True)
    except IntegrityError as exc:
        raise AssistenteServiceError(f'Campanha "{nome_limpo}" já existe.') from exc
    return {
        'ok': True,
        'campanha': {'id': campanha.pk, 'nome': campanha.nome, 'is_active': campanha.is_active},
    }


def atualizar_campanha_discador(
    campanha_id: int | None = None,
    campanha_nome: str = '',
    nome: str = '',
    is_active=None,
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Edita nome e/ou ativa/inativa a campanha."""
    from django.db import IntegrityError

    from discador.services import atualizar_campanha

    discador = _resolver_discador(slug)
    campanha = _buscar_campanha(
        discador, campanha_id=campanha_id, campanha_nome=campanha_nome, so_ativas=False,
    )
    novo_nome = (nome or '').strip() or campanha.nome
    if is_active is None:
        ativo = campanha.is_active
    else:
        ativo = bool(is_active) if not isinstance(is_active, str) else str(is_active).strip().lower() in (
            '1', 'true', 'sim', 'ativa', 'ativo', 'yes',
        )
    try:
        campanha = atualizar_campanha(campanha=campanha, nome=novo_nome, is_active=ativo)
    except IntegrityError as exc:
        raise AssistenteServiceError(f'Campanha "{novo_nome}" já existe.') from exc
    return {
        'ok': True,
        'campanha': {'id': campanha.pk, 'nome': campanha.nome, 'is_active': campanha.is_active},
    }


def inativar_campanha_discador(
    campanha_id: int | None = None,
    campanha_nome: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Inativa campanha (is_active=False). Preferível a excluir se houver acessos."""
    return atualizar_campanha_discador(
        campanha_id=campanha_id,
        campanha_nome=campanha_nome,
        is_active=False,
        slug=slug,
        actor=actor,
    )


def excluir_campanha_discador(
    campanha_id: int | None = None,
    campanha_nome: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Exclui campanha sem acessos vinculados."""
    from django.core.exceptions import ValidationError

    from discador.services import excluir_campanha

    discador = _resolver_discador(slug)
    campanha = _buscar_campanha(
        discador, campanha_id=campanha_id, campanha_nome=campanha_nome, so_ativas=False,
    )
    nome = campanha.nome
    pk = campanha.pk
    try:
        excluir_campanha(campanha=campanha)
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    return {'ok': True, 'campanha_id': pk, 'nome': nome, 'excluida': True}


def atualizar_contrato_discador(
    licencas_contratadas: int | None = None,
    valor_por_licenca=None,
    observacao: str = '',
    slug: str = 'joytec',
    actor=None,
) -> dict:
    """Altera quantidade e/ou valor do contrato de licenças."""
    from decimal import Decimal, InvalidOperation

    from django.core.exceptions import ValidationError

    from discador.services import atualizar_contrato, kpis_licencas

    discador = _resolver_discador(slug)
    qtd = discador.licencas_contratadas if licencas_contratadas is None else int(licencas_contratadas)
    valor = discador.valor_por_licenca
    if valor_por_licenca is not None and str(valor_por_licenca).strip() != '':
        try:
            valor = Decimal(str(valor_por_licenca).replace(',', '.'))
        except (InvalidOperation, TypeError) as exc:
            raise AssistenteServiceError('valor_por_licenca inválido.') from exc
    try:
        discador = atualizar_contrato(
            discador=discador,
            valor_por_licenca=valor,
            licencas_contratadas=qtd,
            observacao=observacao or 'Alterado pelo wizard de gestão.',
            actor=actor,
        )
    except ValidationError as exc:
        raise AssistenteServiceError(_msg_validacao(exc)) from exc
    kpis = kpis_licencas(discador)
    return {
        'ok': True,
        'discador': discador.nome,
        'licencas_contratadas': discador.licencas_contratadas,
        'valor_por_licenca': str(discador.valor_por_licenca),
        'custo_mensal': str(kpis['custo_mensal']),
        'consumidas': kpis['consumidas'],
        'disponiveis': kpis['disponiveis'],
    }


# ---------------------------------------------------------------------------
# MoneyConsig (API B2B via Integrações → APIs)
# ---------------------------------------------------------------------------


def moneyconsig_auth_me() -> dict:
    from integracoes.moneyconsig_client import auth_me
    return auth_me()


def moneyconsig_usuario_consultar(*, username: str = '', q: str = '') -> dict:
    from integracoes.moneyconsig_client import usuarios_consulta
    return usuarios_consulta(username=username, q=q)


def moneyconsig_alerta_ti_listar(*, limite: int = 50) -> dict:
    from integracoes.moneyconsig_client import alerta_ti_listar
    return alerta_ti_listar(limite=limite)


def moneyconsig_alerta_ti_criar(
    *,
    mensagem: str = '',
    tipo_destinatario: str = '',
    destinatarios_ids: list | None = None,
) -> dict:
    from integracoes.moneyconsig_client import alerta_ti_criar
    return alerta_ti_criar(
        mensagem=mensagem,
        tipo_destinatario=tipo_destinatario,
        destinatarios_ids=destinatarios_ids,
    )


def moneyconsig_alerta_ti_destinatarios(
    *,
    tipo: str = '',
    empresas: str = '',
    departamentos: str = '',
    setores: str = '',
    cargos: str = '',
) -> dict:
    from integracoes.moneyconsig_client import alerta_ti_destinatarios
    return alerta_ti_destinatarios(
        tipo,
        empresas=empresas,
        departamentos=departamentos,
        setores=setores,
        cargos=cargos,
    )


# ---------------------------------------------------------------------------
# Tags de funil
# ---------------------------------------------------------------------------


def _normalizar_tag_nome(nome: str) -> str:
    nome = re.sub(r'\s+', ' ', (nome or '').strip())
    if len(nome) > 30:
        nome = nome[:30].rstrip()
    return nome


def definir_tag_chamado(ticket_id: int, tag: str = '', *, limpar: bool = False) -> dict:
    """Define ou remove a única tag do chamado (cria TicketTag se necessário)."""
    from django.utils.text import slugify

    from helpdesk.models import TicketTag

    ticket = Ticket.objects.filter(pk=ticket_id).select_related('tag').first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    if limpar or not (tag or '').strip():
        antes = ticket.tag.nome if ticket.tag_id else None
        ticket.tag = None
        ticket.save(update_fields=['tag', 'updated_at'])
        return {'ok': True, 'ticket_id': ticket.pk, 'tag': None, 'tag_antes': antes}

    nome = _normalizar_tag_nome(tag)
    if len(nome) < 2:
        raise AssistenteServiceError('Tag muito curta (mín. 2 caracteres).')
    slug = slugify(nome)[:40] or 'tag'
    obj, created = TicketTag.objects.get_or_create(
        slug=slug,
        defaults={'nome': nome, 'criada_por_ia': True},
    )
    if not created and obj.nome != nome and len(nome) <= 30:
        # Mantém nome existente; só associa
        pass
    antes = ticket.tag.nome if ticket.tag_id else None
    ticket.tag = obj
    ticket.save(update_fields=['tag', 'updated_at'])
    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'tag': obj.nome,
        'tag_id': obj.pk,
        'criada': created,
        'tag_antes': antes,
    }


# ---------------------------------------------------------------------------
# Presença / ajuda TI
# ---------------------------------------------------------------------------


def listar_ti_online() -> dict:
    from helpdesk.presence import listar_ti_online_resumo
    itens = listar_ti_online_resumo()
    return {'ok': True, 'count': len(itens), 'results': itens}


def pedir_ajuda_ti(ticket_id: int, pergunta: str) -> dict:
    """Pergunta interna mencionando todos os TI online (anti-spam 10 min)."""
    from datetime import timedelta

    from helpdesk.mentions import processar_mencoes_assistente
    from helpdesk.presence import usuarios_ti_online

    pergunta = (pergunta or '').strip()
    if not pergunta:
        raise AssistenteServiceError('Informe a pergunta para a TI.')

    ticket = Ticket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise AssistenteServiceError('Chamado não encontrado.', 404)

    if ticket.assistente_ajuda_ti_em:
        delta = timezone.now() - ticket.assistente_ajuda_ti_em
        if delta < timedelta(minutes=10):
            # Se TI já respondeu internamente depois, libera
            respondeu = Comment.objects.filter(
                ticket=ticket,
                is_active=True,
                is_interno=True,
                is_assistente=False,
                created_at__gt=ticket.assistente_ajuda_ti_em,
            ).exists()
            if not respondeu:
                raise AssistenteServiceError(
                    'Já existe pedido de ajuda recente aguardando resposta da TI. '
                    'Aguarde ou continue com o contexto disponível.'
                )

    online = usuarios_ti_online()
    if not online:
        raise AssistenteServiceError(
            'Nenhum membro da TI online no momento. '
            'Deixe nota interna sem @ ou use escalar_para_ti.'
        )

    mencoes = ' '.join(f'@{u.username}' for u in online)
    texto = f'{mencoes}\n[AJUDA TI] {pergunta}'
    comment = Comment.objects.create(
        ticket=ticket,
        author=None,
        text=texto,
        is_assistente=True,
        is_interno=True,
    )
    mencionados = processar_mencoes_assistente(ticket, comment)
    ticket.assistente_ajuda_ti_em = timezone.now()
    ticket.save(update_fields=['assistente_ajuda_ti_em', 'updated_at'])

    try:
        from helpdesk.views.kanban import adicionar_nao_lido_operadores
        adicionar_nao_lido_operadores(ticket, None)
    except Exception:
        pass
    if mencionados:
        try:
            from helpdesk.notifications import agendar_notificacao_mencoes
            agendar_notificacao_mencoes(ticket, mencionados, f'[Interno] {pergunta[:100]}')
        except Exception:
            pass

    return {
        'ok': True,
        'ticket_id': ticket.pk,
        'comment_id': comment.pk,
        'mencionados': [u.username for u in mencionados],
        'online': len(online),
    }


# ---------------------------------------------------------------------------
# Chips — só com autorização de menção interna TI
# ---------------------------------------------------------------------------


def _resolver_titular_chip(
    *,
    tipo_titular: str,
    nome_livre: str = '',
    user_id=None,
    operador_id=None,
):
    from core.models import CustomUser

    tipo = (tipo_titular or 'texto').strip().lower()
    if tipo == 'usuario':
        if not user_id:
            raise AssistenteServiceError('Informe user_id para titular tipo usuario.')
        user = CustomUser.objects.filter(pk=user_id, is_active=True).first()
        if not user:
            raise AssistenteServiceError('Usuário não encontrado.', 404)
        nome = user.get_full_name() or user.username
        return nome, user, None
    if tipo == 'operador':
        if not operador_id:
            raise AssistenteServiceError('Informe operador_id para titular tipo operador.')
        from operadores.models import Operador
        op = Operador.objects.filter(pk=operador_id).first()
        if not op:
            raise AssistenteServiceError('Operador não encontrado.', 404)
        nome = getattr(op, 'nome', None) or str(op)
        return nome, None, op
    nome = (nome_livre or '').strip()
    if not nome:
        raise AssistenteServiceError('Informe nome_livre para titular tipo texto.')
    return nome, None, None


def listar_operadoras_chips() -> dict:
    """Operadoras cadastradas no controle de chips (para resolver nome -> id)."""
    from chips.models import Operator

    itens = [
        {'id': o.pk, 'nome': o.name, 'status': o.status}
        for o in Operator.objects.all().order_by('name')
    ]
    return {'ok': True, 'count': len(itens), 'results': itens}


def _resolver_operadora_chip(operator_id=None, operator_nome: str = ''):
    """Resolve a operadora por id ou nome; erro traz as opções existentes."""
    from chips.models import Operator

    operator = None
    if operator_id:
        operator = Operator.objects.filter(pk=operator_id).first()
    if not operator and operator_nome:
        nome = operator_nome.strip()
        operator = (
            Operator.objects.filter(name__iexact=nome).first()
            or Operator.objects.filter(name__icontains=nome).first()
        )
    if operator:
        return operator

    disponiveis = ', '.join(
        f'{o.pk}={o.name}' for o in Operator.objects.all().order_by('name')
    ) or '(nenhuma cadastrada)'
    raise AssistenteServiceError(
        f'Operadora não encontrada. Cadastradas: {disponiveis}. '
        'Use operator_nome com um destes nomes ou o id correspondente.',
        404,
    )


def criar_chip_assistente(
    ticket_id: int,
    *,
    line_number: str,
    operator_id=None,
    operator_nome: str = '',
    tipo_titular: str = 'texto',
    nome_livre: str = '',
    user_id=None,
    operador_id=None,
    observacao: str = '',
    actor=None,
) -> dict:
    """Cria chip operacional (somente com autorização TI interna)."""
    from django.core.exceptions import ValidationError

    from chips.services import criar_chip_operacional

    line_number = (line_number or '').strip()
    if not line_number:
        raise AssistenteServiceError('Informe line_number.')
    if not operator_id and not operator_nome:
        raise AssistenteServiceError('Informe operator_nome (ex.: TIM) ou operator_id.')
    operator = _resolver_operadora_chip(operator_id, operator_nome)

    nome, emp_user, emp_op = _resolver_titular_chip(
        tipo_titular=tipo_titular,
        nome_livre=nome_livre,
        user_id=user_id,
        operador_id=operador_id,
    )
    try:
        grid = criar_chip_operacional(
            line_number=line_number,
            operator=operator,
            employee_name=nome,
            employee_user=emp_user,
            employee_operador=emp_op,
            observacao=observacao or '',
            actor=actor,
        )
    except ValidationError as exc:
        raise AssistenteServiceError(str(exc)) from exc

    send_assistente_message(
        ticket_id,
        f'Chip {line_number} ({operator.name}) criado/entregue para {nome} (pedido TI).',
        interno=True,
        permitir_repeticao=True,
    )
    return {'ok': True, 'ticket_id': ticket_id, 'chip': grid}


def transferir_chip_assistente(
    ticket_id: int,
    *,
    chip_id=None,
    line_number: str = '',
    tipo_titular: str = 'texto',
    nome_livre: str = '',
    user_id=None,
    operador_id=None,
    actor=None,
) -> dict:
    """Entrega (AVAILABLE) ou transfere (IN_USE) chip — só com autorização TI."""
    from django.core.exceptions import ValidationError

    from chips.models import Chip
    from chips.services import entregar_chip, transferir_chip

    chip = None
    if chip_id:
        chip = Chip.objects.filter(pk=chip_id).first()
    elif line_number:
        chip = Chip.objects.filter(line_number__iexact=line_number.strip()).first()
    if not chip:
        raise AssistenteServiceError('Chip não encontrado.', 404)

    nome, emp_user, emp_op = _resolver_titular_chip(
        tipo_titular=tipo_titular,
        nome_livre=nome_livre,
        user_id=user_id,
        operador_id=operador_id,
    )
    try:
        if chip.usage_status == Chip.UsageChoices.AVAILABLE:
            grid = entregar_chip(
                chip,
                employee_name=nome,
                employee_user=emp_user,
                employee_operador=emp_op,
                actor=actor,
            )
            acao = 'entregue'
        else:
            grid = transferir_chip(
                chip,
                novo_nome=nome,
                novo_user=emp_user,
                novo_operador=emp_op,
                actor=actor,
            )
            acao = 'transferido'
    except ValidationError as exc:
        raise AssistenteServiceError(str(exc)) from exc

    send_assistente_message(
        ticket_id,
        f'Chip {chip.line_number} {acao} para {nome} (pedido TI).',
        interno=True,
        permitir_repeticao=True,
    )
    return {'ok': True, 'ticket_id': ticket_id, 'acao': acao, 'chip': grid}
