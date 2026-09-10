# Documentação — `helpdesk/`

App de **chamados de TI** com Kanban, métricas, histórico e atualização em tempo quase real (poll HTMX + partials).

## Para que serve

Registrar tickets (título, prioridade, categoria, solicitante, co-autores), movê-los entre colunas de status, comentar, arquivar resolvidos antigos e exibir dashboard de métricas.

## Matriz de papéis (RBAC)

| Papel | Código | Módulos | Ver chamados | Arquivados | Kanban move | Comentar | Contestar |
|-------|--------|---------|--------------|------------|-------------|----------|-----------|
| Superuser | `is_superuser` | Todos | Todos | Sim | Sim | Todos | Não |
| Membro Equipe (TI) | `IT_USER` | chips, emails, equipment, helpdesk | Todos | Sim | Sim | Todos | Não |
| Administrador | `ADMIN` | helpdesk | Todos | Sim | Sim | Todos | Não |
| Supervisor | `SUPERVISOR` | helpdesk | Equipes do usuário | Não | Não | Qualquer visível* | Não |
| Líder de Equipe | `TEAM_LEADER` | helpdesk | Equipes do usuário | Não | Não | Só autor/solicitante/co-autor* | Não |
| Multiplicador | `MULTIPLIER` | helpdesk | Próprios + co-autor | Não | Não | Só autor/solicitante/co-autor* | Não |
| Usuário Padrão | `STANDARD` | helpdesk | Próprios | Não | Não | Próprios* | Sim** |

\* Em chamados **finalizados** (`status=RESOLVED`), apenas operadores helpdesk (Admin, Membro TI, superuser) podem comentar.

\*\* Contestação: solicitante vinculado (autor, `requester_user` ou co-autor) em chamado finalizado **não arquivado** — reabre como Novo e registra histórico em `TicketContestation`.

**Criação de chamados:**
- `STANDARD`: sempre para si.
- `SUPERVISOR` / `TEAM_LEADER`: equipes das quais é membro; eu / nome livre.
- `MULTIPLIER`: eu / nome livre / co-autor da equipe (M2M `co_authors`).
- `IT_USER` / `ADMIN` / superuser: nome livre ou usuário do sistema.

**Co-autores:** campo `Ticket.co_authors` — concedem visibilidade e permissão de comentário ao multiplicador compartilhar chamados.

**Django Admin:** apenas `is_superuser` (`is_staff=True`). Demais papéis não devem ter acesso ao admin.

**Grupos de restrição** (`HelpdeskRestrictionGroup`): aba Configurações. Chamados cujo criador ou solicitante está num grupo ativo só aparecem para os visualizadores daquele grupo (TI/staff/superuser) e para stakeholders. Vários grupos são independentes.

Implementação central: `ticket_access.py`. Skill do agente: `.agent/skills/rbac-helpdesk/SKILL.md`.

## Arquivos

| Arquivo | Função |
|---------|--------|
| `apps.py` | Configuração do app. |
| `models.py` | `TicketCategory`, `Ticket`, `Comment`, `HelpdeskRestrictionGroup` (grupos de restrição de visibilidade). |
| `forms.py` | `TicketCreateForm`, `TicketUpdateForm` e `HelpdeskRestrictionGroupForm`. |
| `ticket_access.py` | Filtro de chamados, permissões por papel, grupos de restrição. |
| `context_processors.py` | Flags `eh_operador_helpdesk`, `pode_operar_kanban`, `pode_acessar_dashboard_helpdesk`. |
| `audit.py` | Wrappers de `registrar_acao` para eventos do helpdesk. |
| `urls.py` | Rotas sob `/helpdesk/`. |
| `views/` | Lógica de telas e APIs parciais. Ver `views/DOCUMENTACAO.md`. |
| `templates/` | Front do helpdesk. Ver `templates/DOCUMENTACAO.md`. |

## Status do Kanban (`Ticket.StatusChoices`)

Novos → Em Atendimento → Pendente → Resolvido (arquivamento automático 24h após finalização, via `resolved_at`).
