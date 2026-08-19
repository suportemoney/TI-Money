"""Cliente HTTP da API B2B MoneyConsig."""

from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin

import requests

from integracoes.models import IntegracaoApi

logger = logging.getLogger(__name__)

TIMEOUT = 30
# Consultas em lote na tela do discador não podem usar o timeout cheio
TIMEOUT_CONSULTA_LOTE = 8
MAX_WORKERS_CONSULTA = 6
DEFAULT_BASE = 'https://sistema.moneypromotora.com.br'

_STATUS_INATIVO = {
    'inativo', 'inativa', 'inactive', 'desligado', 'desligada',
    'demitido', 'demitida', 'desativado', 'desativada',
}
_STATUS_ATIVO = {'ativo', 'ativa', 'active', 'ativado', 'ativada'}


def normalizar_base_url(url: str) -> str:
    """Garante scheme https:// e remove barra final.

    Aceita host sem scheme (ex.: sistema.moneypromotora.com.br).
    """
    base = (url or '').strip().rstrip('/')
    if not base:
        return DEFAULT_BASE
    low = base.lower()
    if low.startswith('http://') or low.startswith('https://'):
        return base
    # Host sem scheme → assume HTTPS
    return f'https://{base}'


def obter_integracao_moneyconsig() -> IntegracaoApi | None:
    """Retorna a primeira integração MoneyConsig ativa."""
    return (
        IntegracaoApi.objects.filter(
            provider=IntegracaoApi.Provider.MONEYCONSIG,
            is_active=True,
        )
        .order_by('name')
        .first()
    )


def moneyconsig_disponivel() -> bool:
    return obter_integracao_moneyconsig() is not None


def _erro(mensagem: str, **extra: Any) -> dict:
    return {'ok': False, 'erro': mensagem, **extra}


def _resolver_creds() -> tuple[str, str] | dict:
    """Devolve (base_url, api_token) ou dict de erro."""
    integracao = obter_integracao_moneyconsig()
    if not integracao:
        return _erro(
            'Nenhuma integração MoneyConsig ativa. '
            'Cadastre em Integrações → APIs.',
        )
    creds = integracao.get_credentials()
    token = (creds.get('api_token') or '').strip()
    if not token:
        return _erro('Token MoneyConsig ausente na integração cadastrada.')
    base = normalizar_base_url(creds.get('base_url') or DEFAULT_BASE)
    return base, token


def _headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }


def _request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: int | None = None,
    creds: tuple[str, str] | None = None,
) -> dict:
    if creds is None:
        resolved = _resolver_creds()
        if isinstance(resolved, dict):
            return resolved
        creds = resolved
    base, token = creds
    url = urljoin(base + '/', path.lstrip('/'))
    try:
        resp = requests.request(
            method,
            url,
            headers=_headers(token),
            params=params or None,
            json=json_body,
            timeout=TIMEOUT if timeout is None else timeout,
        )
    except requests.Timeout:
        logger.warning('MoneyConsig timeout: %s %s', method, path)
        return _erro('Timeout ao chamar a API MoneyConsig.')
    except requests.RequestException as exc:
        logger.warning('MoneyConsig rede: %s %s — %s', method, path, exc)
        return _erro(f'Falha de rede na API MoneyConsig: {exc}')

    if resp.status_code == 401:
        return _erro('Token MoneyConsig inválido ou sem permissão B2B (HTTP 401).', http_status=401)

    try:
        data = resp.json()
    except ValueError:
        return _erro(
            f'Resposta não-JSON da API MoneyConsig (HTTP {resp.status_code}).',
            http_status=resp.status_code,
        )

    if resp.status_code >= 400:
        msg = data.get('detail') or data.get('erro') or data.get('message') or resp.reason
        return _erro(str(msg), http_status=resp.status_code, resposta=data)

    if isinstance(data, dict):
        return {'ok': True, **data}
    return {'ok': True, 'data': data}


def auth_me() -> dict:
    """Valida o token e retorna identidade + escopo."""
    return _request('GET', '/api/b2b/auth/me/')


def usuarios_consulta(
    *,
    username: str = '',
    q: str = '',
    timeout: int | None = None,
    creds: tuple[str, str] | None = None,
) -> dict:
    """Consulta User/Funcionario no MoneyConsig (username e/ou q)."""
    username = (username or '').strip()
    q = (q or '').strip()
    if not username and not q:
        return _erro('Informe username e/ou q para consultar usuário.')
    params: dict[str, str] = {}
    if username:
        params['username'] = username
    if q:
        params['q'] = q
    return _request(
        'GET',
        '/api/b2b/usuarios/consulta/',
        params=params,
        timeout=timeout,
        creds=creds,
    )


