import datetime

from django.db import migrations
from django.utils import timezone


def migrar_validade_e_arquivo(apps, schema_editor):
    """Dados legados: validade nula vira created_at+2h; inativos viram arquivados."""
    InformativeMessage = apps.get_model('helpdesk', 'InformativeMessage')
    for msg in InformativeMessage.objects.all().iterator():
        mudou = False
        valido = msg.valido_ate
        if valido is None:
            base = msg.created_at or timezone.now()
            msg.valido_ate = base + datetime.timedelta(hours=2)
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
        ('helpdesk', '0030_informativemessage_letreiro_arquivo'),
    ]

    operations = [
        migrations.RunPython(migrar_validade_e_arquivo, noop_reverse),
    ]
