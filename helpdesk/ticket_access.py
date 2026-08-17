"""
Regras de visibilidade e permissões de chamados por papel do usuário.
Matriz completa documentada em helpdesk/DOCUMENTACAO.md e .agent/skills/rbac-helpdesk/SKILL.md.
"""
from typing import Optional

from django.db.models import Q, QuerySet

from core.models import CustomUser

# Chamados em que este usuário é criador OU solicitante: só o TI abaixo vê (além dos stakeholders).
CRIADOR_CHAMADOS_RESTRITOS_ID = 25
TI_VISUALIZADOR_EXCLUSIVO_ID = 2


def _role(user) -> Optional[str]:
    if not user or not user.is_authenticated:
        return None
    return getattr(user, 'role', None)


def _eh_ti_visualizador_exclusivo(user) -> bool:
    return bool(user and getattr(user, 'pk', None) == TI_VISUALIZADOR_EXCLUSIVO_ID)


def _q_chamado_restrito() -> Q:
    """Restrito se o user 25 for quem abriu ou o solicitante vinculado."""
    return (
        Q(created_by_id=CRIADOR_CHAMADOS_RESTRITOS_ID)
        | Q(requester_user_id=CRIADOR_CHAMADOS_RESTRITOS_ID)
    )


def _chamado_eh_restrito(ticket) -> bool:
    return (
        ticket.created_by_id == CRIADOR_CHAMADOS_RESTRITOS_ID
        or ticket.requester_user_id == CRIADOR_CHAMADOS_RESTRITOS_ID
    )


def _filtro_excluir_chamados_restritos_para(user) -> Q:
    """
    Remove chamados restritos (criador ou solicitante = user 25),
    exceto se o user for stakeholder (criador, solicitante ou co-autor).
    O TI exclusivo não usa este filtro.
    """
    return (
        ~_q_chamado_restrito()
        | Q(created_by=user)
        | Q(requester_user=user)
        | Q(co_authors=user)
    )


def _aplicar_restricao_criador_25(queryset: QuerySet, user) -> QuerySet:
    """Chamados do user 25 (criador/solicitante): só TI id 2; stakeholders mantêm acesso."""
    if _eh_ti_visualizador_exclusivo(user):
        return queryset
    ids = queryset.filter(_filtro_excluir_chamados_restritos_para(user)).values_list('pk', flat=True).distinct()
    return queryset.filter(pk__in=ids)


def usuario_eh_operador_helpdesk(user) -> bool:
    """Membro TI, Administrador ou superuser — operações completas no helpdesk."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _role(user) in (CustomUser.RoleChoices.ADMIN, CustomUser.RoleChoices.IT_USER)


def usuario_pode_acessar_dashboard_e_historico(user) -> bool:
    """Dashboard e histórico: Membro TI, Administrador ou superuser."""
    return usuario_eh_operador_helpdesk(user)


def usuario_pode_ver_arquivados(user) -> bool:
    """Arquivados visíveis apenas no histórico para operadores helpdesk."""
    return usuario_eh_operador_helpdesk(user)


def usuario_pode_gerenciar_categorias(user) -> bool:
    return usuario_eh_operador_helpdesk(user)


def usuario_pode_definir_prioridade(user) -> bool:
    return usuario_eh_operador_helpdesk(user)


def usuario_pode_transferir_chamado(user) -> bool:
    return usuario_eh_operador_helpdesk(user)


def usuario_pode_operar_kanban(user) -> bool:
    """Somente operadores helpdesk movem cards no Kanban."""
    return usuario_eh_operador_helpdesk(user)


def usuario_ve_todos_chamados(user) -> bool:
    """Visão global no Kanban: operadores e superuser."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _role(user) in (
        CustomUser.RoleChoices.ADMIN,
        CustomUser.RoleChoices.IT_USER,
    )


def usuarios_solicitantes_equipe(user) -> QuerySet:
    """Membros ativos das equipes do usuário (inclui o próprio)."""
    if not user or not user.is_authenticated:
        return CustomUser.objects.none()
    if not user.equipes.exists():
        return CustomUser.objects.filter(pk=user.pk, is_active=True)
    return CustomUser.objects.filter(
        is_active=True,
        equipes__in=user.equipes.all(),
    ).distinct().order_by('first_name', 'last_name', 'username')


def buscar_membro_equipe_por_nome(user, nome: str):
    """Busca membro da equipe pelo nome completo ou username (case-insensitive)."""
    nome = (nome or '').strip()
    if not nome or not user:
        return None
    nome_lower = nome.lower()
    for membro in usuarios_solicitantes_equipe(user).exclude(pk=user.pk):
        nome_completo = (membro.get_full_name() or '').strip()
        if nome_completo.lower() == nome_lower or membro.username.lower() == nome_lower:
            return membro
    return None


