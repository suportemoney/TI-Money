"""Operações de domínio do módulo chips."""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import CustomUser
from chips.audit import (
    log_devolucao,
    log_entrega,
    log_transferencia,
)
from chips.models import Batch, Chip, ChipMovement
from chips.queries import chip_para_grid_dict, chips_com_anotacoes_operacionais


def _titular_atual(chip):
    """Retorna o último movimento de entrega/transferência."""
    return chip.movements.filter(
        action__in=[
            ChipMovement.ActionChoices.DELIVERY,
            ChipMovement.ActionChoices.TRANSFER,
        ],
    ).order_by('-timestamp').first()


@transaction.atomic
def entregar_chip(
    chip,
    *,
    employee_name,
    employee_user=None,
    employee_operador=None,
    actor,
    activated_at=None,
):
    """Entrega chip disponível na TI para uma pessoa."""
    if chip.usage_status != Chip.UsageChoices.AVAILABLE:
        raise ValidationError('Somente chips disponíveis na TI podem ser entregues.')

    hoje = activated_at or timezone.localdate()
    if not chip.activated_at:
        chip.activated_at = hoje

    chip.usage_status = Chip.UsageChoices.IN_USE
    chip.save()

    ChipMovement.objects.create(
        chip=chip,
        employee_name=employee_name,
        employee_user=employee_user,
        employee_operador=employee_operador,
        action=ChipMovement.ActionChoices.DELIVERY,
        registered_by=actor,
    )
    log_entrega(chip, employee_name, actor)
    return _chip_grid(chip.pk)


@transaction.atomic
def transferir_chip(chip, *, novo_nome, novo_user=None, novo_operador=None, actor, preserve_movement=False):
    """Transfere posse sem alterar activated_at nem ciclo de recarga."""
    if chip.usage_status != Chip.UsageChoices.IN_USE:
        raise ValidationError('Somente chips em uso podem ser transferidos.')

    anterior = _titular_atual(chip)
    nome_anterior = anterior.employee_name if anterior else 'Desconhecido'
    
    if preserve_movement and anterior:
        anterior.employee_name = novo_nome
        anterior.employee_user = novo_user
        anterior.employee_operador = novo_operador
        # Opcional: atualizar quem registrou a mudança?
        # anterior.registered_by = actor
        anterior.save(update_fields=['employee_name', 'employee_user', 'employee_operador'])
    else:
        ChipMovement.objects.create(
            chip=chip,
            employee_name=novo_nome,
            employee_user=novo_user,
            employee_operador=novo_operador,
            action=ChipMovement.ActionChoices.TRANSFER,
            registered_by=actor,
        )
        
    log_transferencia(chip, nome_anterior, novo_nome, actor)
    return _chip_grid(chip.pk)


@transaction.atomic
def devolver_para_ti(chip, *, actor, batch=None):
    """Devolve chip da rua para envelope na TI."""
    if chip.usage_status != Chip.UsageChoices.IN_USE:
        raise ValidationError('Somente chips em uso podem ser devolvidos.')

    anterior = _titular_atual(chip)
    nome_anterior = anterior.employee_name if anterior else 'Desconhecido'

    ChipMovement.objects.create(
        chip=chip,
        employee_name=nome_anterior,
        employee_user=anterior.employee_user if anterior else None,
        employee_operador=anterior.employee_operador if anterior else None,
        action=ChipMovement.ActionChoices.RETURN,
        registered_by=actor,
    )
    log_devolucao(chip, nome_anterior, actor)

    if chip.status in [Chip.StatusChoices.CANCELED, Chip.StatusChoices.LOST]:
        chip.usage_status = Chip.UsageChoices.UNAVAILABLE
        # Envelope opcional quando o chip fica indisponível
        if batch is not None:
            chip.batch = batch
    else:
        # Chip ativo volta AVAILABLE e exige envelope físico na TI
        if batch is None:
            raise ValidationError('Selecione o envelope para devolver o chip à TI.')
        chip.batch = batch
        chip.usage_status = Chip.UsageChoices.AVAILABLE

    chip.full_clean()
    chip.save()
    return _chip_grid(chip.pk)


