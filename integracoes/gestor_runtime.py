"""Runtime do wizard flutuante de gestão (DeepSeek + tools do CRM)."""

from __future__ import annotations

import json
import logging
from typing import Any

from helpdesk.assistente_services import (
    AssistenteServiceError,
    atualizar_descricao_chamado,
    atualizar_observacao_chip,
    atualizar_solicitante,
    atualizar_status_chip,
    consultar_acesso_discador,
    consultar_chips,
    consultar_email,
    consultar_equipamento,
    consultar_licencas_discador,
    consultar_usuario,
    criar_chip_assistente,
    definir_tag_chamado,
    descrever_imagem_anexo,
    enviar_esclarecimento,
    enviar_pergunta_opcoes,
    escalar_para_ti,
    extrair_texto_pdf_anexo,
    ler_anexo_como_texto,
    limpar_recusa_chamado,
    listar_anexos_ticket,
    listar_campanhas_discador,
    listar_categorias_especificas,
    listar_operadoras_chips,
    listar_ramais_discador,
    listar_ti_online,
    moneyconsig_alerta_ti_criar,
    moneyconsig_alerta_ti_destinatarios,
    moneyconsig_alerta_ti_listar,
    moneyconsig_auth_me,
    moneyconsig_usuario_consultar,
    pedir_ajuda_ti,
    recusar_chamado,
    send_assistente_message,
    set_ticket_priority,
    set_ticket_status,
    transferir_chip_assistente,
    triar_chamado,
)
from integracoes.assistente_runtime import TOOLS_CHIP_SENSIVEIS, TOOLS_SPEC, _parse_args
from integracoes.gestor_discador import (
    DISCADOR_TOOLS_MUTACAO,
    DISCADOR_TOOLS_SPEC,
    executar_tool_discador_gestor,
)
from integracoes.llm import LlmError, chat_completion

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
MAX_SNAPSHOT = 8000
SESSION_KEY = 'gestor_wizard_messages'
MAX_HISTORICO = 40

CONFIRM_EXATAS = frozenset({
    'sim', 's', 'ok', 'okay', 'pode', 'pode executar', 'pode fazer',
    'confirma', 'confirmado', 'confirmar', 'confirmo', 'faz', 'execute',
    'executa', 'ok faz', 'ok, faz', 'yes', 'pode sim',
})

TOOLS_QUE_EXIGEM_TICKET = frozenset({
    'send_assistente_message',
    'enviar_pergunta_opcoes',
    'enviar_esclarecimento',
    'set_ticket_priority',
    'set_ticket_status',
    'triar_chamado',
    'recusar_chamado',
    'limpar_recusa_chamado',
    'listar_anexos',
    'ler_imagem_anexo',
    'ler_pdf_anexo',
    'ler_anexo_texto',
    'atualizar_solicitante',
    'atualizar_descricao_chamado',
    'definir_tag_chamado',
    'pedir_ajuda_ti',
    'escalar_para_ti',
})

GESTOR_TOOLS_MUTACAO = frozenset({
    'send_assistente_message',
    'enviar_pergunta_opcoes',
    'enviar_esclarecimento',
    'set_ticket_priority',
    'set_ticket_status',
    'triar_chamado',
    'recusar_chamado',
    'limpar_recusa_chamado',
    'atualizar_status_chip',
    'atualizar_observacao_chip',
    'atualizar_solicitante',
    'atualizar_descricao_chamado',
    'moneyconsig_alerta_ti_criar',
    'definir_tag_chamado',
    'pedir_ajuda_ti',
    'criar_chip_operacional',
    'transferir_chip',
    'escalar_para_ti',
}) | DISCADOR_TOOLS_MUTACAO

GESTOR_TOOLS_SPEC = list(TOOLS_SPEC) + DISCADOR_TOOLS_SPEC


def mensagem_confirma_mutacao(texto: str) -> bool:
    """True só para confirmação explícita e curta (sim / confirma / pode executar)."""
    t = ' '.join((texto or '').strip().lower().split()).rstrip('.!')
    return t in CONFIRM_EXATAS


