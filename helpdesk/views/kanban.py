import json
import os
from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView
from django.db.models import Case, When, IntegerField, Value
from core.models import CustomUser
from core.permissions import MODULO_HELPDESK, ModuloObrigatorioMixin, requer_modulo
from django.views.decorators.http import require_POST
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.db.models import Q
from helpdesk.forms import TicketCreateForm, TicketUpdateForm
from helpdesk.models import Ticket, TicketCategory, Comment, TicketContestation, TicketUnread
from helpdesk.audit import (
    log_atribuicao,
    log_chamado_criado,
    log_comentario,
    log_contestacao,
    log_edicao,
    log_prioridade_alterada,
    log_status_alterado,
    log_transferencia,
    log_chamado_excluido,
    log_chamado_recusado,
    log_triagem_alterada,
)
from helpdesk.mentions import marcar_mencoes_vistas, processar_mencoes
from helpdesk.notifications import (
    EVENTO_COMMENT,
    EVENTO_CREATED,
    EVENTO_PRIORITY_CHANGED,
    EVENTO_STATUS_CHANGED,
    EVENTO_TRIAGE_CHANGED,
    agendar_notificacao_chamado,
    agendar_notificacao_mencoes,
    destinatarios_finalizacao,
    destinatarios_notificacao,
)
from helpdesk.queue import aplicar_posicoes_fila
from helpdesk.ticket_access import (
    filtrar_chamados_para_usuario,
    filtrar_comentarios_visiveis,
    ticket_pode_mostrar_refresh_ia,
    usuario_eh_operador_helpdesk,
    usuario_pode_acessar_chamado,
    usuario_pode_comentar_chamado,
    usuario_pode_contestar_chamado,
    usuario_pode_editar_chamado,
    usuario_pode_excluir_chamado,
    usuario_pode_gerenciar_categorias,
    usuario_pode_gerenciar_comentarios,
    usuario_pode_operar_kanban,
    usuario_pode_refresh_assistente,
    usuario_pode_transferir_chamado,
    usuario_pode_ver_comentarios_internos,
    usuario_pode_ver_quem_abriu_chamado,
    usuarios_tecnicos_para_transferencia,
)


def _agendar_assistente(
    ticket_id: int,
    *,
    comment_id: int | None = None,
    gatilho: str = 'auto',
) -> None:
    """Agenda processamento do Assistente em thread após o commit da request."""
    def _processar():
        from django.db import close_old_connections
        from integracoes.assistente_runtime import processar_assistente

        close_old_connections()
        try:
            processar_assistente(
                ticket_id,
                comment_id=comment_id,
                gatilho=gatilho,
            )
        finally:
            close_old_connections()

    def _agendar_async():
        import threading
        threading.Thread(target=_processar, daemon=True).start()

    transaction.on_commit(_agendar_async)


def adicionar_nao_lido(ticket, ator, *, somente_nao_operadores=False, usuarios_extra=None):
    """Incrementa badge não-lido. usuarios_extra sempre recebe (ex.: @menções TI↔TI)."""
    from django.db.models import F
    if somente_nao_operadores:
        destinatarios = list(destinatarios_finalizacao(ticket, ator))
    else:
        destinatarios = list(destinatarios_notificacao(ticket, ator))

    ja_incluidos = {u.pk for u in destinatarios}
    for extra in usuarios_extra or []:
        if extra and extra.pk not in ja_incluidos and extra.is_active:
            destinatarios.append(extra)
            ja_incluidos.add(extra.pk)

    for usuario in destinatarios:
        obj, created = TicketUnread.objects.get_or_create(
            ticket=ticket, user=usuario,
            defaults={'count': 1}
        )
        if not created:
            obj.count = F('count') + 1
            obj.save(update_fields=['count'])


def adicionar_nao_lido_operadores(ticket, ator):
    """Badge só para operadores TI/admin/staff (mensagens internas)."""
    from django.db.models import F, Q

    from core.models import CustomUser

    qs = CustomUser.objects.filter(is_active=True).filter(
        Q(role__in=[CustomUser.RoleChoices.ADMIN, CustomUser.RoleChoices.IT_USER])
        | Q(is_superuser=True)
        | Q(is_staff=True)
    )
    ator_id = getattr(ator, 'pk', None) if ator else None
    for usuario in qs:
        if ator_id and usuario.pk == ator_id:
            continue
        if not usuario_pode_acessar_chamado(usuario, ticket):
            continue
        obj, created = TicketUnread.objects.get_or_create(
            ticket=ticket, user=usuario,
            defaults={'count': 1},
        )
        if not created:
            obj.count = F('count') + 1
            obj.save(update_fields=['count'])


def _rotulo_prioridade(valor):
    if not valor:
        return 'Sem prioridade'
    return dict(Ticket.PriorityChoices.choices).get(valor, valor)


def _rotulo_status(valor):
    return dict(Ticket.StatusChoices.choices).get(valor, valor)


def _nome_usuario(user):
    if not user:
        return 'Não atribuído'
    return user.get_full_name() or user.username


def _autor_ultima_finalizacao(ticket):
    """Retorna autor do último comentário de finalização/recusa (fallback)."""
    comentario = (
        Comment.objects.filter(ticket=ticket, is_active=True)
        .filter(
            Q(text__startswith='Chamado finalizado') | Q(text__startswith='Chamado recusado')
        )
        .order_by('-created_at')
        .select_related('author')
        .first()
    )
    return comentario.author if comentario else None


