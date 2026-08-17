"""Flags de permissão e versão de assets do helpdesk nos templates."""
from django.conf import settings


def helpdesk_permissoes(request):
    """Nunca deve derrubar a página — qualquer falha retorna defaults seguros."""
    asset_v = getattr(settings, 'HELPDESK_FRONTEND_VERSION', '1')
    seguro = {
        'helpdesk_asset_v': asset_v,
        'letreiro_mensagens': [],
        'pode_operar_kanban': False,
        'pode_acessar_dashboard_helpdesk': False,
        'eh_operador_helpdesk': False,
        'pode_gerenciar_comentarios': False,
        'pode_gerenciar_informativos': False,
    }
    try:
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return seguro

        from helpdesk.ticket_access import (
            usuario_eh_operador_helpdesk,
            usuario_pode_acessar_dashboard_e_historico,
            usuario_pode_gerenciar_comentarios,
            usuario_pode_gerenciar_informativos,
            usuario_pode_operar_kanban,
        )

        letreiro = []
        try:
            from helpdesk.informative_services import mensagens_letreiro_vigentes
            letreiro = list(mensagens_letreiro_vigentes())
        except Exception:
            letreiro = []

        return {
            'helpdesk_asset_v': asset_v,
            'pode_operar_kanban': usuario_pode_operar_kanban(user),
            'pode_acessar_dashboard_helpdesk': usuario_pode_acessar_dashboard_e_historico(user),
            'eh_operador_helpdesk': usuario_eh_operador_helpdesk(user),
            'pode_gerenciar_comentarios': usuario_pode_gerenciar_comentarios(user),
            'pode_gerenciar_informativos': usuario_pode_gerenciar_informativos(user),
            'letreiro_mensagens': letreiro,
        }
    except Exception:
        return seguro
