from django.urls import path

from discador import views

app_name = 'discador'

urlpatterns = [
    path('', views.JoyTecDashboardView.as_view(), name='joytec'),
    path('joytec/', views.JoyTecDashboardView.as_view(), name='joytec_alias'),

    path('acessos/status-moneyconsig/', views.acessos_status_moneyconsig, name='acessos_status_moneyconsig'),
    path('acessos/create/', views.AcessoCreateView.as_view(), name='acesso_create'),
    path('acessos/<int:pk>/update/', views.AcessoUpdateView.as_view(), name='acesso_update'),
    path('acessos/<int:pk>/delete/', views.AcessoDeleteView.as_view(), name='acesso_delete'),

    path('ramais/create/', views.RamalCreateView.as_view(), name='ramal_create'),
    path('ramais/<int:pk>/update/', views.RamalUpdateView.as_view(), name='ramal_update'),
    path('ramais/<int:pk>/delete/', views.RamalDeleteView.as_view(), name='ramal_delete'),

    path('campanhas/create/', views.CampanhaCreateView.as_view(), name='campanha_create'),
    path('campanhas/<int:pk>/update/', views.CampanhaUpdateView.as_view(), name='campanha_update'),
    path('campanhas/<int:pk>/delete/', views.CampanhaDeleteView.as_view(), name='campanha_delete'),

    path('contrato/update/', views.ContratoUpdateView.as_view(), name='contrato_update'),
]