def usuarios_tecnicos_para_transferencia() -> QuerySet:
    """Técnicos disponíveis para transferência: ADMIN e IT_USER ativos."""
    return CustomUser.objects.filter(
        is_active=True,
        role__in=[CustomUser.RoleChoices.ADMIN, CustomUser.RoleChoices.IT_USER],
    ).order_by('first_name', 'last_name', 'username')


def _filtro_chamados_proprios(user) -> Q:
    """Chamados criados pelo usuário, onde é solicitante ou co-autor."""
    nome = (user.get_full_name() or user.username).strip()
    return (
        Q(created_by=user)
        | Q(requester_user=user)
        | Q(co_authors=user)
        | Q(created_by__isnull=True, requester_name__iexact=nome)
        | Q(created_by__isnull=True, requester_name__iexact=user.username)
    )


def _filtro_chamados_equipe(user) -> Q:
    """Chamados vinculados às equipes do usuário."""
    equipes_user = user.equipes.all()
    if not equipes_user.exists():
        return Q(pk__in=[])
    return (
        Q(equipe__in=equipes_user)
        | Q(created_by__equipes__in=equipes_user)
        | Q(requester_user__equipes__in=equipes_user)
    )


def filtrar_chamados_para_usuario(queryset: QuerySet, user) -> QuerySet:
    """Restringe queryset conforme papel do usuário."""
    if usuario_ve_todos_chamados(user):
        return _aplicar_restricao_criador_25(queryset, user)
    role = _role(user)
    if role in (CustomUser.RoleChoices.TEAM_LEADER, CustomUser.RoleChoices.SUPERVISOR):
        filtro = _filtro_chamados_equipe(user) | _filtro_chamados_proprios(user)
    else:
        filtro = _filtro_chamados_proprios(user)
    # distinct() + order_by com M2M quebra no PostgreSQL — filtra por PK
    ids = queryset.filter(filtro).values_list('pk', flat=True).distinct()
    filtrado = queryset.filter(pk__in=ids)
    return _aplicar_restricao_criador_25(filtrado, user)


def usuario_pode_acessar_chamado(user, ticket) -> bool:
    """Verifica se o usuário pode visualizar um chamado específico."""
    if not user or not user.is_authenticated:
        return False

    # Chamados do user 25 (criador ou solicitante): somente TI id 2 ou stakeholders
    if _chamado_eh_restrito(ticket):
        if _eh_ti_visualizador_exclusivo(user):
            return True
        return usuario_eh_autor_ou_coautor(user, ticket)

    if usuario_ve_todos_chamados(user):
        return True
    qs = type(ticket).objects.filter(pk=ticket.pk)
    return filtrar_chamados_para_usuario(qs, user).exists()


def usuario_eh_autor_ou_coautor(user, ticket) -> bool:
    """Autor (quem abriu), solicitante vinculado ou co-autor."""
    if ticket.created_by_id == user.pk:
        return True
    if ticket.requester_user_id == user.pk:
        return True
    return ticket.co_authors.filter(pk=user.pk).exists()


def usuario_pode_comentar_chamado(user, ticket) -> bool:
    """Regras de comentário por papel."""
    if not usuario_pode_acessar_chamado(user, ticket):
        return False
    if (
        ticket.status == ticket.StatusChoices.RESOLVED
        and not usuario_eh_operador_helpdesk(user)
    ):
        return False
    if usuario_eh_operador_helpdesk(user):
        return True
    role = _role(user)
    if role == CustomUser.RoleChoices.SUPERVISOR:
        return True
    if role in (CustomUser.RoleChoices.TEAM_LEADER, CustomUser.RoleChoices.MULTIPLIER):
        return usuario_eh_autor_ou_coautor(user, ticket)
    return True


def usuario_pode_contestar_chamado(user, ticket) -> bool:
    """Solicitante vinculado pode contestar chamado finalizado (não arquivado)."""
    if not usuario_pode_acessar_chamado(user, ticket):
        return False
    if usuario_eh_operador_helpdesk(user):
        return False
    if not ticket.is_active or ticket.is_archived:
        return False
    if ticket.status != ticket.StatusChoices.RESOLVED:
        return False
    return usuario_eh_autor_ou_coautor(user, ticket)


def usuario_eh_staff_ou_superuser(user) -> bool:
    """Staff Django ou superuser — editar/excluir chamado e gerenciar mensagens do chat."""
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_superuser or user.is_staff)


def usuario_pode_gerenciar_comentarios(user) -> bool:
    """
    Editar/excluir mensagens no histórico:
    is_staff, is_superuser ou operador helpdesk (ADMIN / IT_USER).
    """
    if usuario_eh_staff_ou_superuser(user):
        return True
    return usuario_eh_operador_helpdesk(user)


