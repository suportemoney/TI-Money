import os
from django.db import models
from django.db.models import Q, OuterRef, Subquery, DateTimeField, Case, When
from django.db.models.functions import Coalesce, Least
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class TicketCategory(models.Model):
    """Categoria de chamado configurável (ex.: Hardware, Software)."""
    name = models.CharField(max_length=80, unique=True, help_text='Nome exibido da categoria.')
    is_active = models.BooleanField(default=True, help_text='Categorias inativas não aparecem no formulário.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'categoria de chamado'
        verbose_name_plural = 'categorias de chamado'

    def __str__(self) -> str:
        return self.name


class TicketSpecificCategory(models.Model):
    """Categoria específica do chamado definida pela TI (ex.: Troca de fonte, Instalação de software)."""
    name = models.CharField(max_length=80, unique=True, help_text='Nome exibido da categoria específica.')
    is_active = models.BooleanField(default=True, help_text='Categorias inativas não aparecem no formulário.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'categoria específica'
        verbose_name_plural = 'categorias específicas'

    def __str__(self) -> str:
        return self.name


class Ticket(models.Model):
    """
    Modelo que representa um chamado (Ticket) de Helpdesk.
    Utilizado para o Kanban e controle de tarefas da TI.
    """
    class StatusChoices(models.TextChoices):
        NEW = 'NEW', 'Novos'
        IN_PROGRESS = 'IN_PROGRESS', 'Em Atendimento'
        PENDING = 'PENDING', 'Pendente'
        RESOLVED = 'RESOLVED', 'Resolvido'

    class PriorityChoices(models.TextChoices):
        LOW = 'LOW', 'Baixa'
        MEDIUM = 'MEDIUM', 'Média'
        HIGH = 'HIGH', 'Alta'
        URGENT = 'URGENT', 'Urgente'
        
    title = models.CharField(max_length=200, help_text='Título ou resumo do problema.')
    description = models.TextField(help_text='Descrição detalhada do chamado.')
    
    status = models.CharField(
        max_length=20, 
        choices=StatusChoices.choices, 
        default=StatusChoices.NEW,
        help_text='Coluna atual do cartão no Kanban.'
    )
    priority = models.CharField(
        max_length=20,
        choices=PriorityChoices.choices,
        null=True,
        blank=True,
        help_text='Definida pela TI; null até triagem.',
    )
    category = models.ForeignKey(
        TicketCategory,
        on_delete=models.PROTECT,
        related_name='tickets',
        help_text='Categoria do problema.',
    )
    specific_category = models.ForeignKey(
        TicketSpecificCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='tickets',
        help_text='Categoria específica definida pela TI.',
    )
    
    equipe = models.ForeignKey(
        'core.Equipe',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets',
        help_text='Equipe/Setor de contexto para este chamado.'
    )
    
    requester_name = models.CharField(
        max_length=150, 
        help_text='Nome do solicitante (ex: vindo do WhatsApp).'
    )

    requester_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requested_tickets',
        help_text='Usuário do sistema selecionado como solicitante.',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_tickets',
        help_text='Usuário que abriu o chamado no sistema.',
    )

    co_authors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='coauthored_tickets',
        help_text='Co-autores com acesso e permissão de comentário no chamado.',
    )
    
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='assigned_tickets',
        help_text='Técnico responsável pelo chamado.'
    )
    
    is_rejected = models.BooleanField(default=False, help_text='Indica se o chamado foi recusado pelo técnico.')
    rejection_reason = models.TextField(null=True, blank=True, help_text='Motivo da recusa do chamado.')
    
    unread_by_tech = models.BooleanField(default=False, help_text='Possui interações não lidas pela TI.')
    unread_by_user = models.BooleanField(default=False, help_text='Possui interações não lidas pelo usuário.')
    
    unread_count_tech = models.IntegerField(default=0, help_text='Quantidade de interações não lidas pela TI.')
    unread_count_user = models.IntegerField(default=0, help_text='Quantidade de interações não lidas pelo usuário.')
    
    # Soft delete, arquivamento e timestamps
    is_active = models.BooleanField(default=True, help_text='Indica se o registro está ativo (Soft delete).')
    is_archived = models.BooleanField(default=False, help_text='Indica se o chamado foi arquivado após um tempo resolvido.')
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Momento em que o chamado foi finalizado/resolvido (base do arquivamento automático).',
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_tickets',
        help_text='Usuário que finalizou ou recusou o chamado.',
    )
    assistente_escalado = models.BooleanField(
        default=False,
        help_text='Assistente IA encerrou o atendimento e pediu intervenção da TI.',
    )
    assistente_aguardando_desde = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            'Início da espera por resposta do solicitante/criador após mensagem pública do Assistente.'
        ),
    )
    assistente_followup_mencao_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Quando o Assistente cobrou resposta com @menção (follow-up de 5 min).',
    )
    tag = models.ForeignKey(
        'TicketTag',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tickets',
        help_text='Tag curta de funil/follow-up (uma por chamado).',
    )
    assistente_ajuda_ti_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Última solicitação de ajuda do Assistente aos técnicos online (anti-spam).',
    )
    assistente_chip_auth_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            'Início/renovação da autorização de chips dada por TI via @assistente interno. '
            'Vale por ASSISTENTE_CHIP_AUTH_MINUTOS e é renovada a cada nota interna da TI.'
        ),
    )
    assistente_chip_auth_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chip_auth_tickets',
        help_text='Membro da TI que autorizou operações de chip neste chamado.',
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text='Data e hora de criação.')
    updated_at = models.DateTimeField(auto_now=True, help_text='Data e hora da última atualização.')

    # Prazos padrão de arquivamento automático (em horas)
    HORAS_ARQUIVAR_RESOLVIDO = 24
    HORAS_ARQUIVAR_RECUSADO = 24

    # Janela em que a autorização de chips dada pela TI continua valendo
    ASSISTENTE_CHIP_AUTH_MINUTOS = 120

    @property
    def assistente_chip_autorizado(self) -> bool:
        """True se a TI autorizou operações de chip e a janela ainda está aberta."""
        from datetime import timedelta

        if not self.assistente_chip_auth_em:
            return False
        limite = timedelta(minutes=self.ASSISTENTE_CHIP_AUTH_MINUTOS)
        return (timezone.now() - self.assistente_chip_auth_em) <= limite

    @classmethod
    def archive_old_tickets(
        cls,
        hours_resolved=None,
        hours_rejected=None,
        days_resolved=None,
        **_kwargs,
    ):
        """
        Arquiva tickets RESOLVED após N horas e REJECTED após M horas.
        Aceita hours_resolved ou days_resolved (legado) para evitar erro em deploy parcial.
        """
        if hours_resolved is None and days_resolved is not None:
            hours_resolved = days_resolved * 24
        if hours_resolved is None:
            hours_resolved = cls.HORAS_ARQUIVAR_RESOLVIDO
        if hours_rejected is None:
            hours_rejected = cls.HORAS_ARQUIVAR_RECUSADO

        now = timezone.now()
        resolved_cutoff = now - timedelta(hours=hours_resolved)

        # Data do último comentário de finalização/recusa (mais confiável que updated_at)
        finalize_subquery = Comment.objects.filter(
            ticket_id=OuterRef('pk'),
            is_active=True,
        ).filter(
            Q(text__startswith='Chamado finalizado') | Q(text__startswith='Chamado recusado')
        ).order_by('-created_at').values('created_at')[:1]

        elegiveis = (
            cls.objects.filter(status=cls.StatusChoices.RESOLVED, is_archived=False)
            .annotate(
                data_comentario_final=Subquery(finalize_subquery, output_field=DateTimeField()),
            )
            .annotate(
                referencia=Case(
                    When(
                        resolved_at__isnull=False,
                        data_comentario_final__isnull=False,
                        then=Least('resolved_at', 'data_comentario_final'),
                    ),
                    default=Coalesce('resolved_at', 'data_comentario_final', 'updated_at'),
                    output_field=DateTimeField(),
                ),
            )
            .filter(referencia__lt=resolved_cutoff)
        )

        pks_arquivar = []
        for ticket in elegiveis.iterator():
            pks_arquivar.append(ticket.pk)
            if not ticket.data_comentario_final:
                continue
            if ticket.resolved_at is None or ticket.resolved_at > ticket.data_comentario_final:
                cls.objects.filter(pk=ticket.pk).update(resolved_at=ticket.data_comentario_final)

        if pks_arquivar:
            cls.objects.filter(pk__in=pks_arquivar).update(is_archived=True)

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old_status = Ticket.objects.only('status').get(pk=self.pk).status
                if old_status != self.status:
                    self.is_archived = False
                    if self.status == self.StatusChoices.RESOLVED:
                        self.resolved_at = timezone.now()
                    elif old_status == self.StatusChoices.RESOLVED:
                        self.resolved_at = None
                        self.resolved_by = None
            except Ticket.DoesNotExist:
                pass
        elif self.status == self.StatusChoices.RESOLVED:
            self.resolved_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"[{self.get_status_display()}] {self.title} - {self.requester_name}"

    @property
    def attachment_counts(self):
        images, audios, docs = 0, 0, 0
        for att in self.attachments.all():
            if att.is_image:
                images += 1
            elif att.is_audio:
                audios += 1
            else:
                docs += 1
        return {'images': images, 'audios': audios, 'docs': docs}