def gerar_comentarios_alteracao(antes, depois):
    """Gera mensagens de histórico para campos alterados na edição."""
    mensagens = []
    if antes.title != depois.title:
        mensagens.append(f'Título alterado para "{depois.title}".')
    if (antes.description or '') != (depois.description or ''):
        mensagens.append('Descrição atualizada.')
    if antes.category_id != depois.category_id:
        mensagens.append(f'Categoria alterada para {depois.category.name}.')
    if antes.status != depois.status:
        mensagens.append(
            f'Status alterado de {_rotulo_status(antes.status)} para {_rotulo_status(depois.status)}.'
        )
    if antes.priority != depois.priority:
        mensagens.append(
            f'Prioridade alterada de {_rotulo_prioridade(antes.priority)} '
            f'para {_rotulo_prioridade(depois.priority)}.'
        )
    if antes.specific_category_id != depois.specific_category_id:
        antes_nome = antes.specific_category.name if antes.specific_category_id else 'Nenhuma'
        depois_nome = depois.specific_category.name if depois.specific_category_id else 'Nenhuma'
        mensagens.append(f'Triagem alterada de {antes_nome} para {depois_nome}.')
    if antes.requester_name != depois.requester_name or antes.requester_user_id != depois.requester_user_id:
        mensagens.append(f'Solicitante alterado para {depois.requester_name}.')
    if antes.assigned_to_id != depois.assigned_to_id:
        mensagens.append(
            f'Técnico transferido de {_nome_usuario(antes.assigned_to)} '
            f'para {_nome_usuario(depois.assigned_to)}.'
        )
    return mensagens


def _metadata_alteracao_ticket(antes, depois):
    """Monta metadata estruturada para edição de chamado."""
    metadata = {}
    if antes.title != depois.title:
        metadata['title'] = {'antes': antes.title, 'depois': depois.title}
    if antes.status != depois.status:
        metadata['status'] = {'antes': antes.status, 'depois': depois.status}
    if antes.priority != depois.priority:
        metadata['priority'] = {'antes': antes.priority, 'depois': depois.priority}
    if antes.specific_category_id != depois.specific_category_id:
        metadata['specific_category'] = {
            'antes': antes.specific_category_id,
            'depois': depois.specific_category_id,
        }
    if antes.category_id != depois.category_id:
        metadata['category'] = {'antes': str(antes.category), 'depois': str(depois.category)}
    if antes.requester_name != depois.requester_name:
        metadata['requester_name'] = {'antes': antes.requester_name, 'depois': depois.requester_name}
    if antes.assigned_to_id != depois.assigned_to_id:
        metadata['assigned_to'] = {
            'antes': _nome_usuario(antes.assigned_to),
            'depois': _nome_usuario(depois.assigned_to),
        }
    if (antes.description or '') != (depois.description or ''):
        metadata['description'] = {'antes': '...', 'depois': 'atualizada'}
    return metadata


def _contexto_comentarios(request, ticket):
    comments = filtrar_comentarios_visiveis(
        ticket.comments.filter(is_active=True).order_by('-created_at'),
        request.user,
    )
    return {
        'ticket': ticket,
        'comments': comments,
        'pode_gerenciar_comentarios': usuario_pode_gerenciar_comentarios(request.user),
        'pode_ver_internos': usuario_pode_ver_comentarios_internos(request.user),
        'pode_refresh_ia': (
            usuario_pode_refresh_assistente(request.user)
            and ticket_pode_mostrar_refresh_ia(ticket)
        ),
    }


def _contexto_drawer(request, ticket, edit_form=None):
    pode_editar = usuario_pode_editar_chamado(request.user, ticket)
    pode_refresh = (
        usuario_pode_refresh_assistente(request.user)
        and ticket_pode_mostrar_refresh_ia(ticket)
    )
    comments = filtrar_comentarios_visiveis(
        ticket.comments.filter(is_active=True).order_by('-created_at'),
        request.user,
    )
    ti_online = []
    if usuario_eh_operador_helpdesk(request.user):
        try:
            from helpdesk.presence import listar_ti_online_resumo
            ti_online = listar_ti_online_resumo()
        except Exception:
            ti_online = []
    return {
        'ticket': ticket,
        'comments': comments,
        'pode_ver_quem_abriu': usuario_pode_ver_quem_abriu_chamado(request.user, ticket),
        'pode_editar': pode_editar,
        'pode_comentar': usuario_pode_comentar_chamado(request.user, ticket),
        'pode_contestar': usuario_pode_contestar_chamado(request.user, ticket),
        'total_contestacoes': ticket.contestations.count(),
        'pode_excluir': usuario_pode_excluir_chamado(request.user, ticket),
        'pode_gerenciar_comentarios': usuario_pode_gerenciar_comentarios(request.user),
        'pode_ver_internos': usuario_pode_ver_comentarios_internos(request.user),
        'pode_refresh_ia': pode_refresh,
        'pode_transferir': usuario_pode_transferir_chamado(request.user),
        'tecnicos': usuarios_tecnicos_para_transferencia() if usuario_pode_transferir_chamado(request.user) else CustomUser.objects.none(),
        'edit_form': edit_form or (TicketUpdateForm(instance=ticket, user=request.user) if pode_editar else None),
        'mostrar_edicao': edit_form is not None,
        'ti_online': ti_online,
    }