@transaction.atomic
def registrar_bloqueio(chip, actor):
    """Marca chip como bloqueado e registra data."""
    chip.status = Chip.StatusChoices.BANNED
    chip.last_blocked_at = timezone.now()
    chip.save()
    return chip


@transaction.atomic
def criar_chip_operacional(
    *,
    line_number,
    operator,
    employee_name='',
    employee_user=None,
    employee_operador=None,
    activated_at=None,
    batch=None,
    observacao='',
    email_vinculado=None,
    actor,
):
    """Cria chip e posiciona na TI ou entrega direto ao callcenter."""
    chip = Chip.objects.create(
        line_number=line_number.strip(),
        operator=operator,
        batch=batch,
        observacao=observacao.strip(),
        email_vinculado=email_vinculado,
        usage_status=Chip.UsageChoices.IN_USE if employee_name.strip() else Chip.UsageChoices.AVAILABLE,
        activated_at=(
            activated_at or timezone.localdate()
            if employee_name.strip()
            else None
        ),
    )

    if employee_name.strip():
        ChipMovement.objects.create(
            chip=chip,
            employee_name=employee_name,
            employee_user=employee_user,
            employee_operador=employee_operador,
            action=ChipMovement.ActionChoices.DELIVERY,
            registered_by=actor,
        )
        log_entrega(chip, employee_name, actor)

    from chips.audit import log_chip_criado
    log_chip_criado(chip, actor)
    return _chip_grid(chip.pk)


@transaction.atomic
def atualizar_chip_grid(chip, *, dados, actor):
    """Atualiza campos editáveis inline do grid."""
    from chips.audit import log_chip_atualizado

    antes = Chip.objects.get(pk=chip.pk)
    alterou = False

    if 'line_number' in dados and dados['line_number']:
        chip.line_number = str(dados['line_number']).strip()
        alterou = True

    if 'operator_id' in dados:
        op_id = _inteiro_opcional(dados['operator_id'])
        if op_id is None:
            raise ValidationError('Selecione uma operadora válida.')
        chip.operator_id = op_id
        alterou = True

    if 'activated_at' in dados:
        chip.activated_at = _parse_data_grid(dados.get('activated_at'))
        alterou = True


    if 'batch_id' in dados:
        chip.batch_id = _inteiro_opcional(dados.get('batch_id'))
        alterou = True

    if 'employee_name' in dados and chip.usage_status == Chip.UsageChoices.IN_USE:
        nome = (dados.get('employee_name') or '').strip()
        if nome:
            atual = _titular_atual(chip)
            if not atual or atual.employee_name != nome:
                if alterou:
                    chip.save()
                log_chip_atualizado(chip, actor, antes)
                return transferir_chip(chip, novo_nome=nome, novo_user=None, actor=actor)
        alterou = True

    if alterou:
        chip.save()

    chip.refresh_from_db()
    log_chip_atualizado(chip, actor, antes)
    return _chip_grid(chip.pk)


def _chip_grid(pk):
    chip = chips_com_anotacoes_operacionais(Chip.objects.filter(pk=pk)).first()
    return chip_para_grid_dict(chip) if chip else {}


def _inteiro_opcional(valor):
    """Converte valor do JSON do grid para int ou None."""
    if valor is None or valor == '':
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _parse_data_grid(valor):
    """Interpreta data enviada pelo grid (ISO ou vazio)."""
    if not valor:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError as exc:
        raise ValidationError('Data de ativação inválida. Use AAAA-MM-DD.') from exc


@transaction.atomic
def recalcular_status_chips():
    """
    Verifica chips ativos no sistema.
    Se a recarga excedeu 90 dias (ciclo status == 'overdue'),
    altera o status do chip para CANCELED (Cancelado).
    """
    from chips.queries import _calcular_ciclo
    chips = chips_com_anotacoes_operacionais(
        Chip.objects.filter(is_active=True).exclude(
            status__in=[Chip.StatusChoices.CANCELED, Chip.StatusChoices.BANNED]
        )
    )
    cancelados_count = 0
    for chip in chips:
        _, _, cycle_status = _calcular_ciclo(chip)
        if cycle_status == 'overdue':
            chip.status = Chip.StatusChoices.CANCELED
            chip.save()
            cancelados_count += 1
    return cancelados_count