def _system_prompt_gestor() -> str:
    return (
        'Você é o wizard de gestão do CRM-TI da Money Promotora. '
        'Fala com o gestor (user allowlist) em português, direto.\n'
        'Objetivo: executar no sistema o que ele pediria na tela, sem ele clicar.\n\n'
        'Regras:\n'
        '- Use as tools. Não invente IDs, ramais, logins ou status.\n'
        '- A página atual (URL + tabelas) está no contexto: use isso quando ele '
        'disser "essa tabela", "esses nomes", "aqui".\n'
        '- MUTAÇÃO (criar, excluir, liberar, alterar status, chip, alerta): '
        'primeiro descreva o plano (o quê, em quais linhas/IDs). '
        'NÃO chame a tool de mutação até a última mensagem do gestor ser '
        'confirmação explícita (sim, confirma, pode executar).\n'
        '- Consultas (listar, consultar) podem rodar sem confirmação.\n'
        '- Tools de chamado só funcionam se houver ticket_id no contexto.\n'
        '- Você NÃO é o Assistente do helpdesk: não precisa mandar mensagem '
        'pública nem escalar para si mesmo.\n'
        '- Discador JoyTec (só você, wizard): criar/editar/inativar ramais, '
        'campanhas, acessos e contrato/licenças — sempre com confirmação.\n'
        '- Inativar acesso = liberar_acesso_discador; inativar ramal = '
        'NOT_CONFIGURED (liberar_licenca_ramal); inativar campanha = is_active false.\n'
        '- Arquivos anexados (print, PDF, CSV, XLSX) vêm como texto/OCR no contexto: use-os.\n'
        '- Chips: pode criar/transferir sem menção @assistente.\n'
        '- Depois de executar, relate o resultado com IDs afetados.'
    )


def montar_contexto_pagina(pagina: dict | None) -> str:
    """Texto de sistema com URL e snapshot das tabelas visíveis."""
    pagina = pagina or {}
    path = (pagina.get('path') or '').strip()
    query = (pagina.get('query') or '').strip()
    title = (pagina.get('title') or '').strip()
    tabelas = (pagina.get('tabelas') or '')[:MAX_SNAPSHOT]
    ticket_id = pagina.get('ticket_id')
    linhas = [
        'Contexto da página atual do gestor:',
        f'- Título: {title or "(sem título)"}',
        f'- URL: {path}{query}',
    ]
    if ticket_id:
        linhas.append(f'- ticket_id: {ticket_id}')
    else:
        linhas.append('- ticket_id: (nenhum chamado aberto nesta página)')
    if tabelas.strip():
        linhas.append('- Tabelas visíveis:')
        linhas.append(tabelas.strip())
    else:
        linhas.append('- Tabelas visíveis: (vazio)')
    return '\n'.join(linhas)