class KanbanView(ModuloObrigatorioMixin, TemplateView):
    template_name = 'helpdesk/kanban.html'
    modulo_obrigatorio = MODULO_HELPDESK

    def dispatch(self, request, *args, **kwargs):
        # HTML/JS inline do helpdesk não fica preso em cache do browser
        response = super().dispatch(request, *args, **kwargs)
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Usa defaults do model — evita TypeError se deploy ficar com assinatura antiga
        Ticket.archive_old_tickets()
        
        # Apenas tickets ativos e NÃO arquivados no Kanban
        tickets = filtrar_chamados_para_usuario(
            Ticket.objects.filter(is_active=True, is_archived=False),
            self.request.user,
        ).select_related(
            'assigned_to', 'created_by', 'requester_user', 'category',
            'specific_category', 'equipe', 'tag',
        ).prefetch_related('co_authors', 'attachments')

        # Filtro de funil por tag
        tag_slug = (self.request.GET.get('tag') or '').strip()
        if tag_slug:
            tickets = tickets.filter(tag__slug=tag_slug)
        
        priority_ordering = Case(
            When(priority='URGENT', then=Value(4)),
            When(priority='HIGH', then=Value(3)),
            When(priority='MEDIUM', then=Value(2)),
            When(priority='LOW', then=Value(1)),
            default=Value(0),
            output_field=IntegerField()
        )
        
        from django.db.models import Exists, OuterRef, Subquery
        from django.db.models.functions import Coalesce
        from helpdesk.models import TicketMention

        unread_subquery = TicketUnread.objects.filter(
            ticket_id=OuterRef('pk'),
            user=self.request.user
        ).values('count')[:1]
        unread_mention_exists = TicketMention.objects.filter(
            ticket_id=OuterRef('pk'),
            user=self.request.user,
            seen_at__isnull=True,
        )

        tickets_annotated = tickets.annotate(
            priority_order=priority_ordering,
            user_unread_count=Coalesce(Subquery(unread_subquery, output_field=IntegerField()), 0),
            has_unread_mention=Exists(unread_mention_exists),
        )

        tickets_new = list(
            tickets_annotated.filter(status=Ticket.StatusChoices.NEW).order_by('-priority_order', 'created_at')
        )
        tickets_in_progress = list(
            tickets_annotated.filter(status=Ticket.StatusChoices.IN_PROGRESS).order_by('-priority_order', '-created_at')
        )
        aplicar_posicoes_fila(tickets_new, tickets_in_progress)

        context['tickets_new'] = tickets_new

        is_ti = usuario_eh_operador_helpdesk(self.request.user)
        if is_ti:
            context['untriaged_count'] = sum(1 for t in tickets_new if not t.priority)
        else:
            context['untriaged_count'] = 0

        context['tickets_in_progress'] = tickets_in_progress
        context['tickets_pending'] = tickets_annotated.filter(status=Ticket.StatusChoices.PENDING).order_by('-priority_order', '-created_at')
        context['tickets_resolved'] = tickets_annotated.filter(status=Ticket.StatusChoices.RESOLVED).order_by('-updated_at')
        context['pode_operar_kanban'] = usuario_pode_operar_kanban(self.request.user)

        from helpdesk.models import TicketSpecificCategory, TicketTag
        context['specific_categories'] = TicketSpecificCategory.objects.filter(is_active=True).order_by('name')
        context['ticket_tags'] = TicketTag.objects.order_by('nome')[:80]
        context['tag_filtro'] = tag_slug

        return context

