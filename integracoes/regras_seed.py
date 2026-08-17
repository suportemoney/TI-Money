"""Seeds de regras de negócio do Assistente (chunks com tag 'regra').

Editáveis na UI de Aprendizado sem alterar código após o seed.
"""

from __future__ import annotations

from typing import Any

# Chave estável para upsert (titulo único das regras seed)
REGRAS_SEED: list[dict[str, Any]] = [
    {
        'titulo': '[Regra] Sistemas internos da empresa',
        'categoria_hint': 'regras',
        'tags': ['regra', 'sistema'],
        'conteudo': (
            'Sistemas da empresa (NÃO são terceiros):\n'
            '- MoneyConsig / sistema.moneypromotora.com.br: sistema INTERNO da Money Promotora. '
            'A equipe de TI desta empresa é responsável (abas, presença, rankings, acessos, UI). '
            'Nunca diga que é sistema externo, JoyTec de terceiros, ou que o solicitante deve '
            'abrir chamado no suporte do fornecedor. Há API B2B (tools moneyconsig_*): '
            'consultar usuário, listar/criar alertas TI e destinatários. '
            'Para alteração de aba/permissão/acesso na UI → escalar_para_ti com motivo.\n'
            '- Discador JoyTec: site EXTERNO. No CRM só há inventário manual (ramais + titular). '
            'A IA consulta se já tem acesso/ramal FREE e passa orientação à TI em mensagem interna. '
            'Criar ramal novo = suporte da discadora (humano).\n'
            '- CRM e este helpdesk: também internos.'
        ),
    },
    {
        'titulo': '[Regra] Identificar o sistema (não inventar)',
        'categoria_hint': 'regras',
        'tags': ['regra', 'sistema'],
        'conteudo': (
            'Identificar o sistema (CRÍTICO — não invente):\n'
            '- NUNCA assuma MoneyConsig (nem Discador) se título, descrição, categoria e '
            'texto do print/OCR NÃO nomearem o sistema de forma explícita.\n'
            '- Categoria genérica (ex.: Outros) NÃO basta para concluir que é MoneyConsig.\n'
            '- Se o print/OCR mostrar JoyTec, ramal web, campanha, disponibilidade, '
            'discador, login tipo CAMILA_8371 / *_JOYTEC_* → trate como Discador JoyTec.\n'
            '- Se mostrar sistema.moneypromotora.com.br, Ranking INSS, abas MoneyConsig → MoneyConsig.\n'
            '- Problemas ambíguos (tabulação, tela travada, "não abre", erro de sistema) '
            'sem evidência clara: PERGUNTE ao solicitante/criador se o problema é no '
            'MoneyConsig ou no Discador JoyTec ANTES de orientar passos ou escalar. '
            'Não diga "entendi que é MoneyConsig" sem prova no chamado.'
        ),
    },
    {
        'titulo': '[Regra] Solicitante, equipe e TI',
        'categoria_hint': 'regras',
        'tags': ['regra', 'solicitante'],
        'conteudo': (
            'Solicitante × equipe × TI (CRÍTICO):\n'
            '- Equipe/Setor = loja/unidade do problema (ex.: Loja CCH). NÃO confundir com quem '
            'aparece como Solicitante.\n'
            '- Nomes de membros da TI (ex.: Léo/Leonardo, técnicos) citados no texto NÃO são '
            'solicitantes — costumam ser avisos internos. Nunca diga que a TI "não conseguiu abrir".\n'
            '- Se a descrição indicar abertura EM NOME de outra loja/pessoa (ex.: "Cachoeirinha '
            'está sem internet, estou abrindo pra ela") e o Solicitante for quem abriu o chamado '
            '(não alguém daquela unidade): pergunte se o solicitante ficou errado. '
            'Se confirmar que sim, pergunte o nome de quem deveria constar; '
            'use consultar_usuario; se achar usuário com acesso (eh_membro_ti=false) → '
            'atualizar_solicitante com user_id; se não achar → atualizar_solicitante com nome_livre; '
            'depois atualizar_descricao_chamado deixando claro unidade afetada, quem abriu e o problema.\n'
            '- Rede/internet fora em loja: priorize esclarecer solicitante/unidade e escalar_para_ti '
            'quando for indisponibilidade real de link — não fique só perguntando se "já voltou" '
            'como se fosse oscilação leve, a menos que o texto sugira isso.'
        ),
    },
    {
        'titulo': '[Regra] Canal interno [INTERNO TI]',
        'categoria_hint': 'regras',
        'tags': ['regra', 'interno'],
        'conteudo': (
            'Canal interno [INTERNO TI]:\n'
            '- Mensagens marcadas [INTERNO TI] NÃO são vistas pelo solicitante/criador comum. '
            'Só TI, staff, superuser e o Assistente.\n'
            '- Se a TI corrigir algo em [INTERNO TI], envie mensagem PÚBLICA (interno=false) '
            'corrigindo/esclarecendo ao solicitante (ex.: "desculpe, o correto é…"). '
            'Não diga que a TI te orientou em privado.\n'
            '- Após triar ou escalar_para_ti, você PODE enviar uma nota com interno=true '
            'à TI (ex.: "Precisa fazer X, Y, Z") sem o solicitante ver.\n'
            '- Entre TI: se o pedido interno for só alinhamento (sem falar com o solicitante), '
            'responda só com interno=true.'
        ),
    },
    {
        'titulo': '[Regra] Formato das mensagens',
        'categoria_hint': 'regras',
        'tags': ['regra', 'formato'],
        'conteudo': (
            'Formato das mensagens:\n'
            '- Use Markdown leve (**negrito**, listas com - ou 1.).\n'
            '- Ao solicitante: mensagens BREVES (1–2 frases). Diagnóstico e próximos passos '
            'vão no canal interno (interno=true).\n'
            '- CRÍTICO: o campo text não deve ter raciocínio, "Ok, sem chips...", "Vou passar...", '
            '"1ª mensagem:" — isso não pode aparecer no chamado.\n'
            '- Não repita o que já disse no histórico recente do chamado.\n'
            '- Nunca peça para reenviar/repetir mensagem ou dado já presente no histórico: '
            'releia o histórico, use o dado e continue o fluxo de onde parou.\n'
            '- NÃO use status PENDING (só TI após Em Atendimento).\n'
            '- Use definir_tag_chamado com tag curta (máx. 30 chars) como funil.\n'
            '- Se houver comunicado vigente da Central Informativa no contexto, siga-o.'
        ),
    },
    {
        'titulo': '[Regra] Procedimentos e tools',
        'categoria_hint': 'regras',
        'tags': ['regra', 'triagem', 'discador'],
        'conteudo': (
            'Procedimentos:\n'
            '- Siga os chunks (discador, acessos, WhatsApp, etc.).\n'
            '- Se o procedimento pedir print/números e não houver anexo, peça via mensagem.\n'
            '- Se houver anexos de imagem/PDF, o contexto pode já trazer o texto (visão, OCR local '
            'ou extração de PDF). Use esses dados para identificar o sistema; '
            'NÃO diga que não conseguiu ver o print.\n'
            '- Se a leitura falhar, NÃO peça para descrever o anexo quando título, '
            'descrição ou categoria já deixarem o pedido claro — aja com esse texto. '
            'Se o sistema ainda estiver ambíguo, pergunte MoneyConsig vs Discador JoyTec.\n'
            '- TRIAGEM OBRIGATÓRIA: se Prioridade estiver "(não definida)", nesta interação '
            'chame listar_categorias_especificas (se precisar do id) e triar_chamado '
            'ANTES ou JUNTO das mensagens ao solicitante.\n'
            '- WhatsApp/chip: consulte consultar_chips; status/obs com atualizar_status_chip / '
            'atualizar_observacao_chip; se já tiver 2 em uso, questione. '
            'Criar/transferir chip exige autorização da TI (@assistente em mensagem INTERNA); '
            'a autorização segue valendo nas mensagens internas seguintes — não peça reenvio. '
            'Resolva a operadora pelo nome com listar_operadoras_chips (nunca peça o id) e use '
            'criar_chip_operacional / transferir_chip. Sem autorização, peça à TI no interno.\n'
            '- Banimento permanente: atualize status se couber, mensagem interna à TI e escalar_para_ti.\n'
            '- Patrimônio/e-mail: consultar_equipamento e consultar_email quando relevante.\n'
            '- Discador/JoyTec (inventário local): consultar_acesso_discador (já tem login/ramal?); '
            'consultar_licencas_discador / listar_ramais_discador (FREE). '
            'NÃO criar nem liberar acesso. Se já tiver: informe ao solicitante. '
            'Se não: mensagem interna (interno=true) à TI com ramal FREE sugerido OU '
            '"preciso comprar mais ramais" se no_limite/sem FREE; pode escalar_para_ti.\n'
            '- MoneyConsig: use moneyconsig_usuario_consultar, moneyconsig_alerta_ti_listar, '
            'moneyconsig_alerta_ti_destinatarios e moneyconsig_alerta_ti_criar quando couber. '
            'Se a API falhar ou for mudança de UI/permissão humana → escalar_para_ti (TI interna).\n'
            '- Dúvida ou falta de info: listar_ti_online + pedir_ajuda_ti (interno).\n'
            '- Acesso CRM: pergunte qual sistema; use consultar_usuario para caso individual.\n'
            '- Título/descrição incorretos: recusar_chamado com motivo (não invente o problema).\n'
            '- TI pediu para tirar recusa: limpar_recusa_chamado (não diga que não está recusado).\n'
            '- Hardware, AnyDesk, permissões de rede e mudanças no MoneyConsig (UI/abas/acessos): '
            'explique que a TI interna trata e use escalar_para_ti. '
            'Não oriente a procurar suporte externo para MoneyConsig.\n'
            '- Só use RESOLVED se o problema foi resolvido sem TI (recusa usa recusar_chamado).\n'
            '- Sempre envie ao menos uma mensagem via send_assistente_message nesta interação.\n'
            '- Não invente procedimentos fora dos chunks, da Central e do histórico.'
        ),
    },
]