import os
import uuid
from django.core.exceptions import ValidationError

def validate_file_attachment(value):
    ext = os.path.splitext(value.name)[1].lower()
    valid_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.txt', '.csv', '.mp3', '.wav', '.ogg', '.m4a']
    if ext not in valid_extensions:
        raise ValidationError('Tipo de arquivo não permitido (apenas imagens/GIF, áudios, PDF, Word, Excel, ZIP, RAR, TXT, CSV).')
    if value.size > 5 * 1024 * 1024:
        raise ValidationError('O arquivo não pode ser maior que 5MB.')

# Mantido para compatibilidade com migrations antigas
validate_image_attachment = validate_file_attachment

def attachment_upload_path(instance, filename):
    ext = filename.split('.')[-1]
    new_filename = f"{uuid.uuid4().hex}.{ext}"
    return os.path.join('ticket_attachments', new_filename)

class TicketAttachment(models.Model):
    """Anexos de imagens para os chamados."""
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='attachments')
    file_name = models.CharField(max_length=255, help_text='Nome original do arquivo.')
    file = models.FileField(upload_to=attachment_upload_path, validators=[validate_file_attachment])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Anexo do chamado #{self.ticket.id}: {self.file_name}"

    @property
    def is_image(self):
        ext = os.path.splitext(self.file.name)[1].lower()
        return ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']

    @property
    def is_audio(self):
        ext = os.path.splitext(self.file.name)[1].lower()
        return ext in ['.mp3', '.wav', '.ogg', '.m4a']

    @property
    def extension(self):
        ext = os.path.splitext(self.file.name)[1].lower()
        return ext[1:] if ext else ''


