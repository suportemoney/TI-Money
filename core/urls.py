from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from . import views
from . import views_diag
from . import wizard_views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('login/', LoginView.as_view(
        template_name='core/login.html',
        redirect_authenticated_user=True
    ), name='login'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path('sem-permissao/', views.sem_permissao_view, name='sem_permissao'),

    # Diagnóstico temporário do 500 (staff/superuser) — remover após o incidente
    path('diag/last-500/', views_diag.diag_last_500, name='diag_last_500'),
    path('diag/helpdesk-check/', views_diag.diag_helpdesk_check, name='diag_helpdesk_check'),

    # Gestão de usuários (somente ADMIN)
    path('usuarios/', views.UserListView.as_view(), name='user_list'),
    path('usuarios/criar/', views.UserCreateView.as_view(), name='user_create'),
    path('usuarios/<int:pk>/editar/', views.UserUpdateView.as_view(), name='user_update'),
    path('usuarios/<int:pk>/mensagem-acesso/', views.user_access_message, name='user_access_message'),
    path('usuarios/<int:pk>/desativar/', views.user_toggle_active, name='user_toggle_active'),
    path('usuarios/<int:pk>/excluir/', views.user_delete, name='user_delete'),

    # Gestão de equipes (somente ADMIN)
    path('equipes/', views.EquipeListView.as_view(), name='equipe_list'),
    path('equipes/criar/', views.EquipeCreateView.as_view(), name='equipe_create'),
    path('equipes/<int:pk>/editar/', views.EquipeUpdateView.as_view(), name='equipe_update'),
    path('equipes/<int:pk>/desativar/', views.equipe_toggle_active, name='equipe_toggle_active'),

    # Auditoria global (somente ADMIN)
    path('auditoria/', views.AuditoriaListView.as_view(), name='auditoria'),

    # Wizard flutuante de gestão (allowlist de user ids)
    path('wizard/chat/', wizard_views.wizard_chat, name='wizard_chat'),
    path('wizard/chat/limpar/', wizard_views.wizard_chat_limpar, name='wizard_chat_limpar'),
]