class TicketCreateView(ModuloObrigatorioMixin, View):
    """Abre modal via HTMX (GET) e cria chamado via POST sem sair do Kanban."""
    modulo_obrigatorio = MODULO_HELPDESK
    template_name = 'helpdesk/_ticket_create_modal.html'

    def _nome_padrao(self, request):
        return request.user.get_full_name() or request.user.username

    def _contexto_modal(self, request, form, **extra):
        return {
            'form': form,
            'pode_gerenciar_categorias': usuario_pode_gerenciar_categorias(request.user),
            **extra,
        }

    def get(self, request):
        if not request.headers.get('HX-Request'):
            return redirect('helpdesk:kanban')
        form = TicketCreateForm(user=request.user, nome_solicitante_padrao=self._nome_padrao(request))
        return render(request, self.template_name, self._contexto_modal(request, form))

    def post(self, request):
        form = TicketCreateForm(
            request.POST,
            request.FILES,
            user=request.user,
            nome_solicitante_padrao=self._nome_padrao(request),
        )
        if form.is_valid():
            ticket = form.save(created_by=request.user)
            autor_nome = request.user.get_full_name() or request.user.username
            Comment.objects.create(
                ticket=ticket,
                author=request.user,
                text=f'Chamado aberto por {autor_nome}.',
            )
            log_chamado_criado(ticket, request.user)
            adicionar_nao_lido(ticket, request.user)
            agendar_notificacao_chamado(
                ticket,
                request.user,
                EVENTO_CREATED,
                f'Aberto por {_nome_usuario(request.user)}.',
            )
            _agendar_assistente(ticket.pk)
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                'ticketUpdated': True,
                'closeCreateModal': True,
            })
            return response
        return render(
            request,
            self.template_name,
            self._contexto_modal(request, form),
        )


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_category_create(request):
    """Cria categoria no modal (somente ADMIN/superuser) e atualiza o select via HTMX."""
    if not usuario_pode_gerenciar_categorias(request.user):
        return HttpResponseForbidden('Sem permissão para criar categorias.')

    nome = request.POST.get('name', '').strip()
    if not nome:
        form = TicketCreateForm(
            user=request.user,
            nome_solicitante_padrao=request.user.get_full_name() or request.user.username,
        )
        return render(request, 'helpdesk/_category_field.html', {
            'form': form,
            'pode_gerenciar_categorias': True,
            'erro_categoria': 'Informe o nome da categoria.',
            'painel_nova_categoria_aberto': True,
        })

    categoria = TicketCategory.objects.filter(name__iexact=nome).first()
    if categoria:
        if not categoria.is_active:
            categoria.is_active = True
            categoria.save(update_fields=['is_active'])
    else:
        categoria = TicketCategory.objects.create(name=nome)

    form = TicketCreateForm(
        user=request.user,
        nome_solicitante_padrao=request.user.get_full_name() or request.user.username,
        categoria_inicial=categoria.pk,
    )
    return render(request, 'helpdesk/_category_field.html', {
        'form': form,
        'pode_gerenciar_categorias': True,
    })


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_update_status(request, pk):
    if not usuario_pode_operar_kanban(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão para mover chamados'}, status=403)

    ticket = get_object_or_404(Ticket, pk=pk, is_active=True)
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return JsonResponse({'success': False, 'error': 'Sem permissão'}, status=403)
    try:
        data = json.loads(request.body)
        new_status = data.get('status')
    except json.JSONDecodeError:
        new_status = request.POST.get('status')

    if new_status in dict(Ticket.StatusChoices.choices):
        status_anterior = ticket.status
        prioridade_anterior = ticket.priority
        triagem_anterior = ticket.specific_category

        # Pendente: só TI e apenas a partir de Em Atendimento
        if new_status == Ticket.StatusChoices.PENDING:
            if status_anterior != Ticket.StatusChoices.IN_PROGRESS:
                return JsonResponse({
                    'success': False,
                    'error': 'Só é possível mover para Pendente a partir de Em Atendimento.',
                }, status=400)

        ticket.status = new_status

        priority = data.get('priority')
        if priority is not None:
            ticket.priority = priority or None

        specific_category_id = data.get('specific_category')
        if specific_category_id is not None:
            ticket.specific_category_id = specific_category_id if specific_category_id else None

        if not ticket.assigned_to and new_status != Ticket.StatusChoices.NEW:
            ticket.assigned_to = request.user
            Comment.objects.create(
                ticket=ticket,
                author=request.user,
                text=f'Chamado atribuído automaticamente a {request.user.username} (movimentado para {ticket.get_status_display()}).'
            )
            log_atribuicao(
                ticket,
                request.user,
                descricao_extra=f'(movimentado para {ticket.get_status_display()})',
            )

        # Devolver para Novos: sem técnico responsável + Assistente pode retomar
        voltou_para_novos = (
            status_anterior != Ticket.StatusChoices.NEW
            and new_status == Ticket.StatusChoices.NEW
        )
        if voltou_para_novos:
            ticket.assigned_to = None
            ticket.assistente_escalado = False

        # Sair de Resolvido/Recusado: limpa badge e motivo de recusa
        if new_status != Ticket.StatusChoices.RESOLVED:
            ticket.is_rejected = False
            ticket.rejection_reason = ''
            if status_anterior == Ticket.StatusChoices.RESOLVED:
                ticket.resolved_at = None
                ticket.resolved_by = None
                ticket.assistente_escalado = False

        ticket.save()
        if status_anterior != new_status:
            log_status_alterado(
                ticket,
                request.user,
                _rotulo_status(status_anterior),
                _rotulo_status(new_status),
            )
            agendar_notificacao_chamado(
                ticket,
                request.user,
                EVENTO_STATUS_CHANGED,
                f'Movido para {_rotulo_status(new_status)}.',
            )
        if prioridade_anterior != ticket.priority:
            log_prioridade_alterada(ticket, request.user, prioridade_anterior, ticket.priority)
            agendar_notificacao_chamado(
                ticket,
                request.user,
                EVENTO_PRIORITY_CHANGED,
                f'Prioridade: {_rotulo_prioridade(prioridade_anterior)} → {_rotulo_prioridade(ticket.priority)}.',
            )
        triagem_depois = ticket.specific_category
        if triagem_anterior != triagem_depois:
            log_triagem_alterada(ticket, request.user, triagem_anterior, triagem_depois)
            depois_nome = triagem_depois.name if triagem_depois else 'Nenhuma'
            agendar_notificacao_chamado(
                ticket,
                request.user,
                EVENTO_TRIAGE_CHANGED,
                f'Triagem: {depois_nome}.',
            )
        if voltou_para_novos:
            _agendar_assistente(ticket.pk)
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Status inválido'}, status=400)


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_finalize(request, pk):
    if not usuario_pode_operar_kanban(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão para mover chamados'}, status=403)

    ticket = get_object_or_404(Ticket, pk=pk, is_active=True)
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return JsonResponse({'success': False, 'error': 'Sem permissão'}, status=403)
        
    try:
        data = json.loads(request.body)
        action = data.get('action')
        reason = data.get('reason')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Payload inválido'}, status=400)
        
    if not reason:
        return JsonResponse({'success': False, 'error': 'Observação / Motivo é obrigatório'}, status=400)
        
    status_anterior = ticket.status
    ticket.status = Ticket.StatusChoices.RESOLVED
    
    if action == 'reject':
        ticket.is_rejected = True
        ticket.rejection_reason = reason
        log_chamado_recusado(ticket, request.user, reason)
        Comment.objects.create(
            ticket=ticket, 
            author=request.user, 
            text=f"Chamado recusado.\nMotivo: {reason}"
        )
    else:
        ticket.is_rejected = False
        # Remove a recusa anterior caso seja re-resolvido
        ticket.rejection_reason = ""
        Comment.objects.create(
            ticket=ticket, 
            author=request.user, 
            text=f"Chamado finalizado.\nObservação: {reason}"
        )
        
    if not ticket.assigned_to:
        ticket.assigned_to = request.user

    ticket.resolved_by = request.user
    ticket.save()
    # Finalizados: badge/push só para não-operadores (solicitante, criador, etc.)
    adicionar_nao_lido(ticket, request.user, somente_nao_operadores=True)

    if status_anterior != Ticket.StatusChoices.RESOLVED:
        log_status_alterado(
            ticket,
            request.user,
            _rotulo_status(status_anterior),
            _rotulo_status(Ticket.StatusChoices.RESOLVED),
        )
        agendar_notificacao_chamado(
            ticket,
            request.user,
            EVENTO_STATUS_CHANGED,
            f'Movido para {_rotulo_status(Ticket.StatusChoices.RESOLVED)}.',
            somente_nao_operadores=True,
        )

    return JsonResponse({'success': True})


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_contest(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, is_active=True)
    if not usuario_pode_contestar_chamado(request.user, ticket):
        return JsonResponse({'success': False, 'error': 'Sem permissão para contestar este chamado'}, status=403)

    try:
        data = json.loads(request.body)
        reason = (data.get('reason') or '').strip()
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Payload inválido'}, status=400)

    if not reason:
        return JsonResponse({'success': False, 'error': 'Motivo da contestação é obrigatório'}, status=400)

    finalized_by = ticket.resolved_by or _autor_ultima_finalizacao(ticket)
    finalized_at = ticket.resolved_at
    was_rejected = ticket.is_rejected
    finalized_by_nome = _nome_usuario(finalized_by)

    TicketContestation.objects.create(
        ticket=ticket,
        contested_by=request.user,
        reason=reason,
        finalized_by=finalized_by,
        finalized_at=finalized_at,
        was_rejected=was_rejected,
    )

    rotulo_finalizacao = 'recusado' if was_rejected else 'finalizado'
    data_fmt = finalized_at.strftime('%d/%m/%Y %H:%M') if finalized_at else 'data não registrada'
    Comment.objects.create(
        ticket=ticket,
        author=request.user,
        text=(
            f'Contestação do chamado.\n'
            f'Motivo: {reason}\n'
            f'(Havia sido {rotulo_finalizacao} por {finalized_by_nome} em {data_fmt})'
        ),
    )

    status_anterior = ticket.status
    ticket.status = Ticket.StatusChoices.NEW
    ticket.is_rejected = False
    ticket.rejection_reason = ''
    # Contestações voltam à fila: sem técnico + Assistente pode atuar de novo
    ticket.assigned_to = None
    ticket.assistente_escalado = False
    ticket.save()
    adicionar_nao_lido(ticket, request.user)

    log_status_alterado(
        ticket,
        request.user,
        _rotulo_status(status_anterior),
        _rotulo_status(Ticket.StatusChoices.NEW),
    )
    log_contestacao(ticket, request.user, reason, finalized_by_nome)
    agendar_notificacao_chamado(
        ticket,
        request.user,
        EVENTO_STATUS_CHANGED,
        f'Contestado — voltou para {_rotulo_status(Ticket.StatusChoices.NEW)}.',
    )
    _agendar_assistente(ticket.pk)

    response = JsonResponse({'success': True})
    response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
    return response


@requer_modulo(MODULO_HELPDESK)
def ticket_drawer(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related(
            'assigned_to', 'created_by', 'requester_user', 'category', 'tag',
        ).prefetch_related('co_authors'),
        pk=pk,
        is_active=True,
    )
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão para acessar este chamado.')
        
    deleted, _ = TicketUnread.objects.filter(ticket=ticket, user=request.user).delete()
    mencoes_vistas = marcar_mencoes_vistas(ticket, request.user)
    if deleted or mencoes_vistas:
        response = render(request, 'helpdesk/_drawer.html', _contexto_drawer(request, ticket))
        response['HX-Trigger'] = json.dumps({'ticketUpdated': True, 'ticketRead': True})
        return response

    return render(request, 'helpdesk/_drawer.html', _contexto_drawer(request, ticket))


@requer_modulo(MODULO_HELPDESK)
def ticket_edit(request, pk):
    """Exibe ou salva edição de chamado."""
    ticket = get_object_or_404(
        Ticket.objects.select_related('assigned_to', 'created_by', 'requester_user', 'category'),
        pk=pk,
        is_active=True,
    )
    if not usuario_pode_editar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão para editar chamados.')

    if request.method == 'GET':
        return render(
            request,
            'helpdesk/_drawer.html',
            _contexto_drawer(request, ticket, edit_form=TicketUpdateForm(instance=ticket, user=request.user)),
        )

    antes = Ticket.objects.select_related('assigned_to', 'category', 'specific_category').get(pk=ticket.pk)
    form = TicketUpdateForm(request.POST, instance=ticket, user=request.user)
    if form.is_valid():
        depois = form.save(commit=False)
        voltou_para_novos = (
            antes.status != Ticket.StatusChoices.NEW
            and depois.status == Ticket.StatusChoices.NEW
        )
        if voltou_para_novos:
            depois.assigned_to = None
            depois.assistente_escalado = False
        depois.save()
        form.save_m2m()
        mensagens = gerar_comentarios_alteracao(antes, depois)
        for texto in mensagens:
            Comment.objects.create(ticket=depois, author=request.user, text=texto)
        metadata = _metadata_alteracao_ticket(antes, depois)
        if metadata:
            log_edicao(depois, request.user, metadata, '; '.join(mensagens))

        if 'status' in metadata:
            msg_status = next((m for m in mensagens if m.startswith('Status')), None)
            agendar_notificacao_chamado(
                depois,
                request.user,
                EVENTO_STATUS_CHANGED,
                msg_status or f'Movido para {_rotulo_status(depois.status)}.',
            )
        if 'priority' in metadata:
            msg_prioridade = next((m for m in mensagens if m.startswith('Prioridade')), 'Prioridade alterada.')
            agendar_notificacao_chamado(
                depois,
                request.user,
                EVENTO_PRIORITY_CHANGED,
                msg_prioridade,
            )
        if 'specific_category' in metadata:
            msg_triagem = next((m for m in mensagens if m.startswith('Triagem')), 'Triagem alterada.')
            agendar_notificacao_chamado(
                depois,
                request.user,
                EVENTO_TRIAGE_CHANGED,
                msg_triagem,
            )

        adicionar_nao_lido(depois, request.user)
        if voltou_para_novos:
            _agendar_assistente(depois.pk)

        depois.refresh_from_db()
        response = render(
            request,
            'helpdesk/_drawer.html',
            _contexto_drawer(request, depois),
        )
        response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
        return response

    return render(
        request,
        'helpdesk/_drawer.html',
        _contexto_drawer(request, ticket, edit_form=form),
    )


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_transfer(request, pk):
    """Transferência rápida de técnico responsável (somente operadores)."""
    if not usuario_pode_transferir_chamado(request.user):
        return HttpResponseForbidden('Sem permissão para transferir chamados.')

    ticket = get_object_or_404(
        Ticket.objects.select_related('assigned_to'),
        pk=pk,
        is_active=True,
    )
    tecnico_id = (request.POST.get('assigned_to') or '').strip()

    # Em Novos: permitir remover o responsável residual
    if tecnico_id == '__remover__':
        if ticket.status != Ticket.StatusChoices.NEW:
            return HttpResponseForbidden('Só é possível remover o técnico em chamados Novos.')
        if not ticket.assigned_to_id:
            return render(request, 'helpdesk/_drawer.html', _contexto_drawer(request, ticket))

        anterior = ticket.assigned_to
        ticket.assigned_to = None
        ticket.assistente_escalado = False
        ticket.save(update_fields=['assigned_to', 'assistente_escalado', 'updated_at'])
        Comment.objects.create(
            ticket=ticket,
            author=request.user,
            text=(
                f'Técnico removido ({_nome_usuario(anterior)}). '
                f'Chamado sem responsável na coluna Novos.'
            ),
        )
        log_transferencia(
            ticket,
            request.user,
            _nome_usuario(anterior),
            'Nenhum',
        )
        adicionar_nao_lido(ticket, request.user)
        _agendar_assistente(ticket.pk)
        ticket.refresh_from_db()
        response = render(
            request,
            'helpdesk/_drawer.html',
            _contexto_drawer(request, ticket),
        )
        response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
        return response

    if not tecnico_id:
        return HttpResponseForbidden('Selecione um técnico.')

    tecnico = get_object_or_404(usuarios_tecnicos_para_transferencia(), pk=tecnico_id)
    if ticket.assigned_to_id == tecnico.pk:
        return render(request, 'helpdesk/_drawer.html', _contexto_drawer(request, ticket))

    anterior = ticket.assigned_to
    ticket.assigned_to = tecnico
    ticket.save(update_fields=['assigned_to', 'updated_at'])
    Comment.objects.create(
        ticket=ticket,
        author=request.user,
        text=(
            f'Técnico transferido de {_nome_usuario(anterior)} '
            f'para {_nome_usuario(tecnico)}.'
        ),
    )
    log_transferencia(
        ticket,
        request.user,
        _nome_usuario(anterior),
        _nome_usuario(tecnico),
    )
    ticket.save(update_fields=['updated_at'])
    adicionar_nao_lido(ticket, request.user)
    ticket.refresh_from_db()
    response = render(
        request,
        'helpdesk/_drawer.html',
        _contexto_drawer(request, ticket),
    )
    response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
    return response


@requer_modulo(MODULO_HELPDESK)
@require_POST
def fetch_clipboard_image(request):
    """Baixa GIF/imagem de URL colada (Win+. / Google) e devolve como arquivo."""
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    url = (payload.get('url') or request.POST.get('url') or '').strip()
    if not url:
        return JsonResponse({'error': 'URL não informada.'}, status=400)
    try:
        from helpdesk.clipboard_image import baixar_imagem_remota
        arquivo = baixar_imagem_remota(url)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    response = HttpResponse(arquivo.read(), content_type=arquivo.content_type or 'image/gif')
    response['Content-Disposition'] = f'inline; filename="{arquivo.name}"'
    response['X-Image-Filename'] = arquivo.name
    return response


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_add_comment(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, is_active=True)
    if not usuario_pode_comentar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão para comentar neste chamado.')
    text = request.POST.get('text', '').strip()
    attachment = request.FILES.get('attachment')
    quer_orientar_ia = request.POST.get('orientar_ia') in ('1', 'true', 'on', 'yes')
    quer_interno = request.POST.get('is_interno') in ('1', 'true', 'on', 'yes') or quer_orientar_ia
    is_interno = quer_interno and usuario_pode_ver_comentarios_internos(request.user)
    if quer_orientar_ia and is_interno and text:
        from integracoes.memoria_chat import ORIENTACAO_PREFIXO
        if not text.upper().startswith(ORIENTACAO_PREFIXO):
            text = f'{ORIENTACAO_PREFIXO} {text}'
    if text or attachment:
        if attachment:
            from django.core.exceptions import ValidationError
            from helpdesk.models import validate_image_attachment
            try:
                validate_image_attachment(attachment)
            except ValidationError as e:
                return HttpResponse(e.messages[0], status=400)
        comment = Comment.objects.create(
            ticket=ticket,
            author=request.user,
            text=text,
            attachment=attachment,
            is_interno=is_interno,
        )
        # Menções @: só operadores; concede co_authors + notifica (inclui TI↔TI)
        mencionados = []
        if text and not is_interno:
            mencionados = processar_mencoes(ticket, comment, request.user) or []
        elif text and is_interno:
            # Menções em interno só entre quem vê interno
            mencionados = processar_mencoes(ticket, comment, request.user) or []

        if text:
            meta = {'is_interno': is_interno}
            if mencionados:
                meta['mention_user_ids'] = [u.pk for u in mencionados]
                meta['acao_ui'] = 'MENTION'
            log_comentario(ticket, request.user, text, metadata=meta or None)
        else:
            log_comentario(
                ticket,
                request.user,
                'Anexou uma imagem' + (' (interno).' if is_interno else '.'),
                metadata={'is_interno': is_interno},
            )

        ticket.save(update_fields=['updated_at'])

        # Resposta do solicitante/criador cancela follow-up de espera do Assistente
        from helpdesk.assistente_followup import (
            limpar_espera_assistente,
            usuario_e_solicitante_ou_criador,
        )
        if (
            not is_interno
            and usuario_e_solicitante_ou_criador(ticket, request.user)
        ):
            limpar_espera_assistente(ticket)

        preview = text[:120] if text else 'Nova imagem anexada.'
        if is_interno:
            adicionar_nao_lido_operadores(ticket, request.user)
            # Push só para mencionados (TI) — solicitante não recebe
            if mencionados:
                agendar_notificacao_mencoes(ticket, mencionados, f'[Interno] {preview}')
        else:
            adicionar_nao_lido(ticket, request.user, usuarios_extra=mencionados)
            agendar_notificacao_chamado(ticket, request.user, EVENTO_COMMENT, preview)
            if mencionados:
                agendar_notificacao_mencoes(ticket, mencionados, preview)

        # Assistente: responde a solicitante, orientação interna ou @assistente
        from helpdesk.mentions import texto_menciona_assistente
        mencionou_assistente = bool(text and texto_menciona_assistente(text))

        if mencionou_assistente:
            gatilho = 'mencao'
            if quer_orientar_ia:
                gatilho = 'orientacao'
            _agendar_assistente(ticket.pk, comment_id=comment.pk, gatilho=gatilho)
            if quer_orientar_ia and text and is_interno:
                try:
                    from integracoes.memoria_chat import aprender_de_orientacao
                    aprender_de_orientacao(ticket, text, autor=request.user)
                except Exception:
                    pass
        elif is_interno and usuario_pode_ver_comentarios_internos(request.user):
            gatilho = 'orientacao' if quer_orientar_ia else 'continuacao'
            _agendar_assistente(ticket.pk, comment_id=comment.pk, gatilho=gatilho)
            if quer_orientar_ia and text:
                try:
                    from integracoes.memoria_chat import aprender_de_orientacao
                    aprender_de_orientacao(ticket, text, autor=request.user)
                except Exception:
                    # Não bloqueia o comentário se o aprendizado falhar
                    pass
        elif not usuario_eh_operador_helpdesk(request.user) and not getattr(comment, 'is_assistente', False):
            _agendar_assistente(ticket.pk, comment_id=comment.pk, gatilho='auto')

    response = render(request, 'helpdesk/_comments_list.html', _contexto_comentarios(request, ticket))
    response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
    return response


@requer_modulo(MODULO_HELPDESK)
def mention_users_search(request):
    """Autocomplete de @ — @assistente para todos; usernames só para operadores."""
    q = (request.GET.get('q') or '').strip().lstrip('@')
    results = []
    if not q or 'assistente'.startswith(q.lower()) or 'assist'.startswith(q.lower()):
        results.append({
            'username': 'assistente',
            'label': 'Assistente (IA)',
        })
    if usuario_eh_operador_helpdesk(request.user):
        qs = CustomUser.objects.filter(is_active=True).exclude(pk=request.user.pk)
        if q:
            qs = qs.filter(
                Q(username__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
            )
        for u in qs.order_by('username')[:15]:
            results.append({
                'username': u.username,
                'label': u.get_full_name() or u.username,
            })
    return JsonResponse({'results': results[:16]})


@requer_modulo(MODULO_HELPDESK)
def ticket_comments(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, is_active=True)
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão.')

    deleted, _ = TicketUnread.objects.filter(ticket=ticket, user=request.user).delete()
    mencoes_vistas = marcar_mencoes_vistas(ticket, request.user)

    response = render(request, 'helpdesk/_comments_list.html', _contexto_comentarios(request, ticket))
    if deleted or mencoes_vistas:
        response['HX-Trigger'] = json.dumps({'ticketRead': True})
    return response


@requer_modulo(MODULO_HELPDESK)
def comment_edit(request, ticket_pk, comment_pk):
    """Edita texto de comentário/mensagem — só is_staff / is_superuser."""
    ticket = get_object_or_404(Ticket, pk=ticket_pk, is_active=True)
    comment = get_object_or_404(Comment, pk=comment_pk, ticket=ticket, is_active=True)
    if not usuario_pode_gerenciar_comentarios(request.user):
        return HttpResponseForbidden('Sem permissão para editar mensagens.')
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão.')

    if request.method == 'GET':
        return render(
            request,
            'helpdesk/_comment_edit_form.html',
            {'ticket': ticket, 'comment': comment},
        )

    novo = (request.POST.get('text') or '').strip()
    if not novo:
        return HttpResponse('Informe o texto da mensagem.', status=400)

    antes = comment.text
    comment.text = novo
    comment.save(update_fields=['text'])
    try:
        log_edicao(
            ticket,
            request.user,
            {'comment_id': comment.pk, 'text': {'antes': (antes or '')[:200], 'depois': novo[:200]}},
            f'Mensagem #{comment.pk} editada no chamado.',
        )
    except Exception:
        pass

    response = render(request, 'helpdesk/_comments_list.html', _contexto_comentarios(request, ticket))
    response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
    return response


@requer_modulo(MODULO_HELPDESK)
@require_POST
def comment_delete(request, ticket_pk, comment_pk):
    """Exclui (soft) comentário/mensagem — só is_staff / is_superuser."""
    ticket = get_object_or_404(Ticket, pk=ticket_pk, is_active=True)
    comment = get_object_or_404(Comment, pk=comment_pk, ticket=ticket, is_active=True)
    if not usuario_pode_gerenciar_comentarios(request.user):
        return HttpResponseForbidden('Sem permissão para excluir mensagens.')
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão.')

    comment.is_active = False
    comment.save(update_fields=['is_active'])
    try:
        log_edicao(
            ticket,
            request.user,
            {'comment_id': comment.pk, 'acao': 'excluido'},
            f'Mensagem #{comment.pk} excluída do chamado.',
        )
    except Exception:
        pass

    response = render(request, 'helpdesk/_comments_list.html', _contexto_comentarios(request, ticket))
    response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
    return response


class KanbanBoardPartialView(KanbanView):
    """Retorna apenas o HTML do quadro para ser injetado via HTMX no evento SSE."""
    template_name = 'helpdesk/_kanban_board.html'


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_delete(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, is_active=True)
    if not usuario_pode_excluir_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão para excluir chamados.')
    ticket.is_active = False
    ticket.save()
    log_chamado_excluido(ticket, request.user)
    
    if request.headers.get('HX-Request'):
        response = HttpResponse("")
        response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
        return response
        
    return redirect('helpdesk:kanban')


@requer_modulo(MODULO_HELPDESK)
@require_POST
def ticket_refresh_assistente(request, pk):
    """
    Reaciona o Assistente em chamado Novos sem mensagens ativas da IA
    (ex.: histórico do Assistente foi apagado). Usa o histórico visível.
    """
    ticket = get_object_or_404(
        Ticket.objects.select_related('assigned_to', 'requester_user', 'created_by'),
        pk=pk,
        is_active=True,
    )
    if not usuario_pode_refresh_assistente(request.user):
        return HttpResponseForbidden('Sem permissão para reativar o Assistente.')
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão para acessar este chamado.')
    if ticket.status != Ticket.StatusChoices.NEW:
        return HttpResponseForbidden('Refresh IA só está disponível em chamados Novos.')
    if not ticket_pode_mostrar_refresh_ia(ticket):
        return HttpResponseForbidden(
            'Já existem mensagens do Assistente no histórico, ou o chamado não está em Novos.'
        )

    # Libera flag que impede a IA de falar de novo
    ticket.assistente_escalado = False
    ticket.save(update_fields=['assistente_escalado', 'updated_at'])

    autor = request.user.get_full_name() or request.user.username
    Comment.objects.create(
        ticket=ticket,
        author=request.user,
        text=f'Assistente reacionado ({autor}) — Refresh IA.',
        is_assistente=False,
    )
    try:
        log_edicao(
            ticket,
            request.user,
            {'acao': 'refresh_ia'},
            f'Assistente reacionado no chamado #{ticket.pk} (Refresh IA).',
        )
    except Exception:
        pass

    _agendar_assistente(ticket.pk)
    ticket.refresh_from_db()
    response = render(request, 'helpdesk/_drawer.html', _contexto_drawer(request, ticket))
    response['HX-Trigger'] = json.dumps({'ticketUpdated': True})
    return response


@requer_modulo(MODULO_HELPDESK)
def ticket_attachments(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, is_active=True)
    if not usuario_pode_acessar_chamado(request.user, ticket):
        return HttpResponseForbidden('Sem permissão para visualizar este chamado.')
        
    attachments = []
    comment_id = request.GET.get('comment_id')

    class MockAttachment:
        def __init__(self, file, created_at):
            self.file = file
            self.file_name = file.name.split('/')[-1] if file and file.name else 'anexo_comentario'
            self.created_at = created_at
        
        @property
        def is_image(self):
            ext = os.path.splitext(self.file.name)[1].lower() if self.file and self.file.name else ''
            return ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']

        @property
        def is_audio(self):
            ext = os.path.splitext(self.file.name)[1].lower() if self.file and self.file.name else ''
            return ext in ['.mp3', '.wav', '.ogg', '.m4a']

        @property
        def extension(self):
            ext = os.path.splitext(self.file.name)[1].lower() if self.file and self.file.name else ''
            return ext[1:] if ext else ''

    if comment_id:
        from helpdesk.models import Comment
        comment = get_object_or_404(Comment, pk=comment_id, ticket=ticket)
        # Anexo de mensagem interna: solicitante não acessa
        if comment.is_interno and not usuario_pode_ver_comentarios_internos(request.user):
            return HttpResponseForbidden('Sem permissão para ver este anexo.')
        if comment.attachment:
            attachments = [MockAttachment(comment.attachment, comment.created_at)]
    else:
        tipo = request.GET.get('type')
        qs = ticket.attachments.all().order_by('-created_at')
        if tipo == 'images':
            attachments = [att for att in qs if att.is_image]
        elif tipo == 'audios':
            attachments = [att for att in qs if att.is_audio]
        elif tipo == 'docs':
            attachments = [att for att in qs if not att.is_image and not att.is_audio]
        else:
            attachments = list(qs)
            
    has_images = any(a.is_image for a in attachments)
    
    return render(request, 'helpdesk/_attachments_modal.html', {
        'ticket': ticket,
        'attachments': attachments,
        'has_images': has_images,
    })
