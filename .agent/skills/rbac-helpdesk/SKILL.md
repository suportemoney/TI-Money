---
name: rbac-helpdesk
description: Matriz de papéis e permissões do helpdesk (visibilidade, comentários, kanban, co-autores). Use antes de alterar ticket_access.py, forms, views ou templates do helpdesk.
disable-model-invocation: false
---

# RBAC do Helpdesk

Consulte também [helpdesk/DOCUMENTACAO.md](../../helpdesk/DOCUMENTACAO.md).

## Papéis

| Papel | Código | Visibilidade | Comentar | Kanban |
|-------|--------|--------------|----------|--------|
| Superuser | `is_superuser` | Tudo | Tudo | Sim |
| Membro TI | `IT_USER` | Todos + arquivados | Todos | Sim |
| Administrador | `ADMIN` | Todos + arquivados | Todos | Sim |
| Supervisor | `SUPERVISOR` | Equipes do usuário | Qualquer visível | Não |
| Líder Equipe | `TEAM_LEADER` | Equipes do usuário | Autor/solicitante/co-autor | Não |
| Multiplicador | `MULTIPLIER` | Próprios + co-autor | Autor/solicitante/co-autor | Não |
| Padrão | `STANDARD` | Próprios | Próprios | Não |

## Grupos de restrição

Aba Configurações (`HelpdeskRestrictionGroup`): cada grupo ativo mapeia usuários cujos chamados (`created_by` ou `requester_user`) só são visíveis aos visualizadores daquele grupo (TI / staff / superuser) e aos stakeholders. Vários grupos são independentes; o mesmo usuário em dois grupos une os visualizadores. Grupo inativo ou com listas vazias é ignorado.

## Arquivos-chave

- `helpdesk/ticket_access.py` — helpers de permissão (fonte da verdade)
- `core/permissions.py` — módulos por role (`ADMIN` só helpdesk; gestão só superuser)
- `helpdesk/forms.py` — campos de criação por papel
- `helpdesk/context_processors.py` — flags para templates

## Co-autor (Multiplicador)

Ao criar chamado com tipo `co_autor`, seleciona membro da mesma equipe → persiste em `Ticket.co_authors`. O co-autor passa a ver e comentar o chamado.

## Líder de Equipe

Vê chamados das equipes das quais faz parte, mas **só comenta** se for `created_by`, `requester_user` ou `co_authors`.

## Front-end

- Solicitante em destaque: `_ticket_card.html`, `_drawer.html`, `history.html`
- Comentário condicional: `pode_comentar` no drawer
- Substituir checagens `request.user.role` por `eh_operador_helpdesk` / `pode_acessar_dashboard_helpdesk`

## Antes de editar

1. Ler `helpdesk/DOCUMENTACAO.md` e este skill
2. Alterar `ticket_access.py` primeiro
3. Propagar para forms, views e templates
4. Atualizar testes em `helpdesk/tests.py` e documentação