def usuario_pode_refresh_assistente(user) -> bool:
    """Botão Refresh IA — mesmo público do menu de mensagens."""
    return usuario_pode_gerenciar_comentarios(user)


def usuario_pode_ver_comentarios_internos(user) -> bool:
    """Histórico interno: staff, superuser ou operador TI."""
    return usuario_pode_gerenciar_comentarios(user)


def usuario_pode_gerenciar_informativos(user) -> bool:
    """Arquivar/prorrogar comunicados da Central: TI/staff/superuser."""
    return usuario_pode_gerenciar_comentarios(user)


def filtrar_comentarios_visiveis(queryset, user):
    """Remove mensagens internas do queryset se o usuário não puder vê-las."""
    if usuario_pode_ver_comentarios_internos(user):
        return queryset
    return queryset.filter(is_interno=False)


def ticket_sem_mensagem_assistente_ativa(ticket) -> bool:
    """True se não há comentário público ativo do Assistente."""
    from helpdesk.models import Comment

    return not Comment.objects.filter(
        ticket=ticket,
        is_active=True,
        is_assistente=True,
        is_interno=False,
    ).exists()


def ticket_pode_mostrar_refresh_ia(ticket) -> bool:
    """Refresh IA só em Novos e sem mensagens ativas do Assistente."""
    if not ticket or ticket.status != ticket.StatusChoices.NEW:
        return False
    if not ticket.is_active or ticket.is_archived:
        return False
    return ticket_sem_mensagem_assistente_ativa(ticket)


def usuario_pode_editar_chamado(user, ticket=None) -> bool:
    """Somente is_staff, is_superuser ou operador helpdesk veem/usam Editar no chamado."""
    if not (usuario_eh_staff_ou_superuser(user) or usuario_eh_operador_helpdesk(user)):
        return False
    if ticket is not None and not usuario_pode_acessar_chamado(user, ticket):
        return False
    return True


def usuario_pode_excluir_chamado(user, ticket=None) -> bool:
    """Somente is_staff, is_superuser ou operador helpdesk veem/usam Excluir no chamado."""
    if not (usuario_eh_staff_ou_superuser(user) or usuario_eh_operador_helpdesk(user)):
        return False
    if ticket is not None and not usuario_pode_acessar_chamado(user, ticket):
        return False
    return True


def usuario_pode_ver_quem_abriu_chamado(user, ticket) -> bool:
    """Solicitante vinculado só vê quem abriu se for ele mesmo; perfis avançados sempre veem."""
    if usuario_eh_operador_helpdesk(user):
        return True
    role = _role(user)
    if role in (CustomUser.RoleChoices.SUPERVISOR, CustomUser.RoleChoices.TEAM_LEADER):
        return True
    if ticket.created_by_id == user.pk:
        return True
    if ticket.requester_user_id == user.pk and ticket.created_by_id == user.pk:
        return True
    if usuario_eh_autor_ou_coautor(user, ticket):
        return True
    if ticket.requester_user_id is None and ticket.created_by_id is None:
        return True
    return False


def filtrar_mensagens_informativas(user):
    """
    Filtro de mensagens da Central Informativa.
    - Operadores TI / staff: veem vigentes e arquivados.
    - Usuários normais: só não arquivados e ainda dentro da validade.
    - Demais regras: TI vê tudo da TI; líderes veem equipe; etc.
    """
    from django.utils import timezone

    from helpdesk.models import InformativeMessage

    qs = InformativeMessage.objects.all()
    agora = timezone.now()

    if not usuario_pode_gerenciar_informativos(user):
        qs = qs.filter(arquivado=False, ativo=True).filter(
            Q(valido_ate__isnull=True) | Q(valido_ate__gte=agora),
        )

    if usuario_eh_operador_helpdesk(user) or usuario_pode_gerenciar_informativos(user):
        return qs.order_by('created_at')

    role = _role(user)

    # Todos veem mensagens criadas por operadores TI (ADMIN, IT_USER, superuser)
    q_ti = Q(created_by__role__in=[CustomUser.RoleChoices.ADMIN, CustomUser.RoleChoices.IT_USER]) | Q(created_by__is_superuser=True)

    if role in (CustomUser.RoleChoices.TEAM_LEADER, CustomUser.RoleChoices.SUPERVISOR):
        equipes_user = user.equipes.all()
        q_equipe = Q(created_by__equipes__in=equipes_user)
        q_propria = Q(created_by=user)
        filtro_usuario = q_equipe | q_propria
    else:
        filtro_usuario = Q(created_by=user)

    ids = qs.filter(q_ti | filtro_usuario).values_list('pk', flat=True).distinct()
    return qs.filter(pk__in=ids).order_by('created_at')
