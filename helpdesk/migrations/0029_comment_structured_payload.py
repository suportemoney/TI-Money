from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0028_ticket_assistente_chip_auth'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='structured_payload',
            field=models.JSONField(
                blank=True,
                help_text=(
                    'Payload estruturado do Assistente (questionário com opções ou '
                    'bloco de esclarecimento). Comentários antigos ficam nulos.'
                ),
                null=True,
            ),
        ),
    ]
