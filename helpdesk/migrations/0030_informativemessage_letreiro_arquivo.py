import datetime

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def migrar_validade_e_arquivo(apps, schema_editor):
    InformativeMessage = apps.get_model('helpdesk', 'InformativeMessage')
    for msg in InformativeMessage.objects.all().iterator():
        mudou = False
        valido = msg.valido_ate
        # DateField antigo pode ter virado datetime à meia-noite
        if valido is None:
            base = msg.created_at or timezone.now()
            msg.valido_ate = base + datetime.timedelta(hours=2)
            mudou = True
        elif isinstance(valido, datetime.date) and not isinstance(valido, datetime.datetime):
            msg.valido_ate = timezone.make_aware(
                datetime.datetime.combine(valido, datetime.time(23, 59, 59))
            )
            mudou = True
        if not msg.ativo and not msg.arquivado:
            msg.arquivado = True
            msg.arquivado_em = msg.arquivado_em or timezone.now()
            mudou = True
        if mudou:
            msg.save(update_fields=['valido_ate', 'arquivado', 'arquivado_em'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('helpdesk', '0029_comment_structured_payload'),
    ]

    operations = [
        migrations.AddField(
            model_name='informativemessage',
            name='arquivado',
            field=models.BooleanField(
                default=False,
                help_text='Arquivado: oculto de usuários comuns e fora da IA.',
            ),
        ),
        migrations.AddField(
            model_name='informativemessage',
            name='letreiro',
            field=models.BooleanField(
                default=False,
                help_text='Exibir no letreiro neon do header do Helpdesk.',
            ),
        ),
        migrations.AddField(
            model_name='informativemessage',
            name='arquivado_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='informativemessage',
            name='arquivado_por',
            field=models.ForeignKey(
                blank=True,
                help_text='Quem arquivou manualmente (null se expirou automaticamente).',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='informativos_arquivados',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='informativemessage',
            name='valido_ate',
            field=models.DateTimeField(
                blank=True,
                help_text='Validade do comunicado (padrão: 2h após a criação).',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='informativemessage',
            name='ativo',
            field=models.BooleanField(
                default=True,
                help_text='Espelho de não-arquivado (IA e listagens legadas).',
            ),
        ),
        migrations.AlterField(
            model_name='informativemessage',
            name='palavras_chave',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Palavras-chave geradas pelo sistema (vírgula) para o Assistente.',
                max_length=400,
            ),
        ),
        migrations.RunPython(migrar_validade_e_arquivo, noop_reverse),
    ]
