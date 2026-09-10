from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.permissions import MODULO_HELPDESK, ModuloObrigatorioMixin, requer_modulo, resposta_sem_permissao
from helpdesk.forms import HelpdeskRestrictionGroupForm
from helpdesk.models import HelpdeskRestrictionGroup
from helpdesk.ticket_access import usuario_pode_gerenciar_configuracoes


def _form_grupo(grupo, data=None):
    kwargs = {'instance': grupo, 'auto_id': f'id_g{grupo.pk}_%s'}
    if data is not None:
        return HelpdeskRestrictionGroupForm(data, **kwargs)
    return HelpdeskRestrictionGroupForm(**kwargs)


def _itens_grupos():
    """Lista de grupos com formulário bound à instância."""
    grupos = HelpdeskRestrictionGroup.objects.all().order_by('pk')
    return [{'grupo': grupo, 'form': _form_grupo(grupo)} for grupo in grupos]


def _resposta_lista(request, mensagem=''):
    return render(request, 'helpdesk/_restriction_groups.html', {
        'itens': _itens_grupos(),
        'mensagem': mensagem,
    })


def _resposta_card(request, grupo, form=None, mensagem='', erros=None):
    return render(request, 'helpdesk/_restriction_group_card.html', {
        'grupo': grupo,
        'form': form or _form_grupo(grupo),
        'mensagem': mensagem,
        'erros': erros or [],
    })


class HelpdeskSettingsView(ModuloObrigatorioMixin, TemplateView):
    template_name = 'helpdesk/settings.html'
    modulo_obrigatorio = MODULO_HELPDESK

    def dispatch(self, request, *args, **kwargs):
        if not usuario_pode_gerenciar_configuracoes(request.user):
            return resposta_sem_permissao(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['itens'] = _itens_grupos()
        return context


@requer_modulo(MODULO_HELPDESK)
@require_POST
def restriction_group_create(request):
    if not usuario_pode_gerenciar_configuracoes(request.user):
        return resposta_sem_permissao(request)
    total = HelpdeskRestrictionGroup.objects.count() + 1
    HelpdeskRestrictionGroup.objects.create(nome=f'Grupo {total}', ativo=True)
    return _resposta_lista(request, mensagem='Grupo criado. Marque usuários e visualizadores e salve.')


@requer_modulo(MODULO_HELPDESK)
@require_POST
def restriction_group_update(request, pk):
    if not usuario_pode_gerenciar_configuracoes(request.user):
        return resposta_sem_permissao(request)
    grupo = get_object_or_404(HelpdeskRestrictionGroup, pk=pk)
    form = HelpdeskRestrictionGroupForm(request.POST, instance=grupo, auto_id=f'id_g{grupo.pk}_%s')
    if not form.is_valid():
        erros = [f'{campo}: {", ".join(msgs)}' for campo, msgs in form.errors.items()]
        return _resposta_card(request, grupo, form=form, erros=erros)
    form.save()
    grupo.refresh_from_db()
    return _resposta_card(request, grupo, mensagem='Grupo salvo.')


@requer_modulo(MODULO_HELPDESK)
@require_POST
def restriction_group_delete(request, pk):
    if not usuario_pode_gerenciar_configuracoes(request.user):
        return resposta_sem_permissao(request)
    grupo = get_object_or_404(HelpdeskRestrictionGroup, pk=pk)
    grupo.delete()
    return _resposta_lista(request, mensagem='Grupo excluído.')