def _json_ok(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _bloquear_mutacao(name: str, args: dict) -> str:
    return _json_ok({
        'ok': False,
        'precisa_confirmacao': True,
        'tool': name,
        'args': args,
        'error': (
            'Mutação bloqueada até o gestor confirmar no chat '
            '(diga "confirma", "sim" ou "pode executar"). '
            f'Plano: {name} {json.dumps(args, ensure_ascii=False)}'
        ),
    })


def _auditar_mutacao(ctx: dict, name: str, args: dict, resultado: dict) -> None:
    if not resultado.get('ok'):
        return
    actor = ctx.get('actor')
    if not actor:
        return
    from core.audit import MODULO_CORE, registrar_acao
    from core.models import RegistroAcao

    mutacoes = ctx.setdefault('mutacoes', [])
    mutacoes.append({'tool': name, 'args': args})
    try:
        registrar_acao(
            modulo=MODULO_CORE,
            acao=RegistroAcao.AcaoChoices.UPDATED,
            descricao=f'Wizard gestor executou {name}.',
            actor=actor,
            metadata={'tool': name, 'args': args},
        )
    except Exception:
        logger.exception('Falha ao auditar tool %s do wizard', name)


def _chip_sem_ticket(name: str, args: dict, actor) -> str:
    """Cria/transfere chip sem postar no helpdesk."""
    from chips.models import Chip
    from chips.services import criar_chip_operacional, entregar_chip, transferir_chip
    from helpdesk.assistente_services import _resolver_operadora_chip, _resolver_titular_chip

    if name == 'criar_chip_operacional':
        operator = _resolver_operadora_chip(args.get('operator_id'), args.get('operator_nome') or '')
        nome, emp_user, emp_op = _resolver_titular_chip(
            tipo_titular=args.get('tipo_titular') or 'texto',
            nome_livre=args.get('nome_livre') or '',
            user_id=args.get('user_id'),
            operador_id=args.get('operador_id'),
        )
        grid = criar_chip_operacional(
            line_number=args.get('line_number') or '',
            operator=operator,
            employee_name=nome,
            employee_user=emp_user,
            employee_operador=emp_op,
            observacao=args.get('observacao') or '',
            actor=actor,
        )
        return _json_ok({'ok': True, 'chip': grid})

    chip = None
    if args.get('chip_id'):
        chip = Chip.objects.filter(pk=args.get('chip_id')).first()
    elif args.get('line_number'):
        chip = Chip.objects.filter(line_number__iexact=str(args.get('line_number')).strip()).first()
    if not chip:
        raise AssistenteServiceError('Chip não encontrado.', 404)
    nome, emp_user, emp_op = _resolver_titular_chip(
        tipo_titular=args.get('tipo_titular') or 'texto',
        nome_livre=args.get('nome_livre') or '',
        user_id=args.get('user_id'),
        operador_id=args.get('operador_id'),
    )
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
    return _json_ok({'ok': True, 'acao': acao, 'chip': grid})


def executar_tool_gestor(name: str, args: dict, ctx: dict) -> str:
    """Dispatch das tools do gestor (confirmação + menos restrições)."""
    args = args or {}
    try:
        if name in GESTOR_TOOLS_MUTACAO and not ctx.get('confirma'):
            return _bloquear_mutacao(name, args)

        ticket_id = ctx.get('ticket_id')
        actor = ctx.get('actor')

        if name in TOOLS_QUE_EXIGEM_TICKET and not ticket_id:
            return _json_ok({
                'ok': False,
                'error': 'Não há chamado nesta página. Abra o chamado ou informe o ticket_id.',
            })

        if name in DISCADOR_TOOLS_MUTACAO:
            resultado = executar_tool_discador_gestor(name, args, actor)
            _auditar_mutacao(ctx, name, args, resultado)
            return _json_ok(resultado)
        if name == 'set_ticket_status':
            resultado = set_ticket_status(
                ticket_id, args.get('status', ''), via_assistente=False,
            )
            _auditar_mutacao(ctx, name, args, resultado)
            return _json_ok(resultado)
        if name in TOOLS_CHIP_SENSIVEIS:
            if ticket_id:
                if name == 'criar_chip_operacional':
                    resultado = criar_chip_assistente(
                        ticket_id,
                        line_number=args.get('line_number') or '',
                        operator_id=args.get('operator_id'),
                        operator_nome=args.get('operator_nome') or '',
                        tipo_titular=args.get('tipo_titular') or 'texto',
                        nome_livre=args.get('nome_livre') or '',
                        user_id=args.get('user_id'),
                        operador_id=args.get('operador_id'),
                        observacao=args.get('observacao') or '',
                        actor=actor,
                    )
                else:
                    resultado = transferir_chip_assistente(
                        ticket_id,
                        chip_id=args.get('chip_id'),
                        line_number=args.get('line_number') or '',
                        tipo_titular=args.get('tipo_titular') or 'texto',
                        nome_livre=args.get('nome_livre') or '',
                        user_id=args.get('user_id'),
                        operador_id=args.get('operador_id'),
                        actor=actor,
                    )
                _auditar_mutacao(ctx, name, args, resultado)
                return _json_ok(resultado)
            bruto = _chip_sem_ticket(name, args, actor)
            parsed = json.loads(bruto)
            _auditar_mutacao(ctx, name, args, parsed)
            return bruto

        # Consultas e demais tools do helpdesk (ticket_id só quando existir)
        resultado_txt = _executar_tool_helpdesk(ticket_id or 0, name, args)
        try:
            parsed = json.loads(resultado_txt)
        except json.JSONDecodeError:
            parsed = {'ok': False}
        if name in GESTOR_TOOLS_MUTACAO:
            _auditar_mutacao(ctx, name, args, parsed if isinstance(parsed, dict) else {})
        return resultado_txt
    except AssistenteServiceError as exc:
        return _json_ok({'ok': False, 'error': str(exc)})
    except Exception as exc:
        from django.core.exceptions import ValidationError as DjangoValidationError
        if isinstance(exc, DjangoValidationError):
            return _json_ok({'ok': False, 'error': '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc)})
        if isinstance(exc, (TypeError, ValueError)):
            return _json_ok({'ok': False, 'error': f'Argumentos inválidos: {exc}'})
        logger.exception('Wizard tool %s falhou', name)
        return _json_ok({'ok': False, 'error': str(exc)})


def _executar_tool_helpdesk(ticket_id: int, name: str, args: dict) -> str:
    """Espelho das tools de consulta/ticket do Assistente (sem gate de chip)."""
    if name == 'send_assistente_message':
        return _json_ok(send_assistente_message(
            ticket_id, args.get('text', ''), interno=bool(args.get('interno')),
        ))
    if name == 'enviar_pergunta_opcoes':
        return _json_ok(enviar_pergunta_opcoes(
            ticket_id, args.get('pergunta') or '', args.get('opcoes') or [],
            contexto_curto=args.get('contexto_curto') or '',
        ))
    if name == 'enviar_esclarecimento':
        return _json_ok(enviar_esclarecimento(
            ticket_id, args.get('texto') or '', lacunas=args.get('lacunas') or None,
        ))
    if name == 'set_ticket_priority':
        return _json_ok(set_ticket_priority(ticket_id, args.get('priority', '')))
    if name == 'listar_categorias_especificas':
        return _json_ok(listar_categorias_especificas())
    if name == 'triar_chamado':
        return _json_ok(triar_chamado(
            ticket_id, args.get('priority', ''), args.get('specific_category_id'),
        ))
    if name == 'recusar_chamado':
        return _json_ok(recusar_chamado(ticket_id, args.get('motivo', '')))
    if name == 'limpar_recusa_chamado':
        return _json_ok(limpar_recusa_chamado(ticket_id))
    if name == 'listar_anexos':
        return _json_ok(listar_anexos_ticket(ticket_id))
    if name == 'ler_imagem_anexo':
        return _json_ok(descrever_imagem_anexo(ticket_id, args.get('attachment_ref', '')))
    if name == 'ler_pdf_anexo':
        return _json_ok(extrair_texto_pdf_anexo(ticket_id, args.get('attachment_ref', '')))
    if name == 'ler_anexo_texto':
        return _json_ok(ler_anexo_como_texto(ticket_id, args.get('attachment_ref', '')))
    if name == 'consultar_chips':
        return _json_ok(consultar_chips(args.get('q', '')))
    if name == 'atualizar_status_chip':
        return _json_ok(atualizar_status_chip(
            args.get('chip_id'), args.get('line_number') or '', args.get('status', ''),
        ))
    if name == 'atualizar_observacao_chip':
        return _json_ok(atualizar_observacao_chip(
            args.get('chip_id'), args.get('line_number') or '', args.get('observacao', ''),
        ))
    if name == 'consultar_equipamento':
        return _json_ok(consultar_equipamento(args.get('q', '')))
    if name == 'consultar_email':
        return _json_ok(consultar_email(args.get('q', '')))
    if name == 'consultar_usuario':
        return _json_ok(consultar_usuario(args.get('q', '')))
    if name == 'atualizar_solicitante':
        return _json_ok(atualizar_solicitante(
            ticket_id, args.get('user_id'), args.get('nome_livre', ''),
        ))
    if name == 'atualizar_descricao_chamado':
        return _json_ok(atualizar_descricao_chamado(
            ticket_id, args.get('description', ''), args.get('title'),
        ))
    if name == 'consultar_licencas_discador':
        return _json_ok(consultar_licencas_discador(args.get('slug') or 'joytec'))
    if name == 'listar_ramais_discador':
        return _json_ok(listar_ramais_discador(
            args.get('status') or '', args.get('slug') or 'joytec', args.get('limit') or 40,
        ))
    if name == 'consultar_acesso_discador':
        return _json_ok(consultar_acesso_discador(
            args.get('q', ''), args.get('slug') or 'joytec',
        ))
    if name == 'listar_campanhas_discador':
        so_ativas = args.get('so_ativas')
        if so_ativas is None:
            so_ativas = True
        return _json_ok(listar_campanhas_discador(
            args.get('slug') or 'joytec', so_ativas=bool(so_ativas),
        ))
    if name == 'moneyconsig_auth_me':
        return _json_ok(moneyconsig_auth_me())
    if name == 'moneyconsig_usuario_consultar':
        return _json_ok(moneyconsig_usuario_consultar(
            username=args.get('username') or '', q=args.get('q') or '',
        ))
    if name == 'moneyconsig_alerta_ti_listar':
        return _json_ok(moneyconsig_alerta_ti_listar(limite=args.get('limite') or 50))
    if name == 'moneyconsig_alerta_ti_criar':
        return _json_ok(moneyconsig_alerta_ti_criar(
            mensagem=args.get('mensagem') or '',
            tipo_destinatario=args.get('tipo_destinatario') or '',
            destinatarios_ids=args.get('destinatarios_ids') or [],
        ))
    if name == 'moneyconsig_alerta_ti_destinatarios':
        return _json_ok(moneyconsig_alerta_ti_destinatarios(
            tipo=args.get('tipo') or '',
            empresas=args.get('empresas') or '',
            departamentos=args.get('departamentos') or '',
            setores=args.get('setores') or '',
            cargos=args.get('cargos') or '',
        ))
    if name == 'definir_tag_chamado':
        return _json_ok(definir_tag_chamado(
            ticket_id, args.get('tag') or '', limpar=bool(args.get('limpar')),
        ))
    if name == 'listar_ti_online':
        return _json_ok(listar_ti_online())
    if name == 'pedir_ajuda_ti':
        return _json_ok(pedir_ajuda_ti(ticket_id, args.get('pergunta') or ''))
    if name == 'listar_operadoras_chips':
        return _json_ok(listar_operadoras_chips())
    if name == 'escalar_para_ti':
        return _json_ok(escalar_para_ti(ticket_id, args.get('motivo', '')))
    return _json_ok({'ok': False, 'error': f'Tool desconhecida: {name}'})


def processar_gestor(
    *,
    user,
    mensagem: str,
    historico: list,
    pagina: dict | None,
    anexos_texto: str = '',
) -> dict:
    """Uma interação do wizard: LLM + tools. Devolve reply em texto."""
    pagina = pagina or {}
    ticket_raw = pagina.get('ticket_id')
    try:
        ticket_id = int(ticket_raw) if ticket_raw else None
    except (TypeError, ValueError):
        ticket_id = None

    ctx = {
        'ticket_id': ticket_id,
        'actor': user,
        'confirma': mensagem_confirma_mutacao(mensagem),
        'mutacoes': [],
    }

    messages: list[dict[str, Any]] = [
        {'role': 'system', 'content': _system_prompt_gestor()},
        {'role': 'system', 'content': montar_contexto_pagina(pagina)},
    ]
    if (anexos_texto or '').strip():
        messages.append({'role': 'system', 'content': anexos_texto.strip()})
    for item in (historico or [])[-MAX_HISTORICO:]:
        role = item.get('role')
        content = (item.get('content') or '').strip()
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': mensagem})

    reply = ''
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            msg = chat_completion(messages, tools=GESTOR_TOOLS_SPEC, temperature=0.3)
            messages.append(msg)
            tool_calls = msg.get('tool_calls') or []
            content = (msg.get('content') or '').strip()
            if not tool_calls:
                reply = content
                break
            for call in tool_calls:
                fn = call.get('function') or {}
                name = fn.get('name') or ''
                args = _parse_args(fn.get('arguments'))
                result = executar_tool_gestor(name, args, ctx)
                messages.append({
                    'role': 'tool',
                    'tool_call_id': call.get('id') or name,
                    'content': result,
                })
        if not reply:
            # Última fala da LLM após tools, se houver
            for item in reversed(messages):
                if item.get('role') == 'assistant' and (item.get('content') or '').strip():
                    reply = item['content'].strip()
                    break
        if not reply:
            if ctx.get('mutacoes'):
                reply = 'Ação executada. Confira o resultado nas tools.'
            else:
                reply = (
                    'Não consegui concluir agora. Reformule o pedido ou '
                    'confirme com "sim" se eu já mostrei o plano.'
                )
    except LlmError as exc:
        logger.warning('Wizard LLM: %s', exc)
        raise

    return {
        'reply': reply,
        'mutacoes': ctx.get('mutacoes') or [],
        'ticket_id': ticket_id,
    }
