# Documentação — `helpdesk/templates/helpdesk/`

Templates HTML do módulo **helpdesk**.

## Para que serve

Telas completas e fragmentos reutilizáveis para Kanban dinâmico e drawer lateral de ticket.

## Flags de contexto (global)

Via `helpdesk/context_processors.py`:

- `eh_operador_helpdesk` — Membro TI, Administrador ou superuser
- `pode_operar_kanban` — pode mover cards
- `pode_acessar_dashboard_helpdesk` — dashboard e histórico

No drawer: `pode_comentar` (por chamado).

Aba **Configurações** (`settings.html`): visível para `eh_operador_helpdesk`. CRUD de `HelpdeskRestrictionGroup` (flag restringir, usuários e visualizadores TI).

## Destaque do solicitante

- `_ticket_card.html`: nome em `font-semibold text-blue-700`, descrição separada
- `_drawer.html`: badge `bg-blue-50 text-blue-800`
- `history.html`: `font-semibold text-slate-800`

## Arquivos

| Arquivo | Função |
|---------|--------|
| `kanban.html` | Página principal do quadro Kanban (`/helpdesk/`). |
| `_ticket_card.html` | Card com solicitante em destaque. |
| `_drawer.html` | Detalhes; comentário condicional via `pode_comentar`. |
| `_ticket_create_modal.html` | Modal — campos por papel incl. co-autor (MULTIPLIER). |
| `_nav.html` | Nav interna; dashboard/histórico via `pode_acessar_dashboard_helpdesk`; Configurações para operadores. |
| `history.html` | Histórico com solicitante destacado. |
| `settings.html` | Grupos de restrição de chamados. |
| `_restriction_groups.html` | Lista HTMX dos grupos. |
| `_restriction_group_card.html` | Card de um grupo (flag, usuários, visualizadores). |

## Convenção

Arquivos com prefixo `_` são **partials** incluídos ou carregados por HTMX, não páginas completas.
