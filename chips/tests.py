from django.test import TestCase

from chips.models import Batch, Chip, ChipMovement, Operator
from chips.queries import chip_para_grid_dict, chips_com_anotacoes_operacionais
from chips.services import devolver_para_ti, entregar_chip
from core.models import CustomUser


class DevolverChipTitularTestCase(TestCase):
    """Devolver à TI limpa o titular no grid e preserva o movimento RETURN."""

    def setUp(self):
        self.operator = Operator.objects.create(name='TIM')
        self.batch = Batch.objects.create(nome='Envelope TI')
        self.actor = CustomUser.objects.create_user(
            username='ti.chips',
            password='x',
            role=CustomUser.RoleChoices.IT_USER,
        )
        self.chip = Chip.objects.create(
            line_number='11988887777',
            operator=self.operator,
            batch=self.batch,
            usage_status=Chip.UsageChoices.AVAILABLE,
        )

    def test_devolver_para_ti_limpa_titular_e_mantem_return(self):
        entregar_chip(
            self.chip,
            employee_name='Isadora Paim',
            actor=self.actor,
        )
        self.chip.refresh_from_db()

        anotado_em_uso = chips_com_anotacoes_operacionais(
            Chip.objects.filter(pk=self.chip.pk)
        ).get()
        self.assertEqual(anotado_em_uso.usage_status, Chip.UsageChoices.IN_USE)
        self.assertEqual(anotado_em_uso.employee_name, 'Isadora Paim')

        devolver_para_ti(self.chip, actor=self.actor, batch=self.batch)
        self.chip.refresh_from_db()

        anotado = chips_com_anotacoes_operacionais(
            Chip.objects.filter(pk=self.chip.pk)
        ).get()
        self.assertEqual(anotado.usage_status, Chip.UsageChoices.AVAILABLE)
        self.assertEqual(anotado.employee_name or '', '')
        self.assertIsNone(anotado.employee_user_id)

        grid = chip_para_grid_dict(anotado)
        self.assertEqual(grid['usage_status'], Chip.UsageChoices.AVAILABLE)
        self.assertEqual(grid['employee_name'], '')
        self.assertIsNone(grid['employee_user_id'])

        self.assertTrue(
            ChipMovement.objects.filter(
                chip=self.chip,
                action=ChipMovement.ActionChoices.RETURN,
                employee_name='Isadora Paim',
            ).exists()
        )
        self.assertTrue(
            ChipMovement.objects.filter(
                chip=self.chip,
                action=ChipMovement.ActionChoices.DELIVERY,
            ).exists()
        )
