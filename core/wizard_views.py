"""Endpoints JSON do wizard flutuante de gestão."""

from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from core.wizard import usuario_pode_wizard
from integracoes.gestor_runtime import SESSION_KEY, MAX_HISTORICO, processar_gestor
from integracoes.llm import LlmError
from integracoes.markdown_safe import render_markdown_leve
from integracoes.wizard_anexos import extrair_anexos_wizard

logger = logging.getLogger(__name__)

SESSION_PLANO = 'gestor_wizard_plano'


def _json_body(request) -> dict:
    try:
        if request.body:
            return json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {}


def _recusar_wizard(request):
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'Faça login.'}, status=401)
    if not usuario_pode_wizard(request.user):
        return JsonResponse({'ok': False, 'error': 'Sem permissão para o wizard.'}, status=403)
    return None


@login_required
@require_http_methods(['GET', 'POST'])
def wizard_chat(request):
    """GET: histórico da sessão. POST: envia mensagem + snapshot da página."""
    recusa = _recusar_wizard(request)
    if recusa:
        return recusa

    if request.method == 'GET':
        historico = list(request.session.get(SESSION_KEY) or [])
        payload = []
        for item in historico:
            row = dict(item)
            if row.get('role') == 'assistant':
                row['content_html'] = render_markdown_leve(row.get('content') or '')
            payload.append(row)
        return JsonResponse({
            'ok': True,
            'messages': payload,
            'plano': list(request.session.get(SESSION_PLANO) or []),
            'aguardando_confirmacao': bool(request.session.get(SESSION_PLANO)),
        })

    body = _json_body(request)
    anexos = body.get('anexos') if isinstance(body.get('anexos'), list) else []
    mensagem = (body.get('message') or '').strip()
    if not mensagem:
        if anexos:
            mensagem = 'Analise os arquivos anexados.'
        else:
            return JsonResponse({'ok': False, 'error': 'Mensagem vazia.'}, status=400)

    pagina = body.get('pagina') if isinstance(body.get('pagina'), dict) else {}
    historico = list(request.session.get(SESSION_KEY) or [])
    anexos_texto = extrair_anexos_wizard(anexos)
    nomes = [
        (item.get('nome') or 'arquivo')
        for item in anexos[:4]
        if isinstance(item, dict)
    ]
    texto_historico = mensagem
    if nomes:
        texto_historico = f'{mensagem}\n📎 {", ".join(nomes)}'

    try:
        resultado = processar_gestor(
            user=request.user,
            mensagem=mensagem,
            historico=historico,
            pagina=pagina,
            anexos_texto=anexos_texto,
        )
    except LlmError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
    except Exception:
        logger.exception('Falha no wizard gestor')
        return JsonResponse({'ok': False, 'error': 'Erro ao processar o wizard.'}, status=500)

    reply = resultado.get('reply') or ''
    historico.append({'role': 'user', 'content': texto_historico})
    historico.append({'role': 'assistant', 'content': reply})
    request.session[SESSION_KEY] = historico[-MAX_HISTORICO:]
    plano = resultado.get('plano') or []
    if resultado.get('aguardando_confirmacao'):
        request.session[SESSION_PLANO] = plano
    else:
        request.session[SESSION_PLANO] = []
    request.session.modified = True

    return JsonResponse({
        'ok': True,
        'reply': reply,
        'reply_html': render_markdown_leve(reply),
        'mutacoes': resultado.get('mutacoes') or [],
        'plano': plano,
        'aguardando_confirmacao': bool(resultado.get('aguardando_confirmacao')),
    })


@login_required
@require_POST
def wizard_chat_limpar(request):
    """Zera o histórico da sessão do wizard."""
    recusa = _recusar_wizard(request)
    if recusa:
        return recusa
    request.session[SESSION_KEY] = []
    request.session[SESSION_PLANO] = []
    request.session.modified = True
    return JsonResponse({'ok': True, 'messages': []})
