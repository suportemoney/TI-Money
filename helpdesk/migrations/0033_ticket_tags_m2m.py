# Funil passa a ser multi-select: FK única → M2M

from django.db import migrations, models


def copiar_tag_fk_para_m2m(apps, schema_editor):
    """Copia a tag única antiga para a relação muitos-para-muitos."""
    Ticket = apps.get_model('helpdesk', 'Ticket')
    Through = Ticket.tags.through
    pares = list(
        Ticket.objects.exclude(tag_id=None).values_list('id', 'tag_id')
    )
    if not pares:
        return
    Through.objects.bulk_create(
        [Through(ticket_id=tid, tickettag_id=tag_id) for tid, tag_id in pares],
        ignore_conflicts=True,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0032_helpdeskrestrictiongroup'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='tags',
            field=models.ManyToManyField(
                blank=True,
                help_text='Tags de funil/follow-up do chamado (várias permitidas).',
                related_name='tickets_m2m',
                to='helpdesk.tickettag',
            ),
        ),
        migrations.RunPython(copiar_tag_fk_para_m2m, noop_reverse),
        migrations.RemoveField(
            model_name='ticket',
            name='tag',
        ),
        # related_name é só estado Python — não recria a tabela M2M
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name='ticket',
                    name='tags',
                    field=models.ManyToManyField(
                        blank=True,
                        help_text='Tags de funil/follow-up do chamado (várias permitidas).',
                        related_name='tickets',
                        to='helpdesk.tickettag',
                    ),
                ),
            ],
        ),
    ]
