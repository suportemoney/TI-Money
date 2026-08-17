from django.urls import path
from . import views

app_name = 'helpdesk'

urlpatterns = [
    # Dashboard de Métricas
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    
    # Kanban principal (Vamos redirecionar a home do helpdesk para o dashboard ou Kanban, escolhi Kanban como raiz inicial)
    path('', views.KanbanView.as_view(), name='kanban'),
    
    # Histórico e Filtros
    path('history/', views.HistoryListView.as_view(), name='history'),
    path('history/export/', views.history_export_csv, name='history_export'),
    
    # Gerenciamento de Categorias
    path('categories/', views.CategoriesManageView.as_view(), name='categories'),
    path('categories/create/<str:model_type>/', views.category_create_action, name='category_create_action'),
    path('categories/toggle/<str:model_type>/<int:pk>/', views.category_toggle_active, name='category_toggle_active'),
    path('categories/edit/<str:model_type>/<int:pk>/', views.category_edit, name='category_edit'),
    path('categories/delete/<str:model_type>/<int:pk>/', views.category_delete, name='category_delete'),
    
    # Real-Time e Partials via HTMX
    path('poll/', views.poll_ticket_updates, name='poll'),
    path('push/vapid-public-key/', views.push_vapid_public_key, name='push_vapid_public_key'),
    path('push/status/', views.push_status, name='push_status'),
    path('push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('push/unsubscribe/', views.push_unsubscribe, name='push_unsubscribe'),
    path('sw.js', views.service_worker_js, name='service_worker'),
    path('kanban/board/', views.KanbanBoardPartialView.as_view(), name='kanban_board'),
    path('dashboard/metrics/', views.DashboardMetricsPartialView.as_view(), name='dashboard_metrics'),
    
    # Criação de chamados
    path('ticket/create/', views.TicketCreateView.as_view(), name='ticket_create'),
    path('category/create/', views.ticket_category_create, name='category_create'),
    
    # Ações assíncronas via Fetch/HTMX
    path('ticket/<int:pk>/update-status/', views.ticket_update_status, name='ticket_update_status'),
    path('ticket/<int:pk>/finalize/', views.ticket_finalize, name='ticket_finalize'),
    path('ticket/<int:pk>/contest/', views.ticket_contest, name='ticket_contest'),
    path('ticket/<int:pk>/drawer/', views.ticket_drawer, name='ticket_drawer'),
    path('ticket/<int:pk>/edit/', views.ticket_edit, name='ticket_edit'),
    path('ticket/<int:pk>/delete/', views.ticket_delete, name='ticket_delete'),
    path('ticket/<int:pk>/refresh-ia/', views.ticket_refresh_assistente, name='ticket_refresh_assistente'),
    path('ticket/<int:pk>/attachments/', views.ticket_attachments, name='ticket_attachments'),
    path('ticket/<int:pk>/transfer/', views.ticket_transfer, name='ticket_transfer'),
    path('ticket/<int:pk>/comment/', views.ticket_add_comment, name='ticket_add_comment'),
    path('ticket/<int:pk>/comments/', views.ticket_comments, name='ticket_comments'),
    path(
        'ticket/<int:ticket_pk>/comment/<int:comment_pk>/opcao/',
        views.ticket_responder_opcao,
        name='ticket_responder_opcao',
    ),
    path(
        'ticket/<int:ticket_pk>/comment/<int:comment_pk>/edit/',
        views.comment_edit,
        name='comment_edit',
    ),
    path(
        'ticket/<int:ticket_pk>/comment/<int:comment_pk>/delete/',
        views.comment_delete,
        name='comment_delete',
    ),
    path('api/clipboard-image/', views.fetch_clipboard_image, name='fetch_clipboard_image'),
    
    # Central Informativa
    path('informative/', views.informative_center, name='informative_center'),
    path('informative/list/', views.informative_list, name='informative_list'),
    path('informative/create/', views.informative_create, name='informative_create'),
    path('informative/<int:message_id>/acknowledge/', views.informative_acknowledge, name='informative_acknowledge'),

    path('api/mention-users/', views.mention_users_search, name='mention_users_search'),
    path('api/presence/heartbeat/', views.presence_heartbeat, name='presence_heartbeat'),
    path('api/presence/ti-online/', views.presence_ti_online, name='presence_ti_online'),
]