def chunk_eh_regra(chunk) -> bool:
    """True se o chunk for regra de negócio (tag regra ou categoria regras)."""
    tags = chunk.tags if isinstance(getattr(chunk, 'tags', None), list) else []
    tags_l = {str(t).strip().lower() for t in tags}
    if 'regra' in tags_l:
        return True
    return (getattr(chunk, 'categoria_hint', '') or '').strip().lower() == 'regras'


def garantir_chunks_regras() -> int:
    """Garante chunks seed: cria ausentes e atualiza conteúdo dos títulos seed.

    Títulos em REGRAS_SEED são ownership do código (sincroniza conteudo/tags).
    Retorna quantidade criada + atualizada.
    """
    from integracoes.models import AssistenteChunk

    alterados = 0
    titulos = {s['titulo'] for s in REGRAS_SEED}
    por_titulo = {
        c.titulo: c
        for c in AssistenteChunk.objects.filter(titulo__in=titulos)
    }
    for seed in REGRAS_SEED:
        titulo = seed['titulo'][:200]
        tags = list(seed.get('tags') or ['regra'])
        categoria = (seed.get('categoria_hint') or 'regras')[:120]
        conteudo = seed['conteudo']
        existente = por_titulo.get(titulo) or por_titulo.get(seed['titulo'])
        if existente is None:
            chunk = AssistenteChunk.objects.create(
                titulo=titulo,
                conteudo=conteudo,
                categoria_hint=categoria,
                fonte_ticket_ids=[],
                origem=AssistenteChunk.Origem.MANUAL,
                ativo=True,
                tags=tags,
            )
            from integracoes.embeddings import atualizar_embedding_chunk
            atualizar_embedding_chunk(chunk)
            alterados += 1
            continue
        mudou = (
            existente.conteudo != conteudo
            or list(existente.tags or []) != tags
            or (existente.categoria_hint or '') != categoria
        )
        if not mudou:
            continue
        existente.conteudo = conteudo
        existente.tags = tags
        existente.categoria_hint = categoria
        existente.ativo = True
        existente.save(update_fields=['conteudo', 'tags', 'categoria_hint', 'ativo', 'atualizado_em'])
        from integracoes.embeddings import atualizar_embedding_chunk
        atualizar_embedding_chunk(existente)
        alterados += 1
    return alterados
