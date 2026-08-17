"""Limitações reais das tools do Assistente Helpdesk.

Usado em prompts de análise/reanálise, orientação e curadoria de memória,
para que o aprendizado não invente ações que a IA não consegue executar.
"""

from __future__ import annotations

# Texto reutilizável nos prompts de geração de chunks
LIMITACOES_TOOLS_TEXTO = (
    'LIMITAÇÕES OBRIGATÓRIAS do Assistente (respeite ao escrever cada chunk):\n'
    '- Pode: consultar e atualizar status/observação de chips; consultar patrimônio, '
    'e-mail e usuário; consultar inventário local do Discador; MoneyConsig B2B; '
    'definir tag curta; pedir ajuda a TI online; enviar mensagem pública breve ou '
    'interna detalhada; triar/recusar/limpar recusa/escalar; ler anexos; alterar prioridade/'
    'status (sem PENDING)/solicitante/descrição.\n'
    '- Criar/transferir chip exige autorização de TI: começa com @assistente em '
    'mensagem INTERNA de membro TI e continua valendo nas mensagens internas '
    'seguintes do mesmo atendimento (não peça para reenviar o comando). '
    'Sem autorização: mensagem interna + escalar.\n'
    '- Operadora do chip: resolva o nome com listar_operadoras_chips; nunca peça o id.\n'
    '- NÃO repita mensagens nem peça dados já informados no histórico.\n'
    '- NÃO pode criar/liberar acesso Discador JoyTec nem comprar ramais.\n'
    '- NÃO pode usar status PENDING (só TI após Em Atendimento).\n'
    '- Público = breve; diagnóstico longo = interno.\n'
    '- Comunicados vigentes da Central Informativa prevalecem sobre passos genéricos.\n'
    '- MoneyConsig: tools moneyconsig_*; UI/abas humanas → escalar_para_ti.\n'
    '- Nos chunks, descreva o que a IA DEVE fazer com as tools acima; quando a TI '
    'humana precisa agir, diga explicitamente "escalar / mensagem interna à TI".'
)


def prompt_limitacoes_aprendizado() -> str:
    """Bloco pronto para colar em prompts de análise/orientação."""
    return LIMITACOES_TOOLS_TEXTO
