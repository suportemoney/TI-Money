from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView

from core.audit import logs_do_modulo
from core.permissions import MODULO_DISCADOR, ModuloObrigatorioMixin, requer_modulo
from discador.audit import (
    log_acesso_atualizado,
    log_acesso_criado,
    log_acesso_excluido,
    log_campanha_atualizada,
    log_campanha_criada,
    log_campanha_excluida,
    log_contrato_atualizado,
    log_ramal_atualizado,
    log_ramal_criado,
    log_ramal_excluido,
)
from discador.forms import AcessoDiscadorForm, CampanhaForm, ContratoForm, RamalForm
from discador.htmx import DiscadorModalMixin
from discador.models import AcessoDiscador, Campanha, Discador, Ramal, RamalAtribuicaoHistorico
from discador.services import (
    atualizar_acesso,
    atualizar_campanha,
    atualizar_contrato,
    atualizar_ramal,
    criar_acesso,
    criar_campanha,
    criar_ramal,
    dashboard_custos,
    excluir_acesso,
    excluir_campanha,
    excluir_ramal,
    get_or_create_joytec,
    ids_acessos_inativos_moneyconsig,
    kpis_licencas,
)


def _msg_validacao(exc: ValidationError) -> str:
    if hasattr(exc, 'messages'):
        return '; '.join(str(m) for m in exc.messages)
    return str(exc)


class _DiscadorMixin(ModuloObrigatorioMixin):
    modulo_obrigatorio = MODULO_DISCADOR


class JoyTecDashboardView(_DiscadorMixin, TemplateView):
    """Página JoyTec com abas: Dashboard, Acessos, Ramais, Campanhas, Contrato."""

    template_name = 'discador/joytec.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        discador = get_or_create_joytec()
        context['discador'] = discador
        context['kpis'] = kpis_licencas(discador)
        context['dashboard'] = dashboard_custos(discador)

        acessos = (
            AcessoDiscador.objects.filter(discador=discador)
            .select_related('ramal', 'campanha', 'titular_user')
            .order_by('titular_nome', 'login_discador')
        )
        filtro_status = self.request.GET.get('status', '')
        filtro_tipo = self.request.GET.get('tipo', '')
        if filtro_status:
            acessos = acessos.filter(status=filtro_status)
        if filtro_tipo:
            acessos = acessos.filter(tipo=filtro_tipo)

        context['acessos'] = acessos
        context['filtro_status'] = filtro_status
        context['filtro_tipo'] = filtro_tipo
        context['status_choices'] = Ramal.StatusChoices.choices
        context['tipo_choices'] = AcessoDiscador.TipoChoices.choices

        context['ramais'] = (
            Ramal.objects.filter(discador=discador)
            .select_related()
            .order_by('numero')
        )
        context['campanhas'] = (
            Campanha.objects.filter(discador=discador).order_by('nome')
        )
        context['historico_contrato'] = discador.historico_contrato.select_related(
            'registered_by'
        )[:30]
        context['historico_atribuicoes'] = (
            RamalAtribuicaoHistorico.objects.filter(discador=discador)
            .select_related('ramal', 'registered_by')
            .order_by('-timestamp')[:50]
        )
        context['contrato_form'] = ContratoForm(instance=discador)
        context['audit_logs'] = logs_do_modulo(MODULO_DISCADOR, limite=40)
        context['audit_titulo'] = 'Registro de auditoria — Discadores / JoyTec'
        return context


@require_GET
@requer_modulo(MODULO_DISCADOR)
def acessos_status_moneyconsig(request):
    """JSON com IDs de acessos cujo titular está inativo no MoneyConsig."""
    discador = get_or_create_joytec()
    acessos = (
        AcessoDiscador.objects.filter(discador=discador)
        .select_related('titular_user')
    )
    return JsonResponse({
        'ok': True,
        'inativos': ids_acessos_inativos_moneyconsig(acessos),
    })


# ---------- Acessos ----------

class AcessoCreateView(DiscadorModalMixin, _DiscadorMixin, CreateView):
    model = AcessoDiscador
    form_class = AcessoDiscadorForm
    modal_template_name = 'discador/_acesso_form_modal.html'
    discador_tab = 'acessos'
    modal_title = 'Novo acesso JoyTec'
    modal_subtitle = 'Vincule titular, login, ramal e campanha.'
    modal_submit_label = 'Criar acesso'
    modal_max_width = 'max-w-2xl'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['discador'] = get_or_create_joytec()
        return kwargs

    def form_valid(self, form):
        discador = get_or_create_joytec()
        try:
            self.object = criar_acesso(
                discador=discador,
                titular_nome=form.cleaned_data['titular_nome'],
                titular_user=form.cleaned_data.get('titular_user'),
                login_discador=form.cleaned_data['login_discador'],
                ramal=form.cleaned_data['ramal'],
                campanha=form.cleaned_data['campanha'],
                tipo=form.cleaned_data['tipo'],
                actor=self.request.user,
            )
        except ValidationError as exc:
            form.add_error(None, _msg_validacao(exc))
            return self.form_invalid(form)
        log_acesso_criado(self.object, self.request.user)
        messages.success(self.request, 'Acesso criado com sucesso.')
        return self.htmx_redirect_response()


