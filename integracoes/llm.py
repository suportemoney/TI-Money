"""Cliente HTTP OpenAI-compatible (DeepSeek e similares) + visão multimodal."""

from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from integracoes.models import AssistenteConfig, IntegracaoIA

logger = logging.getLogger(__name__)

# DeepSeek (api.deepseek.com) é só texto — image_url é rejeitado pelo schema.
PROVEDORES_COM_VISAO = frozenset({
    IntegracaoIA.Provider.CHATGPT,
    IntegracaoIA.Provider.GEMINI,
    IntegracaoIA.Provider.GROK,
})


class LlmError(Exception):
    pass


def provedor_tem_visao(provider: str) -> bool:
    return provider in PROVEDORES_COM_VISAO


def obter_integracao_ativa() -> IntegracaoIA | None:
    config = AssistenteConfig.get_solo()
    if config.integracao_id and config.integracao and config.integracao.is_active:
        return config.integracao
    return (
        IntegracaoIA.objects.filter(is_active=True, provider=IntegracaoIA.Provider.DEEPSEEK)
        .order_by('-updated_at')
        .first()
        or IntegracaoIA.objects.filter(is_active=True).order_by('-updated_at').first()
    )


def obter_integracao_visao() -> IntegracaoIA | None:
    """
    Integração multimodal para ler prints.
    Prioridade: integracao_visao → integração principal (se multimodal) → qualquer ativa com visão.
    """
    config = AssistenteConfig.get_solo()
    visao = getattr(config, 'integracao_visao', None)
    if visao is not None and visao.is_active and provedor_tem_visao(visao.provider):
        return visao

    ativa = obter_integracao_ativa()
    if ativa and provedor_tem_visao(ativa.provider):
        return ativa

    return (
        IntegracaoIA.objects.filter(is_active=True, provider__in=PROVEDORES_COM_VISAO)
        .order_by('-updated_at')
        .first()
    )


def _resolver_endpoint_e_modelo(integracao: IntegracaoIA) -> tuple[str, str, str]:
    from integracoes.providers import base_url_do_provedor, normalizar_modelo_para_api

    creds = integracao.get_credentials()
    api_key = (creds.get('api_key') or '').strip()
    if not api_key:
        raise LlmError('Integração sem api_key.')
    base_url = (creds.get('base_url') or '').strip().rstrip('/')
    if not base_url:
        base_url = (base_url_do_provedor(integracao.provider) or '').rstrip('/')
    if not base_url:
        raise LlmError('Integração sem base_url.')
    models = creds.get('models') or []
    if models:
        model = str(models[0]).strip()
    elif integracao.provider == IntegracaoIA.Provider.DEEPSEEK:
        model = 'deepseek-v4-flash'
    elif integracao.provider == IntegracaoIA.Provider.CHATGPT:
        model = 'gpt-4o-mini'
    elif integracao.provider == IntegracaoIA.Provider.GEMINI:
        model = 'gemini-2.0-flash'
    else:
        model = 'gpt-4o-mini'
    # Aliases legados (ex.: deepseek-chat) → id aceito pela API atual
    model = normalizar_modelo_para_api(integracao.provider, model)
    return api_key, base_url, model


def _resolver_modelo_visao(integracao: IntegracaoIA) -> tuple[str, str, str]:
    """Escolhe modelo multimodal na integração (nunca DeepSeek)."""
    if not provedor_tem_visao(integracao.provider):
        raise LlmError(
            f'O provedor {integracao.get_provider_display()} não aceita imagem na API. '
            'Cadastre ChatGPT (gpt-4o) ou Gemini para ler prints.'
        )

    api_key, base_url, model_texto = _resolver_endpoint_e_modelo(integracao)
    creds = integracao.get_credentials()
    models = [str(m).strip() for m in (creds.get('models') or []) if str(m).strip()]

    preferidos = ('gpt-4o', 'gemini', 'vision', 'vl', 'flash')
    for m in models:
        low = m.lower()
        if any(x in low for x in preferidos):
            return api_key, base_url, m

    if integracao.provider == IntegracaoIA.Provider.GEMINI:
        return api_key, base_url, models[0] if models else 'gemini-2.0-flash'
    if integracao.provider == IntegracaoIA.Provider.CHATGPT:
        return api_key, base_url, models[0] if models else 'gpt-4o-mini'
    if integracao.provider == IntegracaoIA.Provider.GROK:
        return api_key, base_url, models[0] if models else 'grok-2-vision-latest'

    return api_key, base_url, model_texto or (models[0] if models else 'gpt-4o-mini')