def normalizar_nome_pessoa(nome: str) -> str:
    """Minúsculas, sem acento e espaços colapsados para comparar nomes."""
    texto = unicodedata.normalize('NFKD', (nome or '').strip())
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    texto = texto.lower().replace('junior', 'jr').replace('jr.', 'jr')
    texto = re.sub(r'[^a-z0-9]+', ' ', texto)
    return ' '.join(texto.split())


def _bool_campo(valor: Any) -> bool | None:
    """Interpreta flags de ativo/inativo da API. None = campo ausente/ambíguo."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)) and valor in (0, 1):
        return bool(valor)
    texto = str(valor).strip().lower()
    if texto in ('true', '1', 'sim', 'yes', 'ativo', 'ativa', 'active'):
        return True
    if texto in ('false', '0', 'nao', 'não', 'no', 'inativo', 'inativa', 'inactive'):
        return False
    return None


def _status_texto_inativo(valor: Any) -> bool | None:
    texto = str(valor or '').strip().lower()
    if not texto:
        return None
    if texto in _STATUS_INATIVO:
        return True
    if texto in _STATUS_ATIVO:
        return False
    return None


def _nome_do_registro(reg: dict) -> str:
    funcionario = reg.get('funcionario') if isinstance(reg.get('funcionario'), dict) else {}
    partes = [
        reg.get('nome'),
        reg.get('name'),
        reg.get('full_name'),
        reg.get('nome_completo'),
        funcionario.get('nome') if isinstance(funcionario, dict) else None,
        funcionario.get('nome_completo') if isinstance(funcionario, dict) else None,
    ]
    for parte in partes:
        if isinstance(parte, str) and parte.strip():
            return parte.strip()
    first = str(reg.get('first_name') or '').strip()
    last = str(reg.get('last_name') or '').strip()
    return f'{first} {last}'.strip()


def registros_da_consulta(payload: dict) -> list[dict]:
    """Extrai lista de usuários/funcionários de formatos comuns da API B2B."""
    if not isinstance(payload, dict) or not payload.get('ok'):
        return []
    if payload.get('encontrado') is False:
        return []
    for chave in ('results', 'usuarios', 'users', 'items'):
        valor = payload.get(chave)
        if isinstance(valor, list):
            return [item for item in valor if isinstance(item, dict)]
    for chave in ('data', 'usuario', 'user', 'funcionario'):
        valor = payload.get(chave)
        if isinstance(valor, list):
            return [item for item in valor if isinstance(item, dict)]
        if isinstance(valor, dict):
            return [valor]
    if any(
        chave in payload
        for chave in ('username', 'is_active', 'ativo', 'funcionario', 'nome', 'first_name')
    ):
        return [payload]
    return []


def registro_esta_inativo(reg: dict) -> bool | None:
    """True = inativo, False = ativo, None = não dá para afirmar."""
    if not isinstance(reg, dict):
        return None

    funcionario = reg.get('funcionario') if isinstance(reg.get('funcionario'), dict) else None
    user = None
    for chave in ('user', 'usuario'):
        if isinstance(reg.get(chave), dict):
            user = reg[chave]
            break

    fontes = [funcionario, user, reg]
    # Prioriza cadastro do funcionário; inativo pinta o nome no discador
    for fonte in fontes:
        if not isinstance(fonte, dict):
            continue
        for chave in ('ativo', 'is_active', 'active', 'user_is_active'):
            flag = _bool_campo(fonte.get(chave)) if chave in fonte else None
            if flag is False:
                return True
            if flag is True:
                return False
        for chave in ('status', 'situacao', 'situacao_funcionario'):
            if chave not in fonte:
                continue
            status = _status_texto_inativo(fonte.get(chave))
            if status is True:
                return True
            if status is False:
                return False
        if fonte.get('data_demissao') or fonte.get('demitido') is True:
            return True
    return None


def nomes_compativeis(busca: str, encontrado: str, *, exigir_exato: bool) -> bool:
    """Compara nomes normalizados. Com vários resultados, só aceita igualdade."""
    a = normalizar_nome_pessoa(busca)
    b = normalizar_nome_pessoa(encontrado)
    if not a or not b:
        return False
    if a == b:
        return True
    if exigir_exato:
        return False
    tokens_a = a.split()
    tokens_b = set(b.split())
    return len(tokens_a) >= 2 and all(token in tokens_b for token in tokens_a)


def escolher_registro_por_nome(registros: list[dict], nome: str) -> dict | None:
    """Escolhe um único registro compatível; ambiguidade = não encontrado."""
    if not registros:
        return None
    if len(registros) == 1:
        unico = registros[0]
        nome_reg = _nome_do_registro(unico)
        if not nome_reg or nomes_compativeis(nome, nome_reg, exigir_exato=False):
            return unico
        return None
    exatos = [
        reg for reg in registros
        if nomes_compativeis(nome, _nome_do_registro(reg), exigir_exato=True)
    ]
    if len(exatos) == 1:
        return exatos[0]
    return None


def consulta_indica_inativo(payload: dict, nome: str) -> bool:
    """True só quando achou o nome e o funcionário está inativo."""
    registro = escolher_registro_por_nome(registros_da_consulta(payload), nome)
    if not registro:
        return False
    return registro_esta_inativo(registro) is True


def status_inativos_por_nome(nomes: list[str]) -> dict[str, bool]:
    """Consulta nomes em paralelo. Chave normalizada → inativo.

    Omite nomes com falha de rede/API para não cachear erro como “ativo”.
    """
    unicos: list[str] = []
    vistos: set[str] = set()
    for nome in nomes:
        chave = normalizar_nome_pessoa(nome)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(nome)

    if not unicos:
        return {}

    resolved = _resolver_creds()
    if isinstance(resolved, dict):
        logger.info(
            'MoneyConsig indisponível para checagem de inativos: %s',
            resolved.get('erro'),
        )
        return {}

    resultado: dict[str, bool] = {}

    def _consultar(nome: str) -> tuple[str, bool | None]:
        chave = normalizar_nome_pessoa(nome)
        payload = usuarios_consulta(
            q=nome,
            timeout=TIMEOUT_CONSULTA_LOTE,
            creds=resolved,
        )
        if not isinstance(payload, dict) or not payload.get('ok'):
            return chave, None
        return chave, consulta_indica_inativo(payload, nome)

    workers = min(MAX_WORKERS_CONSULTA, len(unicos))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futuros = {pool.submit(_consultar, nome): nome for nome in unicos}
        for futuro in as_completed(futuros):
            try:
                chave, inativo = futuro.result()
            except Exception:
                logger.warning(
                    'Falha ao consultar MoneyConsig para "%s"',
                    futuros[futuro],
                    exc_info=True,
                )
                continue
            if inativo is not None:
                resultado[chave] = inativo
    return resultado


def alerta_ti_listar(*, limite: int = 50) -> dict:
    """Lista alertas TI do escopo do token."""
    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = 50
    limite = max(1, min(100, limite))
    return _request('GET', '/api/b2b/alerta-ti/', params={'limite': limite})


def alerta_ti_criar(
    *,
    mensagem: str,
    tipo_destinatario: str,
    destinatarios_ids: list[int] | None = None,
) -> dict:
    """Cria alerta TI no MoneyConsig."""
    mensagem = (mensagem or '').strip()
    tipo_destinatario = (tipo_destinatario or '').strip()
    if not mensagem:
        return _erro('Informe a mensagem do alerta.')
    if not tipo_destinatario:
        return _erro('Informe tipo_destinatario.')
    ids = destinatarios_ids or []
    if not isinstance(ids, list) or not ids:
        return _erro('Informe destinatarios_ids (lista de inteiros).')
    try:
        ids_int = [int(x) for x in ids]
    except (TypeError, ValueError):
        return _erro('destinatarios_ids deve ser lista de inteiros.')
    return _request(
        'POST',
        '/api/b2b/alerta-ti/',
        json_body={
            'mensagem': mensagem,
            'tipo_destinatario': tipo_destinatario,
            'destinatarios_ids': ids_int,
        },
    )


def alerta_ti_destinatarios(
    tipo: str,
    *,
    empresas: str = '',
    departamentos: str = '',
    setores: str = '',
    cargos: str = '',
) -> dict:
    """Cascata de destinatários filtrada por escopo."""
    tipo = (tipo or '').strip().lower()
    permitidos = {
        'funcionarios', 'empresas', 'departamentos',
        'setores', 'lojas', 'equipes', 'cargos',
    }
    if tipo not in permitidos:
        return _erro(
            f'tipo inválido. Use um de: {", ".join(sorted(permitidos))}.',
        )
    params: dict[str, str] = {}
    for key, val in (
        ('empresas', empresas),
        ('departamentos', departamentos),
        ('setores', setores),
        ('cargos', cargos),
    ):
        val = (val or '').strip()
        if val:
            params[key] = val
    return _request(
        'GET',
        f'/api/b2b/alerta-ti/destinatarios/{tipo}/',
        params=params or None,
    )