class AcessoUpdateView(DiscadorModalMixin, _DiscadorMixin, UpdateView):
    model = AcessoDiscador
    form_class = AcessoDiscadorForm
    modal_template_name = 'discador/_acesso_form_modal.html'
    discador_tab = 'acessos'
    modal_submit_label = 'Salvar alterações'
    modal_max_width = 'max-w-2xl'

    def get_queryset(self):
        return AcessoDiscador.objects.filter(discador=get_or_create_joytec())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['discador'] = get_or_create_joytec()
        return kwargs

    def get_modal_title(self):
        return f'Editar acesso — {self.object.login_discador}'

    def get_modal_subtitle(self):
        return f'Ramal {self.object.ramal.numero}'

    def form_valid(self, form):
        acesso = self.get_object()
        antes = AcessoDiscador.objects.get(pk=acesso.pk)
        try:
            self.object = atualizar_acesso(
                acesso=acesso,
                titular_nome=form.cleaned_data['titular_nome'],
                titular_user=form.cleaned_data.get('titular_user'),
                login_discador=form.cleaned_data['login_discador'],
                ramal=form.cleaned_data['ramal'],
                campanha=form.cleaned_data['campanha'],
                tipo=form.cleaned_data['tipo'],
                actor=self.request.user,
            )
        except ValidationError as exc:
            form.add_error(None, _msg_validacao(exc))
            return self.form_invalid(form)
        log_acesso_atualizado(self.object, self.request.user, antes)
        messages.success(self.request, 'Acesso atualizado com sucesso.')
        return self.htmx_redirect_response()


class AcessoDeleteView(_DiscadorMixin, DeleteView):
    model = AcessoDiscador
    success_url = reverse_lazy('discador:joytec')

    def get_queryset(self):
        return AcessoDiscador.objects.filter(discador=get_or_create_joytec())

    def form_valid(self, form):
        acesso = self.object
        log_acesso_excluido(acesso, self.request.user)
        try:
            excluir_acesso(acesso=acesso, actor=self.request.user)
        except ValidationError as exc:
            messages.error(self.request, _msg_validacao(exc))
            return redirect(f"{reverse('discador:joytec')}?tab=acessos")
        messages.success(self.request, 'Acesso excluído. Ramal liberado.')
        return redirect(f"{reverse('discador:joytec')}?tab=acessos")


# ---------- Ramais ----------

class RamalCreateView(DiscadorModalMixin, _DiscadorMixin, CreateView):
    model = Ramal
    form_class = RamalForm
    discador_tab = 'ramais'
    modal_title = 'Novo ramal'
    modal_subtitle = 'Cadastre um ramal do JoyTec.'
    modal_submit_label = 'Criar ramal'
    form_layout = 'as_p'

    def form_valid(self, form):
        discador = get_or_create_joytec()
        try:
            self.object = criar_ramal(
                discador=discador,
                numero=form.cleaned_data['numero'],
                status=form.cleaned_data['status'],
                actor=self.request.user,
            )
        except ValidationError as exc:
            form.add_error(None, _msg_validacao(exc))
            return self.form_invalid(form)
        log_ramal_criado(self.object, self.request.user)
        messages.success(self.request, f'Ramal {self.object.numero} cadastrado.')
        return self.htmx_redirect_response()


class RamalUpdateView(DiscadorModalMixin, _DiscadorMixin, UpdateView):
    model = Ramal
    form_class = RamalForm
    discador_tab = 'ramais'
    modal_submit_label = 'Salvar'
    form_layout = 'as_p'

    def get_queryset(self):
        return Ramal.objects.filter(discador=get_or_create_joytec())

    def get_modal_title(self):
        return f'Editar ramal — {self.object.numero}'

    def form_valid(self, form):
        antes = Ramal.objects.get(pk=self.get_object().pk)
        try:
            self.object = atualizar_ramal(
                ramal=self.get_object(),
                numero=form.cleaned_data['numero'],
                status=form.cleaned_data['status'],
                actor=self.request.user,
            )
        except ValidationError as exc:
            form.add_error(None, _msg_validacao(exc))
            return self.form_invalid(form)
        log_ramal_atualizado(self.object, self.request.user, antes)
        messages.success(self.request, 'Ramal atualizado.')
        return self.htmx_redirect_response()