def thinking_payload_para_provedor(provider: str) -> dict | None:
    """
    DeepSeek V4 liga thinking por padrão (reasoning_content).
    Isso consome o budget, atrasa a chamada e deixa content/tool_calls vazios
    — o Assistente fica 'ativo' sem postar no chat. Desliga no helpdesk.
    """
    if provider == IntegracaoIA.Provider.DEEPSEEK:
        return {'type': 'disabled'}
    return None


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.4,
    timeout: float = 75.0,
) -> dict[str, Any]:
    """
    Chama /chat/completions. Retorna o objeto message da choice[0]
    (content + tool_calls possíveis).
    """
    integracao = obter_integracao_ativa()
    if not integracao:
        raise LlmError('Nenhuma integração IA ativa.')

    api_key, base_url, model = _resolver_endpoint_e_modelo(integracao)
    url = urljoin(base_url + '/', 'chat/completions')
    payload: dict[str, Any] = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
    }
    thinking = thinking_payload_para_provedor(integracao.provider)
    if thinking:
        payload['thinking'] = thinking
    if tools:
        payload['tools'] = tools
        payload['tool_choice'] = 'auto'

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.exception('Falha de rede no LLM')
        raise LlmError(f'Falha de rede: {exc}') from exc

    if resp.status_code >= 400:
        corpo = (resp.text or '')[:500]
        logger.error('LLM HTTP %s model=%s: %s', resp.status_code, model, corpo)
        detalhe = ''
        try:
            err = resp.json().get('error') or {}
            if isinstance(err, dict):
                detalhe = (err.get('message') or '').strip()
            elif isinstance(err, str):
                detalhe = err.strip()
        except Exception:
            detalhe = ''
        if detalhe:
            raise LlmError(f'LLM retornou HTTP {resp.status_code}: {detalhe[:240]}')
        raise LlmError(f'LLM retornou HTTP {resp.status_code}')

    data = resp.json()
    try:
        return data['choices'][0]['message']
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError('Resposta LLM inválida.') from exc


def chat_text(messages: list[dict[str, Any]], **kwargs) -> str:
    msg = chat_completion(messages, tools=None, **kwargs)
    return (msg.get('content') or '').strip()


def _visao_openai_compatible(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    image_bytes: bytes,
    mime: str,
    timeout: float,
) -> str:
    b64 = base64.b64encode(image_bytes).decode('ascii')
    data_url = f'data:{mime};base64,{b64}'
    url = urljoin(base_url + '/', 'chat/completions')
    payload = {
        'model': model,
        'temperature': 0.2,
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'image_url', 'image_url': {'url': data_url}},
                    {'type': 'text', 'text': (prompt or '').strip() or 'Descreva a imagem.'},
                ],
            }
        ],
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    logger.info('LLM visão (OpenAI-compat) model=%s mime=%s bytes=%s', model, mime, len(image_bytes))
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.exception('Falha de rede no LLM visão')
        raise LlmError(f'Falha de rede (visão): {exc}') from exc

    if resp.status_code >= 400:
        logger.error('LLM visão HTTP %s model=%s: %s', resp.status_code, model, resp.text[:800])
        raise LlmError(
            f'Visão indisponível (HTTP {resp.status_code}, modelo {model}). '
            'Cadastre ChatGPT (gpt-4o) ou Gemini na integração de visão.'
        )

    data = resp.json()
    try:
        content = data['choices'][0]['message'].get('content') or ''
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError('Resposta de visão inválida.') from exc
    texto = content.strip() if isinstance(content, str) else str(content)
    if not texto:
        raise LlmError('Modelo de visão retornou descrição vazia.')
    return texto


def _visao_gemini(
    *,
    api_key: str,
    model: str,
    prompt: str,
    image_bytes: bytes,
    mime: str,
    timeout: float,
) -> str:
    """Gemini generateContent com inline_data (visão nativa)."""
    b64 = base64.b64encode(image_bytes).decode('ascii')
    # model pode vir como "models/gemini-2.0-flash" ou só o id
    model_id = model.split('/')[-1] if model else 'gemini-2.0-flash'
    url = (
        f'https://generativelanguage.googleapis.com/v1beta/models/'
        f'{model_id}:generateContent'
    )
    payload = {
        'contents': [
            {
                'parts': [
                    {'inline_data': {'mime_type': mime, 'data': b64}},
                    {'text': (prompt or '').strip() or 'Descreva a imagem.'},
                ],
            }
        ],
        'generationConfig': {'temperature': 0.2},
    }
    logger.info('LLM visão (Gemini) model=%s mime=%s bytes=%s', model_id, mime, len(image_bytes))
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, params={'key': api_key}, json=payload)
    except httpx.HTTPError as exc:
        logger.exception('Falha de rede no Gemini visão')
        raise LlmError(f'Falha de rede (visão Gemini): {exc}') from exc

    if resp.status_code >= 400:
        logger.error('Gemini visão HTTP %s: %s', resp.status_code, resp.text[:800])
        raise LlmError(f'Visão Gemini indisponível (HTTP {resp.status_code}).')

    data = resp.json()
    try:
        parts = data['candidates'][0]['content']['parts']
        textos = [p.get('text', '') for p in parts if isinstance(p, dict)]
        texto = '\n'.join(t for t in textos if t).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError('Resposta de visão Gemini inválida.') from exc
    if not texto:
        raise LlmError('Gemini retornou descrição vazia.')
    return texto


