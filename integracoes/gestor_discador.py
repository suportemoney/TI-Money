"""Tools de escrita JoyTec exclusivas do wizard de gestão."""

from __future__ import annotations

from helpdesk.assistente_services import (
    atualizar_acesso_discador,
    atualizar_campanha_discador,
    atualizar_contrato_discador,
    atualizar_ramal_discador,
    criar_acesso_discador,
    criar_campanha_discador,
    criar_ramal_discador,
    excluir_campanha_discador,
    excluir_ramal_discador,
    inativar_campanha_discador,
    liberar_acesso_discador,
    liberar_licenca_ramal,
)

_DESC_CONFIRMA = ' Exige clique em Confirmar na interface do wizard.'


def _fn(name: str, description: str, properties: dict, required: list | None = None) -> dict:
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': description + _DESC_CONFIRMA,
            'parameters': {
                'type': 'object',
                'properties': properties,
                'required': required or [],
            },
        },
    }


DISCADOR_TOOLS_SPEC = [
    _fn(
        'criar_acesso_discador',
        'Cria acesso JoyTec (titular, login, ramal, campanha, tipo).',
        {
            'titular_nome': {'type': 'string'},
            'login_discador': {'type': 'string'},
            'tipo': {'type': 'string'},
            'ramal_id': {'type': 'integer'},
            'ramal_numero': {'type': 'string'},
            'campanha_id': {'type': 'integer'},
            'campanha_nome': {'type': 'string'},
            'slug': {'type': 'string'},
        },
        ['titular_nome', 'login_discador'],
    ),
    _fn(
        'atualizar_acesso_discador',
        'Edita acesso existente (titular, login, ramal, campanha, tipo).',
        {
            'acesso_id': {'type': 'integer'},
            'titular_nome': {'type': 'string'},
            'login_discador': {'type': 'string'},
            'tipo': {'type': 'string'},
            'ramal_id': {'type': 'integer'},
            'ramal_numero': {'type': 'string'},
            'campanha_id': {'type': 'integer'},
            'campanha_nome': {'type': 'string'},
            'slug': {'type': 'string'},
        },
        ['acesso_id'],
    ),
    _fn(
        'liberar_acesso_discador',
        'Inativa/exclui o acesso e deixa o ramal Livre. Use acesso_id.',
        {'acesso_id': {'type': 'integer'}},
        ['acesso_id'],
    ),
    _fn(
        'criar_ramal_discador',
        'Cadastra ramal. status: IN_USE, FREE ou NOT_CONFIGURED (padrão).',
        {
            'numero': {'type': 'string'},
            'status': {'type': 'string'},
            'slug': {'type': 'string'},
        },
        ['numero'],
    ),
    _fn(
        'atualizar_ramal_discador',
        'Edita ramal (número e/ou status). Inativar = NOT_CONFIGURED.',
        {
            'ramal_id': {'type': 'integer'},
            'ramal_numero': {'type': 'string'},
            'numero': {'type': 'string'},
            'status': {'type': 'string'},
            'slug': {'type': 'string'},
        },
    ),
    _fn(
        'liberar_licenca_ramal',
        'Marca ramal como Não configurado (deixa de consumir licença). Sem acesso.',
        {
            'ramal_id': {'type': 'integer'},
            'ramal_numero': {'type': 'string'},
            'slug': {'type': 'string'},
        },
    ),
    _fn(
        'excluir_ramal_discador',
        'Remove o ramal do cadastro. Precisa estar sem acesso.',
        {
            'ramal_id': {'type': 'integer'},
            'ramal_numero': {'type': 'string'},
            'slug': {'type': 'string'},
        },
    ),
    _fn(
        'criar_campanha_discador',
        'Cria campanha ativa no discador.',
        {'nome': {'type': 'string'}, 'slug': {'type': 'string'}},
        ['nome'],
    ),
    _fn(
        'atualizar_campanha_discador',
        'Edita nome e/ou ativa/inativa campanha (is_active).',
        {
            'campanha_id': {'type': 'integer'},
            'campanha_nome': {'type': 'string'},
            'nome': {'type': 'string'},
            'is_active': {'type': 'boolean'},
            'slug': {'type': 'string'},
        },
    ),
    _fn(
        'inativar_campanha_discador',
        'Inativa campanha (is_active=false). Use se houver acessos vinculados.',
        {
            'campanha_id': {'type': 'integer'},
            'campanha_nome': {'type': 'string'},
            'slug': {'type': 'string'},
        },
    ),
    _fn(
        'excluir_campanha_discador',
        'Exclui campanha sem acessos. Se houver acessos, inative.',
        {
            'campanha_id': {'type': 'integer'},
            'campanha_nome': {'type': 'string'},
            'slug': {'type': 'string'},
        },
    ),
    _fn(
        'atualizar_contrato_discador',
        'Altera licenças contratadas e/ou valor por licença do contrato.',
        {
            'licencas_contratadas': {'type': 'integer'},
            'valor_por_licenca': {'type': 'number'},
            'observacao': {'type': 'string'},
            'slug': {'type': 'string'},
        },
    ),
]

