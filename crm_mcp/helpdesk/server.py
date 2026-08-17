"""MCP Helpdesk — leitura + escrita (Assistente)."""

from mcp.server.fastmcp import FastMCP

from crm_mcp.shared.client import CrmTiApiError, get_client

mcp = FastMCP('crm-ti-helpdesk')


@mcp.tool()
def list_tickets(
    status: str = '',
    q: str = '',
    assigned_to: str = '',
    archived: str = 'false',
    active: str = 'true',
    limit: int = 20,
) -> str:
    """Lista chamados do helpdesk. Filtros: status (NEW|IN_PROGRESS|PENDING|RESOLVED), q, assigned_to, archived, active, limit."""
    try:
        return get_client().get_text('tickets/', {
            'status': status or None,
            'q': q or None,
            'assigned_to': assigned_to or None,
            'archived': archived or None,
            'active': active or None,
            'limit': limit,
        })
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def get_ticket(ticket_id: int) -> str:
    """Retorna detalhes de um chamado pelo ID."""
    try:
        return get_client().get_text(f'tickets/{ticket_id}/')
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def list_ticket_comments(ticket_id: int, limit: int = 50) -> str:
    """Lista comentários ativos de um chamado."""
    try:
        return get_client().get_text(f'tickets/{ticket_id}/comments/', {'limit': limit})
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def send_assistente_message(ticket_id: int, text: str, interno: bool = False) -> str:
    """Envia mensagem no chamado como Assistente. interno=True: só TI vê."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/assistente/comentarios/',
            {'text': text, 'interno': bool(interno)},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def set_ticket_priority(ticket_id: int, priority: str) -> str:
    """Define prioridade: LOW, MEDIUM, HIGH ou URGENT."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/priority/',
            {'priority': priority},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def set_ticket_status(ticket_id: int, status: str) -> str:
    """Altera coluna Kanban: NEW, IN_PROGRESS, PENDING ou RESOLVED."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/status/',
            {'status': status},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def escalar_para_ti(ticket_id: int, motivo: str = '') -> str:
    """Encerra o Assistente e pede intervenção da TI (status PENDING se NEW)."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/assistente/escalar/',
            {'motivo': motivo},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def listar_categorias_especificas() -> str:
    """Lista categorias específicas ativas (id e nome) para triagem."""
    try:
        return get_client().get_text('categorias-especificas/')
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def triar_chamado(ticket_id: int, priority: str, specific_category_id: int = 0) -> str:
    """Triagem: prioridade + categoria específica. specific_category_id=0 omite categoria."""
    body = {'priority': priority}
    if specific_category_id:
        body['specific_category_id'] = specific_category_id
    try:
        return get_client().post_text(f'tickets/{ticket_id}/assistente/triar/', body)
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def recusar_chamado(ticket_id: int, motivo: str) -> str:
    """Recusa chamado (título/descrição incorretos) com motivo."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/assistente/recusar/',
            {'motivo': motivo},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def limpar_recusa_chamado(ticket_id: int) -> str:
    """Remove recusa (badge/motivo) e reabre se ainda estiver Resolvido."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/assistente/limpar-recusa/',
            {},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def listar_anexos(ticket_id: int) -> str:
    """Lista anexos do ticket e comentários (refs ticket:ID / comment:ID)."""
    try:
        return get_client().get_text(f'tickets/{ticket_id}/anexos/')
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def ler_imagem_anexo(ticket_id: int, attachment_ref: str) -> str:
    """Lê print: visão multimodal se houver, senão OCR local → texto."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/anexos/ler-imagem/',
            {'attachment_ref': attachment_ref},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def ler_pdf_anexo(ticket_id: int, attachment_ref: str) -> str:
    """Extrai texto de PDF (nativo ou OCR local)."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/anexos/ler-pdf/',
            {'attachment_ref': attachment_ref},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def ler_anexo_texto(ticket_id: int, attachment_ref: str) -> str:
    """Converte imagem ou PDF em texto para IA só-texto (ex.: DeepSeek)."""
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/anexos/ler-texto/',
            {'attachment_ref': attachment_ref},
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def consultar_chips(q: str) -> str:
    """Busca chips por consultor ou número (WhatsApp)."""
    try:
        return get_client().get_text('assistente/consultar-chips/', {'q': q})
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def consultar_usuario(q: str) -> str:
    """Busca usuário CRM por username ou nome. eh_membro_ti=true = TI."""
    try:
        return get_client().get_text('assistente/consultar-usuario/', {'q': q})
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def atualizar_solicitante(
    ticket_id: int,
    user_id: int = 0,
    nome_livre: str = '',
) -> str:
    """Corrige solicitante: user_id (conta, >0) ou nome_livre (sem conta)."""
    body = {}
    if user_id:
        body['user_id'] = user_id
    if nome_livre:
        body['nome_livre'] = nome_livre
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/assistente/solicitante/',
            body,
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def atualizar_descricao_chamado(
    ticket_id: int,
    description: str,
    title: str = '',
) -> str:
    """Reescreve descrição (e título opcional) do chamado."""
    body = {'description': description}
    if title:
        body['title'] = title
    try:
        return get_client().post_text(
            f'tickets/{ticket_id}/assistente/descricao/',
            body,
        )
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def list_chunks(origem: str = '', limit: int = 30) -> str:
    """Lista chunks de aprendizado ativos do Assistente. origem opcional: ia|manual|chat."""
    try:
        return get_client().get_text('aprendizado/chunks/', {
            'origem': origem or None,
            'limit': limit,
        })
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def get_chunk(chunk_id: int) -> str:
    """Retorna um chunk de aprendizado completo pelo ID."""
    try:
        return get_client().get_text(f'aprendizado/chunks/{chunk_id}/')
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def search_chunks(q: str, limit: int = 20, ativos: str = 'true') -> str:
    """Busca chunks de aprendizado por relevância textual (keyword + sinônimos)."""
    try:
        return get_client().get_text('aprendizado/chunks/search/', {
            'q': q or None,
            'limit': limit,
            'ativos': ativos or 'true',
        })
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def create_chunk(
    titulo: str,
    conteudo: str,
    categoria_hint: str = '',
    tags: str = '',
) -> str:
    """Cria chunk de aprendizado (origem manual). tags: lista separada por vírgula."""
    body = {
        'titulo': titulo,
        'conteudo': conteudo,
        'categoria_hint': categoria_hint or '',
        'tags': tags or '',
    }
    try:
        return get_client().post_text('aprendizado/chunks/criar/', body)
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


@mcp.tool()
def update_chunk(
    chunk_id: int,
    titulo: str = '',
    conteudo: str = '',
    categoria_hint: str = '',
    tags: str = '',
    ativo: str = '',
) -> str:
    """Atualiza chunk de aprendizado. Campos vazios são ignorados. ativo: true|false."""
    body: dict = {}
    if titulo:
        body['titulo'] = titulo
    if conteudo:
        body['conteudo'] = conteudo
    if categoria_hint != '':
        body['categoria_hint'] = categoria_hint
    if tags != '':
        body['tags'] = tags
    if ativo != '':
        body['ativo'] = ativo
    if not body:
        return 'Erro: informe ao menos um campo para atualizar.'
    try:
        return get_client().post_text(f'aprendizado/chunks/{chunk_id}/atualizar/', body)
    except CrmTiApiError as exc:
        return f'Erro: {exc}'


def main():
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