def chat_completion_vision(
    prompt: str,
    image_bytes: bytes,
    mime: str = 'image/jpeg',
    *,
    timeout: float = 90.0,
) -> str:
    """
    Descreve uma imagem via provedor multimodal (ChatGPT / Gemini / Grok).
    DeepSeek não entra aqui — a API oficial é só texto.
    """
    integracao = obter_integracao_visao()
    if not integracao:
        raise LlmError(
            'Nenhuma integração com visão configurada. '
            'DeepSeek lê só texto: cadastre ChatGPT (gpt-4o) ou Gemini em '
            'Integrações → Aprendizado IA → Integração de visão (prints).'
        )

    if not image_bytes:
        raise LlmError('Imagem vazia.')
    if len(image_bytes) > 5 * 1024 * 1024:
        raise LlmError('Imagem maior que 5MB.')

    mime = (mime or 'image/jpeg').split(';')[0].strip().lower() or 'image/jpeg'
    if mime == 'application/octet-stream':
        mime = 'image/jpeg'
    if not mime.startswith('image/'):
        raise LlmError(f'Arquivo não é imagem (mime={mime}).')

    api_key, base_url, model = _resolver_modelo_visao(integracao)

    if integracao.provider == IntegracaoIA.Provider.GEMINI:
        return _visao_gemini(
            api_key=api_key,
            model=model,
            prompt=prompt,
            image_bytes=image_bytes,
            mime=mime,
            timeout=timeout,
        )

    return _visao_openai_compatible(
        api_key=api_key,
        base_url=base_url,
        model=model,
        prompt=prompt,
        image_bytes=image_bytes,
        mime=mime,
        timeout=timeout,
    )


MODELO_EMBEDDING_PADRAO = 'text-embedding-3-small'


def obter_integracao_embedding() -> IntegracaoIA | None:
    """Integração com API de embeddings OpenAI-compatible (ChatGPT)."""
    return (
        IntegracaoIA.objects.filter(
            is_active=True,
            provider=IntegracaoIA.Provider.CHATGPT,
        )
        .order_by('-updated_at')
        .first()
    )


def obter_embedding(texto: str, *, timeout: float = 30.0) -> tuple[list[float], str]:
    """
    Gera embedding via POST /embeddings (OpenAI-compatible).
    Retorna (vetor, nome_do_modelo).
    """
    texto = (texto or '').strip()
    if not texto:
        raise LlmError('Texto vazio para embedding.')

    integracao = obter_integracao_embedding()
    if not integracao:
        raise LlmError(
            'Nenhuma integração ChatGPT ativa para embeddings. '
            'Cadastre ChatGPT (text-embedding-3-small) ou use só keyword.'
        )

    api_key, base_url, _ = _resolver_endpoint_e_modelo(integracao)
    # Embeddings usam modelo dedicado, não o de chat
    modelo = MODELO_EMBEDDING_PADRAO
    url = urljoin(base_url.rstrip('/') + '/', 'embeddings')
    payload = {
        'model': modelo,
        'input': texto[:8000],
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.exception('Falha de rede no embedding')
        raise LlmError(f'Falha de rede no embedding: {exc}') from exc

    if resp.status_code >= 400:
        corpo = (resp.text or '')[:400]
        logger.error('Embedding HTTP %s: %s', resp.status_code, corpo)
        raise LlmError(f'Embedding retornou HTTP {resp.status_code}')

    data = resp.json()
    try:
        vetor = data['data'][0]['embedding']
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError('Resposta de embedding inválida.') from exc
    if not isinstance(vetor, list) or not vetor:
        raise LlmError('Vetor de embedding vazio.')
    return [float(x) for x in vetor], modelo

