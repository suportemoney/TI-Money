# Documentação — `helpdesk/views/`

Views e endpoints do **helpdesk**.

## Para que serve

Permissões centralizadas em `helpdesk/ticket_access.py`. Drawer expõe `pode_comentar` além de `pode_editar` / `pode_excluir`.

## Arquivos

| Arquivo | Função |
|---------|--------|
| `__init__.py` | Exporta views de `kanban`, `dashboard`, `history`, `poll` e `settings`. |
| `kanban.py` | Kanban, criação, categorias, status, drawer, comentários, `ticket_edit` e `ticket_transfer`. |
| `dashboard.py` | `DashboardView` e `DashboardMetricsPartialView` — métricas agregadas. |
| `history.py` | `HistoryListView` — listagem de chamados passados/arquivados (filtro prioridade null). |
| `poll.py` | `poll_ticket_updates` — requisição curta a cada 4s; retorna `HX-Trigger: ticketUpdated` só quando há tickets alterados. |
| `settings.py` | Aba Configurações — CRUD de grupos de restrição (`HelpdeskRestrictionGroup`). |

## Rotas de ticket (kanban.py)

| Rota | View |
|------|------|
| `ticket/create/` | Modal de criação (campos condicionais por papel) |
| `ticket/<pk>/drawer/` | Detalhes + comentários |
| `ticket/<pk>/edit/` | Edição completa (ADMIN/superuser) |
| `ticket/<pk>/transfer/` | Transferência rápida de técnico |
| `ticket/<pk>/update-status/` | Drag-and-drop Kanban (ADMIN/superuser) |
| `ticket/<pk>/comment/` | Novo comentário (exige `usuario_pode_comentar_chamado`) |

## Rotas de configurações (settings.py)

| Rota | View |
|------|------|
| `settings/` | Página de grupos de restrição |
| `settings/grupo/create/` | Cria grupo vazio |
| `settings/grupo/<pk>/` | Salva grupo |
| `settings/grupo/<pk>/delete/` | Exclui grupo |
