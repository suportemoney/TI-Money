"""Middlewares do core."""
from __future__ import annotations

import traceback
from pathlib import Path

from django.conf import settings


class CaptureLast500Middleware:
    """
    Grava o último traceback de 500 em logs/last_500.txt
    para diagnóstico em produção (DEBUG=False).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        try:
            base = Path(settings.BASE_DIR)
            log_dir = base / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            destino = log_dir / 'last_500.txt'
            corpo = (
                f'path={getattr(request, "path", "")}\n'
                f'method={getattr(request, "method", "")}\n'
                f'user={getattr(getattr(request, "user", None), "username", "?")}\n'
                f'exception={type(exception).__name__}: {exception}\n\n'
                f'{traceback.format_exc()}\n'
            )
            destino.write_text(corpo, encoding='utf-8')
        except Exception:
            # Diagnóstico nunca pode derrubar o fluxo de erro
            pass
        return None