class Comment(models.Model):
    """
    Modelo de comentários e histórico de iterações em um ticket.
    """
    ticket = models.ForeignKey(
        Ticket, 
        on_delete=models.CASCADE, 
        related_name='comments',
        help_text='Chamado ao qual o comentário pertence.'
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True,
        help_text='Usuário que fez o comentário (ou None se for do sistema).'
    )
    text = models.TextField(help_text='Texto do comentário ou atualização do histórico.')
    attachment = models.FileField(
        upload_to=attachment_upload_path, 
        validators=[validate_file_attachment], 
        null=True, 
        blank=True, 
        help_text='Arquivo anexado ao comentário.'
    )
    
    # Soft delete e timestamps
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_assistente = models.BooleanField(
        default=False,
        help_text='Comentário gerado pelo Assistente de IA.',
    )
    is_interno = models.BooleanField(
        default=False,
        help_text=(
            'Mensagem interna: visível só para TI/staff/superuser e para o Assistente. '
            'Solicitante/criador comum não vê.'
        ),
    )
    structured_payload = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            'Payload estruturado do Assistente (questionário com opções ou '
            'bloco de esclarecimento). Comentários antigos ficam nulos.'
        ),
    )

    def __str__(self) -> str:
        autor = self.author.username if self.author_id else ('Assistente' if self.is_assistente else 'Sistema')
        return f"Comment by {autor} on Ticket {self.ticket_id}"

    @property
    def payload_tipo(self) -> str:
        """Tipo do payload estruturado, se houver."""
        payload = self.structured_payload or {}
        return str(payload.get('type') or '')

    @property
    def questionario_aberto(self) -> bool:
        """True se este comentário é um questionário ainda sem resposta."""
        payload = self.structured_payload or {}
        return (
            payload.get('type') == 'questionario'
            and payload.get('status') == 'aberto'
        )

    @property
    def esclarecimento_aberto(self) -> bool:
        """True se este comentário é um esclarecimento ainda aberto."""
        payload = self.structured_payload or {}
        return (
            payload.get('type') == 'esclarecimento'
            and payload.get('status', 'aberto') == 'aberto'
        )

    @property
    def is_image(self):
        if not self.attachment:
            return False
        ext = os.path.splitext(self.attachment.name)[1].lower()
        return ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']

    @property
    def is_audio(self):
        if not self.attachment:
            return False
        ext = os.path.splitext(self.attachment.name)[1].lower()
        return ext in ['.mp3', '.wav', '.ogg', '.m4a']