class RamalDeleteView(_DiscadorMixin, DeleteView):
    model = Ramal

    def get_queryset(self):
        return Ramal.objects.filter(discador=get_or_create_joytec())

    def form_valid(self, form):
        ramal = self.object
        try:
            excluir_ramal(ramal=ramal, actor=self.request.user)
        except ValidationError as exc:
            messages.error(self.request, _msg_validacao(exc))
            return redirect(f"{reverse('discador:joytec')}?tab=ramais")
        log_ramal_excluido(ramal, self.request.user)
        messages.success(self.request, 'Ramal excluído.')
        return redirect(f"{reverse('discador:joytec')}?tab=ramais")


# ---------- Campanhas ----------

class CampanhaCreateView(DiscadorModalMixin, _DiscadorMixin, CreateView):
    model = Campanha
    form_class = CampanhaForm
    discador_tab = 'campanhas'
    modal_title = 'Nova campanha'
    modal_subtitle = 'Cadastre uma campanha do JoyTec.'
    modal_submit_label = 'Criar campanha'
    form_layout = 'as_p'

    def form_valid(self, form):
        discador = get_or_create_joytec()
        self.object = criar_campanha(
            discador=discador,
            nome=form.cleaned_data['nome'],
            is_active=form.cleaned_data.get('is_active', True),
        )
        log_campanha_criada(self.object, self.request.user)
        messages.success(self.request, f'Campanha "{self.object.nome}" criada.')
        return self.htmx_redirect_response()


class CampanhaUpdateView(DiscadorModalMixin, _DiscadorMixin, UpdateView):
    model = Campanha
    form_class = CampanhaForm
    discador_tab = 'campanhas'
    modal_submit_label = 'Salvar'
    form_layout = 'as_p'

    def get_queryset(self):
        return Campanha.objects.filter(discador=get_or_create_joytec())

    def get_modal_title(self):
        return f'Editar campanha — {self.object.nome}'

    def form_valid(self, form):
        antes = Campanha.objects.get(pk=self.get_object().pk)
        self.object = atualizar_campanha(
            campanha=self.get_object(),
            nome=form.cleaned_data['nome'],
            is_active=form.cleaned_data.get('is_active', True),
        )
        log_campanha_atualizada(self.object, self.request.user, antes)
        messages.success(self.request, 'Campanha atualizada.')
        return self.htmx_redirect_response()


class CampanhaDeleteView(_DiscadorMixin, DeleteView):
    model = Campanha

    def get_queryset(self):
        return Campanha.objects.filter(discador=get_or_create_joytec())

    def form_valid(self, form):
        campanha = self.object
        try:
            excluir_campanha(campanha=campanha)
        except ValidationError as exc:
            messages.error(self.request, _msg_validacao(exc))
            return redirect(f"{reverse('discador:joytec')}?tab=campanhas")
        # Objeto já removido do banco; auditoria usa snapshot em memória
        log_campanha_excluida(campanha, self.request.user)
        messages.success(self.request, 'Campanha excluída.')
        return redirect(f"{reverse('discador:joytec')}?tab=campanhas")


# ---------- Contrato ----------

class ContratoUpdateView(DiscadorModalMixin, _DiscadorMixin, UpdateView):
    model = Discador
    form_class = ContratoForm
    discador_tab = 'contrato'
    modal_title = 'Atualizar contrato JoyTec'
    modal_subtitle = 'Altere valor por licença e quantidade contratada.'
    modal_submit_label = 'Salvar contrato'
    form_layout = 'as_p'
    modal_max_width = 'max-w-lg'

    def get_object(self, queryset=None):
        return get_or_create_joytec()

    def form_valid(self, form):
        try:
            self.object = atualizar_contrato(
                discador=self.get_object(),
                valor_por_licenca=form.cleaned_data['valor_por_licenca'],
                licencas_contratadas=form.cleaned_data['licencas_contratadas'],
                observacao=form.cleaned_data.get('observacao', ''),
                actor=self.request.user,
            )
        except ValidationError as exc:
            form.add_error(None, _msg_validacao(exc))
            return self.form_invalid(form)
        log_contrato_atualizado(self.object, self.request.user)
        messages.success(self.request, 'Contrato atualizado e registrado no histórico.')
        return self.htmx_redirect_response()
