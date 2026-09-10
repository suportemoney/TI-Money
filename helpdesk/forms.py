from django import forms
from django.utils.safestring import mark_safe

from core.models import CustomUser
from helpdesk.models import HelpdeskRestrictionGroup, Ticket, TicketCategory, validate_file_attachment
from helpdesk.ticket_access import (
    buscar_membro_equipe_por_nome,
    usuario_pode_definir_prioridade,
    usuarios_tecnicos_para_transferencia,
)


INPUT_CLASS = 'w-full text-sm p-2.5 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-slate-50 focus:bg-white transition-colors'
SELECT_CLASS = 'w-full text-sm p-2.5 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-slate-50 focus:bg-white transition-colors'


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = single_file_clean(data, initial)
        return result


class TicketCreateForm(forms.ModelForm):
    """Formulário de criação de chamado com campos condicionais por papel do usuário."""

    TIPO_TEXTO = 'texto'
    TIPO_USUARIO = 'usuario'
    TIPO_EU = 'eu'

    tipo_solicitante = forms.ChoiceField(
        choices=[
            (TIPO_TEXTO, 'Nome livre'),
            (TIPO_USUARIO, 'Usuário do sistema'),
        ],
        initial=TIPO_TEXTO,
        widget=forms.RadioSelect,
        label='Tipo de solicitante',
    )
    requester_name = forms.CharField(
        required=False,
        max_length=150,
        label='Nome do solicitante',
        widget=forms.TextInput(attrs={
            'placeholder': 'Nome de quem solicita o chamado',
            'class': INPUT_CLASS,
        }),
    )
    requester_user = forms.ModelChoiceField(
        queryset=CustomUser.objects.none(),
        required=False,
        label='Usuário solicitante',
        empty_label='Selecione um usuário',
        widget=forms.Select(attrs={'class': SELECT_CLASS + ' searchable-select'}),
    )
    attachment = MultipleFileField(
        required=False,
        label='Anexos (Opcional)',
        help_text='Máx: 5MB por arquivo. Limite de 4 arquivos (PDF, imagens/GIF, áudios, Word, Excel, ZIP, etc).',
        validators=[validate_file_attachment],
        widget=MultipleFileInput(attrs={
            'class': 'w-full text-sm p-2 border border-slate-300 rounded-lg bg-white',
            'accept': 'image/*,.gif,audio/*,.pdf,.doc,.docx,.xls,.xlsx,.zip,.rar,.txt,.csv,.mp3,.wav,.ogg,.m4a',
            'multiple': True
        })
    )

    class Meta:
        model = Ticket
        fields = ['title', 'description', 'priority', 'category', 'specific_category', 'equipe']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'Resumo curto da solicitação',
                'class': INPUT_CLASS,
            }),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Descreva o cenário, erros apresentados, prints...',
                'class': INPUT_CLASS + ' resize-y',
                'required': 'required',
                'aria-required': 'true',
            }),
            'priority': forms.Select(attrs={'class': SELECT_CLASS}),
            'category': forms.Select(attrs={'class': SELECT_CLASS}),
            'specific_category': forms.Select(attrs={'class': SELECT_CLASS}),
            'equipe': forms.Select(attrs={'class': SELECT_CLASS}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        nome_padrao = kwargs.pop('nome_solicitante_padrao', '')
        categoria_inicial = kwargs.pop('categoria_inicial', None)
        super().__init__(*args, **kwargs)

        from helpdesk.models import TicketSpecificCategory
        self.fields['category'].queryset = TicketCategory.objects.filter(is_active=True).order_by('name')
        self.fields['specific_category'].queryset = TicketSpecificCategory.objects.filter(is_active=True).order_by('name')
        self.fields['specific_category'].required = False
        self.fields['specific_category'].empty_label = 'Sem categoria específica'
        
        if not self.is_bound and categoria_inicial:
            self.fields['category'].initial = categoria_inicial
        elif not self.is_bound:
            padrao = TicketCategory.objects.filter(is_active=True, name__iexact='Outros').first()
            if padrao:
                self.fields['category'].initial = padrao.pk

        self._configurar_por_papel(nome_padrao)
        self.fields['description'].required = True
        self.fields['description'].label = mark_safe(
            'Descrição Detalhada <span class="text-red-500">*</span>'
        )

    def clean_description(self):
        descricao = (self.cleaned_data.get('description') or '').strip()
        if not descricao:
            raise forms.ValidationError('Informe a descrição do chamado.')
        return descricao

    def _configurar_por_papel(self, nome_padrao):
        if not self.user:
            return

        from core.models import Equipe
        role = getattr(self.user, 'role', CustomUser.RoleChoices.STANDARD)
        if self.user.is_superuser:
            role = CustomUser.RoleChoices.ADMIN

        if role == CustomUser.RoleChoices.STANDARD:
            equipes_ativas = self.user.equipes.filter(is_active=True).order_by('name')
            self.fields['equipe'].queryset = equipes_ativas
            if equipes_ativas.count() <= 1:
                self._remover_campo('equipe')
            else:
                self.fields['equipe'].empty_label = 'Selecione a equipe'
                self.fields['equipe'].required = True

            self._remover_campo('tipo_solicitante')
            self._remover_campo('requester_name')
            self._remover_campo('requester_user')
            self._remover_campo('priority')
            self._remover_campo('specific_category')
            return

        if role in (
            CustomUser.RoleChoices.MULTIPLIER,
            CustomUser.RoleChoices.SUPERVISOR,
            CustomUser.RoleChoices.TEAM_LEADER,
        ):
            self.fields['tipo_solicitante'].choices = [
                (self.TIPO_EU, 'Eu mesmo(a)'),
                (self.TIPO_TEXTO, 'Nome livre'),
            ]
            self.fields['tipo_solicitante'].initial = self.TIPO_EU
            self._remover_campo('requester_user')
            self._remover_campo('priority')
            self._remover_campo('specific_category')
            self._configurar_equipes_usuario()
            return
        # ADMIN, IT_USER ou superuser
        if 'equipe' in self.fields:
            self.fields['equipe'].queryset = Equipe.objects.filter(is_active=True).order_by('name')
            self.fields['equipe'].empty_label = 'Selecione a equipe'
            self.fields['equipe'].required = False

        if not self.is_bound and nome_padrao:
            self.fields['requester_name'].initial = nome_padrao
        if not usuario_pode_definir_prioridade(self.user):
            self._remover_campo('priority')
        else:
            self.fields['priority'].required = False
            self.fields['priority'].empty_label = 'Sem prioridade (triagem posterior)'

        if 'requester_user' in self.fields:
            self.fields['requester_user'].queryset = CustomUser.objects.filter(
                is_active=True,
            ).order_by('first_name', 'last_name', 'username')
            self.fields['requester_user'].label_from_instance = self._rotulo_usuario

    def _configurar_equipes_usuario(self):
        """Restringe equipe às equipes do usuário (supervisor, líder, multiplicador)."""
        equipes_ativas = self.user.equipes.filter(is_active=True).order_by('name')
        self.fields['equipe'].queryset = equipes_ativas
        if equipes_ativas.count() <= 1:
            self._remover_campo('equipe')
        else:
            self.fields['equipe'].empty_label = 'Selecione a equipe'
            self.fields['equipe'].required = True

    def _remover_campo(self, nome):
        if nome in self.fields:
            del self.fields[nome]

    @staticmethod
    def _rotulo_usuario(usuario):
        nome = usuario.get_full_name().strip()
        if nome:
            return f'{nome} ({usuario.username})'
        return usuario.username

    def clean(self):
        cleaned = super().clean()
        role = getattr(self.user, 'role', CustomUser.RoleChoices.STANDARD)

        if role == CustomUser.RoleChoices.STANDARD and not self.user.is_superuser:
            cleaned['requester_name'] = self.user.get_full_name() or self.user.username
            cleaned['requester_user'] = self.user
            cleaned['priority'] = None
            return cleaned

        tipo = cleaned.get('tipo_solicitante')
        requester_name = (cleaned.get('requester_name') or '').strip()
        requester_user = cleaned.get('requester_user')
        co_autor_user = None

        if tipo == self.TIPO_USUARIO:
            if not requester_user:
                self.add_error('requester_user', 'Selecione um usuário do sistema.')
            else:
                cleaned['requester_name'] = (
                    requester_user.get_full_name() or requester_user.username
                )
                cleaned['requester_user'] = requester_user
                cleaned['co_autor_user'] = None
        elif tipo == self.TIPO_EU:
            cleaned['requester_name'] = self.user.get_full_name() or self.user.username
            cleaned['requester_user'] = self.user
            cleaned['co_autor_user'] = None
        else:
            if not requester_name:
                self.add_error('requester_name', 'Informe o nome do solicitante.')
            else:
                cleaned['requester_name'] = requester_name
                cleaned['requester_user'] = None
                if role == CustomUser.RoleChoices.MULTIPLIER:
                    co_autor_user = buscar_membro_equipe_por_nome(self.user, requester_name)

        cleaned['co_autor_user'] = co_autor_user

        if not usuario_pode_definir_prioridade(self.user):
            cleaned['priority'] = None

        return cleaned

    def save(self, commit=True, created_by=None):
        ticket = super().save(commit=False)
        ticket.requester_name = self.cleaned_data['requester_name']
        ticket.requester_user = self.cleaned_data.get('requester_user')
        if not usuario_pode_definir_prioridade(self.user):
            ticket.priority = None
        if created_by is not None:
            ticket.created_by = created_by
            
        if not ticket.equipe_id and self.user:
            equipes = self.user.equipes.filter(is_active=True)
            if equipes.count() == 1:
                ticket.equipe = equipes.first()
                
        if commit:
            ticket.save()
            co_autor = self.cleaned_data.get('co_autor_user')
            if co_autor:
                ticket.co_authors.add(co_autor)
            attachments = self.cleaned_data.get('attachment')
            if attachments:
                if not isinstance(attachments, list):
                    attachments = [attachments]
                
                from helpdesk.models import TicketAttachment
                from helpdesk.image_utils import optimize_image_to_webp
                
                for attachment_file in attachments[:4]:
                    optimized_file = optimize_image_to_webp(attachment_file)
                    TicketAttachment.objects.create(
                        ticket=ticket,
                        file_name=optimized_file.name,
                        file=optimized_file
                    )
        return ticket


class TicketUpdateForm(forms.ModelForm):
    """Formulário de edição de chamado (somente ADMIN/superuser)."""

    TIPO_TEXTO = 'texto'
    TIPO_USUARIO = 'usuario'
    TIPO_EU = 'eu'

    tipo_solicitante = forms.ChoiceField(
        choices=[
            (TIPO_TEXTO, 'Nome livre'),
            (TIPO_USUARIO, 'Usuário do sistema'),
        ],
        widget=forms.RadioSelect,
        label='Tipo de solicitante',
    )
    requester_name = forms.CharField(
        required=False,
        max_length=150,
        label='Nome do solicitante',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )
    requester_user = forms.ModelChoiceField(
        queryset=CustomUser.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username'),
        required=False,
        label='Usuário solicitante',
        empty_label='Selecione um usuário',
        widget=forms.Select(attrs={'class': SELECT_CLASS}),
    )

    class Meta:
        model = Ticket
        fields = ['title', 'description', 'category', 'specific_category', 'priority', 'status', 'assigned_to']
        widgets = {
            'title': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'class': INPUT_CLASS + ' resize-y',
                'required': 'required',
                'aria-required': 'true',
            }),
            'category': forms.Select(attrs={'class': SELECT_CLASS}),
            'specific_category': forms.Select(attrs={'class': SELECT_CLASS}),
            'priority': forms.Select(attrs={'class': SELECT_CLASS}),
            'status': forms.Select(attrs={'class': SELECT_CLASS}),
            'assigned_to': forms.Select(attrs={'class': SELECT_CLASS}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        from helpdesk.models import TicketSpecificCategory
        self.fields['category'].queryset = TicketCategory.objects.filter(is_active=True).order_by('name')
        self.fields['specific_category'].queryset = TicketSpecificCategory.objects.filter(is_active=True).order_by('name')
        self.fields['specific_category'].required = False
        self.fields['specific_category'].empty_label = 'Nenhuma'
        self.fields['assigned_to'].queryset = usuarios_tecnicos_para_transferencia()
        self.fields['assigned_to'].required = False
        self.fields['assigned_to'].empty_label = 'Não atribuído'
        self.fields['priority'].required = False
        self.fields['priority'].empty_label = 'Sem prioridade'
        self.fields['description'].required = True
        self.fields['description'].label = mark_safe(
            'Descrição <span class="text-red-500">*</span>'
        )
        self.fields['requester_user'].label_from_instance = TicketCreateForm._rotulo_usuario

        if self.instance and self.instance.pk:
            if self.instance.requester_user_id:
                self.fields['tipo_solicitante'].initial = self.TIPO_USUARIO
                self.fields['requester_user'].initial = self.instance.requester_user_id
            else:
                self.fields['tipo_solicitante'].initial = self.TIPO_TEXTO
                self.fields['requester_name'].initial = self.instance.requester_name

        role = getattr(self.user, 'role', CustomUser.RoleChoices.STANDARD) if self.user else CustomUser.RoleChoices.STANDARD
        if self.user and self.user.is_superuser:
            role = CustomUser.RoleChoices.ADMIN

        if role in (
            CustomUser.RoleChoices.STANDARD,
            CustomUser.RoleChoices.SUPERVISOR,
            CustomUser.RoleChoices.TEAM_LEADER,
            CustomUser.RoleChoices.MULTIPLIER,
        ):
            self._remover_campo('priority')
            self._remover_campo('status')
            self._remover_campo('assigned_to')
            self._remover_campo('specific_category')
            if role == CustomUser.RoleChoices.STANDARD:
                self._remover_campo('tipo_solicitante')
                self._remover_campo('requester_name')
                self._remover_campo('requester_user')
            elif role in (
                CustomUser.RoleChoices.SUPERVISOR,
                CustomUser.RoleChoices.TEAM_LEADER,
                CustomUser.RoleChoices.MULTIPLIER,
            ):
                self.fields['tipo_solicitante'].choices = [
                    (self.TIPO_EU, 'Eu mesmo(a)'),
                    (self.TIPO_TEXTO, 'Nome livre'),
                ]
                self._remover_campo('requester_user')
                if self.instance and self.instance.pk:
                    if self.instance.requester_user_id == self.user.id:
                        self.fields['tipo_solicitante'].initial = self.TIPO_EU
                    else:
                        self.fields['tipo_solicitante'].initial = self.TIPO_TEXTO
                        if self.instance.requester_user_id:
                            self.fields['requester_name'].initial = (
                                self.instance.requester_user.get_full_name() or self.instance.requester_user.username
                            )
                        else:
                            self.fields['requester_name'].initial = self.instance.requester_name

    def _remover_campo(self, nome):
        if nome in self.fields:
            del self.fields[nome]

    def clean_description(self):
        descricao = (self.cleaned_data.get('description') or '').strip()
        if not descricao:
            raise forms.ValidationError('Informe a descrição do chamado.')
        return descricao

    def clean(self):
        cleaned = super().clean()
        
        # Se os campos de solicitante não estão no formulário (ex: usuário padrão), não valide ou altere o solicitante
        if 'tipo_solicitante' not in self.fields:
            return cleaned

        tipo = cleaned.get('tipo_solicitante')
        requester_name = (cleaned.get('requester_name') or '').strip()
        requester_user = cleaned.get('requester_user')

        if tipo == self.TIPO_USUARIO:
            if not requester_user:
                self.add_error('requester_user', 'Selecione um usuário do sistema.')
            else:
                cleaned['requester_name'] = (
                    requester_user.get_full_name() or requester_user.username
                )
                cleaned['requester_user'] = requester_user
        elif tipo == self.TIPO_EU:
            cleaned['requester_name'] = self.user.get_full_name() or self.user.username
            cleaned['requester_user'] = self.user
        else:
            if not requester_name:
                self.add_error('requester_name', 'Informe o nome do solicitante.')
            else:
                cleaned['requester_name'] = requester_name
                cleaned['requester_user'] = None

        return cleaned

    def save(self, commit=True):
        ticket = super().save(commit=False)
        if 'tipo_solicitante' in self.fields:
            ticket.requester_name = self.cleaned_data['requester_name']
            ticket.requester_user = self.cleaned_data.get('requester_user')
        if commit:
            ticket.save()
        return ticket


class HelpdeskRestrictionGroupForm(forms.ModelForm):
    """Formulário de um grupo de restrição de visibilidade de chamados."""

    class Meta:
        model = HelpdeskRestrictionGroup
        fields = ['nome', 'ativo', 'usuarios_restritos', 'visualizadores']
        widgets = {
            'nome': forms.TextInput(attrs={
                'class': INPUT_CLASS,
                'placeholder': 'Ex.: Grupo 1',
            }),
            'ativo': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500',
            }),
            'usuarios_restritos': forms.CheckboxSelectMultiple(attrs={
                'class': 'h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500',
            }),
            'visualizadores': forms.CheckboxSelectMultiple(attrs={
                'class': 'h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from helpdesk.ticket_access import (
            usuarios_ativos_para_restricao,
            usuarios_visualizadores_restricao,
        )

        self.fields['nome'].required = True
        self.fields['usuarios_restritos'].required = False
        self.fields['usuarios_restritos'].queryset = usuarios_ativos_para_restricao()
        self.fields['usuarios_restritos'].label_from_instance = TicketCreateForm._rotulo_usuario
        self.fields['visualizadores'].required = False
        self.fields['visualizadores'].queryset = usuarios_visualizadores_restricao()
        self.fields['visualizadores'].label_from_instance = TicketCreateForm._rotulo_usuario
        self.fields['ativo'].label = 'Restringir chamados'