class TicketContestation(models.Model):
    """Registro de contestação de chamado finalizado pelo solicitante."""
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name='contestations',
        help_text='Chamado contestado.',
    )
    contested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ticket_contestations',
        help_text='Usuário que contestou a finalização.',
    )
    reason = models.TextField(help_text='Motivo informado na contestação.')
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='contested_finalizations',
        help_text='Usuário que havia finalizado ou recusado o chamado.',
    )
    finalized_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Momento em que o chamado havia sido finalizado.',
    )
    was_rejected = models.BooleanField(
        default=False,
        help_text='Indica se a finalização contestada era uma recusa.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'contestação de chamado'
        verbose_name_plural = 'contestações de chamado'

    def __str__(self) -> str:
        return f'Contestação #{self.pk} — chamado #{self.ticket_id}'


class PushSubscription(models.Model):
    """Inscrição Web Push do usuário para notificações de chamados."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='push_subscriptions',
        help_text='Usuário inscrito para receber push.',
    )
    endpoint = models.TextField(unique=True, help_text='URL do endpoint do browser.')
    p256dh = models.CharField(max_length=255, help_text='Chave pública do cliente (p256dh).')
    auth = models.CharField(max_length=255, help_text='Segredo de autenticação do cliente.')
    user_agent = models.CharField(max_length=255, blank=True, help_text='User-Agent no momento da inscrição.')
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, help_text='False quando o endpoint expirou ou foi cancelado.')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'inscrição push'
        verbose_name_plural = 'inscrições push'

    def __str__(self) -> str:
        return f'Push #{self.pk} — {self.user}'


class TicketUnread(models.Model):
    """Controle individual de interações não lidas por usuário."""
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='unreads')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='unread_tickets_link')
    count = models.IntegerField(default=1)

    class Meta:
        unique_together = ('ticket', 'user')
        verbose_name = 'notificação não lida'
        verbose_name_plural = 'notificações não lidas'

    def __str__(self):
        return f'{self.user.username} tem {self.count} não lida(s) no chamado #{self.ticket.id}'


class TicketMention(models.Model):
    """Menção @username em comentário — concede acesso e alerta visual até vista."""
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name='mentions',
        help_text='Chamado em que a menção ocorreu.',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ticket_mentions',
        help_text='Usuário mencionado.',
    )
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name='mentions',
        help_text='Comentário que contém a menção.',
    )
    mentioned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mentions_made',
        help_text='Operador que fez a menção.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Momento em que o mencionado abriu o chamado (null = não visto).',
    )

    class Meta:
        unique_together = ('ticket', 'user', 'comment')
        ordering = ['-created_at']
        verbose_name = 'menção em chamado'
        verbose_name_plural = 'menções em chamados'

    def __str__(self):
        return f'@{self.user.username} em chamado #{self.ticket_id}'


class TicketTag(models.Model):
    """Tag curta reutilizável para funil/follow-up dos chamados (máx. 1 por ticket)."""

    nome = models.CharField(max_length=30, unique=True, help_text='Nome curto da tag (sem espaços longos).')
    slug = models.SlugField(max_length=40, unique=True, help_text='Identificador normalizado.')
    criada_por_ia = models.BooleanField(default=False, help_text='True se criada pelo Assistente.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'tag de chamado'
        verbose_name_plural = 'tags de chamado'

    def __str__(self) -> str:
        return self.nome


class InformativeMessage(models.Model):
    """
    Mensagens informativas trocadas no chat 'Central Informativa'.
    """
    VALIDADE_PADRAO = timedelta(hours=2)

    text = models.TextField(help_text='Conteúdo da mensagem.')
    palavras_chave = models.CharField(
        max_length=400,
        blank=True,
        default='',
        help_text='Palavras-chave geradas pelo sistema (vírgula) para o Assistente.',
    )
    valido_ate = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Validade do comunicado (padrão: 2h após a criação).',
    )
    ativo = models.BooleanField(
        default=True,
        help_text='Espelho de não-arquivado (IA e listagens legadas).',
    )
    arquivado = models.BooleanField(
        default=False,
        help_text='Arquivado: oculto de usuários comuns e fora da IA.',
    )
    letreiro = models.BooleanField(
        default=False,
        help_text='Exibir no letreiro neon do header do Helpdesk.',
    )
    arquivado_em = models.DateTimeField(null=True, blank=True)
    arquivado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='informativos_arquivados',
        help_text='Quem arquivou manualmente (null se expirou automaticamente).',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='informative_messages',
        help_text='Usuário que enviou a mensagem.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='acknowledged_info_msgs',
        blank=True,
        help_text='Usuários que deram OK na mensagem.',
    )

    class Meta:
        ordering = ['created_at']
        verbose_name = 'mensagem informativa'
        verbose_name_plural = 'mensagens informativas'

    def __str__(self):
        return f'Info by {self.created_by.username} at {self.created_at}'

    @property
    def vigente(self) -> bool:
        """True se não arquivado e ainda dentro da validade."""
        if self.arquivado or not self.ativo:
            return False
        if self.valido_ate is None:
            return True
        return self.valido_ate >= timezone.now()

    def marcar_arquivado(self, *, por=None, agora=None):
        """Arquiva o comunicado (manual ou por expiração)."""
        agora = agora or timezone.now()
        self.arquivado = True
        self.ativo = False
        self.arquivado_em = agora
        self.arquivado_por = por
        self.save(update_fields=[
            'arquivado', 'ativo', 'arquivado_em', 'arquivado_por',
        ])

    def prorrogar(self, *, horas=2):
        """Prorroga a validade e desarquiva."""
        agora = timezone.now()
        self.valido_ate = agora + timedelta(hours=horas)
        self.arquivado = False
        self.ativo = True
        self.arquivado_em = None
        self.arquivado_por = None
        self.save(update_fields=[
            'valido_ate', 'arquivado', 'ativo', 'arquivado_em', 'arquivado_por',
        ])


class UserPresence(models.Model):
    """Heartbeat de presença online dos usuários (TI no helpdesk)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='presence',
        help_text='Usuário monitorado.',
    )
    last_seen = models.DateTimeField(
        db_index=True,
        help_text='Último heartbeat recebido.',
    )

    class Meta:
        verbose_name = 'presença de usuário'
        verbose_name_plural = 'presenças de usuários'

    def __str__(self) -> str:
        return f'{self.user_id} @ {self.last_seen}'
