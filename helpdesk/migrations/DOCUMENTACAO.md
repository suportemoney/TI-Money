# Documentação — `helpdesk/migrations/`

Migrações Django do app **helpdesk**.

## Para que serve

Versiona tabelas `Ticket` e `Comment` e campos adicionais (ex.: arquivamento).

## Arquivos

| Arquivo | Função |
|---------|--------|
| `0001_initial.py` | Criação de tickets e comentários. |
| `0002_ticket_is_archived.py` | Campo `is_archived` para ocultar chamados resolvidos antigos do Kanban ativo. |
| `0003_ticket_created_by.py` | Campo `created_by` — usuário que abriu o chamado (filtro para papel USER). |
| `0004_ticket_requester_user.py` | Campo `requester_user` — FK opcional do solicitante quando selecionado no sistema. |
| `0005_ticketcategory.py` | Modelo `TicketCategory` e migração do campo `category` de CharField para FK. |
| `0006_ticket_priority_nullable.py` | Prioridade nullable (triagem pela TI). |
| `0007_ticket_description_required.py` | Descrição obrigatória no chamado. |
| `0015_ticket_co_authors.py` | M2M `co_authors` — co-autores com acesso e comentário. |
| `0032_helpdeskrestrictiongroup.py` | Grupos de restrição de visibilidade + seed legado user 25 → TI 2. |