DISCADOR_TOOLS_MUTACAO = frozenset(spec['function']['name'] for spec in DISCADOR_TOOLS_SPEC)


def executar_tool_discador_gestor(name: str, args: dict, actor) -> dict:
    """Dispatch das mutações JoyTec do wizard. Retorno já é dict com ok."""
    args = args or {}
    slug = args.get('slug') or 'joytec'
    if name == 'criar_acesso_discador':
        return criar_acesso_discador(
            args.get('titular_nome') or '',
            args.get('login_discador') or '',
            args.get('tipo') or 'CONSULTOR',
            args.get('ramal_id'),
            args.get('ramal_numero') or '',
            args.get('campanha_id'),
            args.get('campanha_nome') or '',
            slug,
            actor=actor,
        )
    if name == 'atualizar_acesso_discador':
        return atualizar_acesso_discador(
            int(args.get('acesso_id') or 0),
            titular_nome=args.get('titular_nome'),
            login_discador=args.get('login_discador'),
            tipo=args.get('tipo'),
            ramal_id=args.get('ramal_id'),
            ramal_numero=args.get('ramal_numero') or '',
            campanha_id=args.get('campanha_id'),
            campanha_nome=args.get('campanha_nome') or '',
            slug=slug,
            actor=actor,
        )
    if name == 'liberar_acesso_discador':
        return liberar_acesso_discador(int(args.get('acesso_id') or 0), actor=actor)
    if name == 'criar_ramal_discador':
        return criar_ramal_discador(
            args.get('numero') or '', args.get('status') or 'NOT_CONFIGURED', slug, actor=actor,
        )
    if name == 'atualizar_ramal_discador':
        return atualizar_ramal_discador(
            args.get('ramal_id'),
            args.get('ramal_numero') or '',
            args.get('numero') or '',
            args.get('status') or '',
            slug,
            actor=actor,
        )
    if name == 'liberar_licenca_ramal':
        return liberar_licenca_ramal(
            args.get('ramal_id'), args.get('ramal_numero') or '', slug, actor=actor,
        )
    if name == 'excluir_ramal_discador':
        return excluir_ramal_discador(
            args.get('ramal_id'), args.get('ramal_numero') or '', slug, actor=actor,
        )
    if name == 'criar_campanha_discador':
        return criar_campanha_discador(args.get('nome') or '', slug, actor=actor)
    if name == 'atualizar_campanha_discador':
        return atualizar_campanha_discador(
            args.get('campanha_id'),
            args.get('campanha_nome') or '',
            args.get('nome') or '',
            args.get('is_active'),
            slug,
            actor=actor,
        )
    if name == 'inativar_campanha_discador':
        return inativar_campanha_discador(
            args.get('campanha_id'), args.get('campanha_nome') or '', slug, actor=actor,
        )
    if name == 'excluir_campanha_discador':
        return excluir_campanha_discador(
            args.get('campanha_id'), args.get('campanha_nome') or '', slug, actor=actor,
        )
    if name == 'atualizar_contrato_discador':
        return atualizar_contrato_discador(
            args.get('licencas_contratadas'),
            args.get('valor_por_licenca'),
            args.get('observacao') or '',
            slug,
            actor=actor,
        )
    return {'ok': False, 'error': f'Tool discador desconhecida: {name}'}
