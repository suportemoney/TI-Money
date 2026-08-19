"""Gate do wizard flutuante de gestão (allowlist por user id)."""

from django.conf import settings


def ids_wizard_gestor() -> list[int]:
    """IDs permitidos (setting GESTOR_WIZARD_USER_IDS, padrão [2])."""
    bruto = getattr(settings, 'GESTOR_WIZARD_USER_IDS', [2]) or [2]
    ids: list[int] = []
    for item in bruto:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids or [2]


def usuario_pode_wizard(user) -> bool:
    """Só usuários autenticados cuja pk está na allowlist."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    pk = getattr(user, 'pk', None)
    if pk is None:
        return False
    return int(pk) in ids_wizard_gestor()
