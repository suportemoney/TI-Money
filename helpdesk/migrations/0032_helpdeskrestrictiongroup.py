# Generated manually — grupos de restrição dinâmica de chamados

from django.conf import settings
from django.db import migrations, models


def seed_grupo_inicial(apps, schema_editor):
    """Mantém o comportamento legado: user 25 visível só para o TI 2."""
    User = apps.get_model('core', 'CustomUser')
    Grupo = apps.get_model('helpdesk', 'HelpdeskRestrictionGroup')
    criador = User.objects.filter(pk=25).first()
    visualizador = User.objects.filter(pk=2).first()
    if not criador or not visualizador:
        return
    grupo = Grupo.objects.create(nome='Grupo 1', ativo=True)
    grupo.usuarios_restritos.add(criador)
    grupo.visualizadores.add(visualizador)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0031_informativemessage_dados_validade'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HelpdeskRestrictionGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(help_text='Nome exibido do grupo (ex.: Grupo 1).', max_length=80)),
                ('ativo', models.BooleanField(default=True, help_text='Se desligado, este grupo não restringe chamados.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('usuarios_restritos', models.ManyToManyField(
                    blank=True,
                    help_text='Chamados desses usuários só aparecem para os visualizadores do grupo.',
                    related_name='helpdesk_restriction_as_user',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('visualizadores', models.ManyToManyField(
                    blank=True,
                    help_text='Membros TI, staff ou superuser que veem os chamados restritos deste grupo.',
                    related_name='helpdesk_restriction_as_viewer',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'grupo de restrição de chamados',
                'verbose_name_plural': 'grupos de restrição de chamados',
                'ordering': ['pk'],
            },
        ),
        migrations.RunPython(seed_grupo_inicial, noop_reverse),
    ]
