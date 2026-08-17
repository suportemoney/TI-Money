from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from helpdesk.assistente_services import (
    AssistenteServiceError,
    atualizar_descricao_chamado,
    atualizar_solicitante,
    consultar_chips,
    consultar_usuario,
    descrever_imagem_anexo,
    escalar_para_ti,
    extrair_texto_pdf_anexo,
    ler_anexo_como_texto,
    listar_anexos_ticket,
    listar_categorias_especificas,
    recusar_chamado,
    limpar_recusa_chamado,
    send_assistente_message,
    set_ticket_priority,
    set_ticket_status,
    triar_chamado,
)
from helpdesk.models import Comment, Ticket
from mcp_api.auth import requer_token_mcp
from mcp_api.serializers import parse_limit, serialize_comment, serialize_ticket


def _json_body(request) -> dict:
    import json
    try:
        if request.body:
            return json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {k: v for k, v in request.POST.items()}


def _service_response(fn, *args, **kwargs):
    try:
        return JsonResponse(fn(*args, **kwargs))
    except AssistenteServiceError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=exc.status_code)


@require_GET
@requer_token_mcp
def list_tickets(request):
    qs = Ticket.objects.select_related(
        'category', 'specific_category', 'equipe',
        'requester_user', 'created_by', 'assigned_to',
    ).order_by('-updated_at')

    status = (request.GET.get('status') or '').strip()
    if status:
        qs = qs.filter(status=status)

    archived = (request.GET.get('archived') or '').strip().lower()
    if archived == '1' or archived == 'true':
        qs = qs.filter(is_archived=True)
    elif archived == '0' or archived == 'false':
        qs = qs.filter(is_archived=False)

    active = (request.GET.get('active') or '').strip().lower()
    if active in ('1', 'true'):
        qs = qs.filter(is_active=True)
    elif active in ('0', 'false'):
        qs = qs.filter(is_active=False)

    assignee = (request.GET.get('assigned_to') or '').strip()
    if assignee:
        if assignee.isdigit():
            qs = qs.filter(assigned_to_id=int(assignee))
        else:
            qs = qs.filter(assigned_to__username__iexact=assignee)

    q = (request.GET.get('q') or '').strip()
    if q:
        filtro = Q(title__icontains=q) | Q(description__icontains=q) | Q(requester_name__icontains=q)
        if q.isdigit():
            filtro |= Q(pk=int(q))
        qs = qs.filter(filtro)

    limit = parse_limit(request)
    itens = [serialize_ticket(t) for t in qs[:limit]]
    return JsonResponse({'count': len(itens), 'results': itens})


@require_GET
@requer_token_mcp
def get_ticket(request, pk):
    ticket = get_object_or_404(
        Ticket.objects.select_related(
            'category', 'specific_category', 'equipe',
            'requester_user', 'created_by', 'assigned_to', 'resolved_by',
        ),
        pk=pk,
    )
    return JsonResponse(serialize_ticket(ticket, detalhe=True))


@require_GET
@requer_token_mcp
def list_ticket_comments(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    qs = (
        Comment.objects.filter(ticket=ticket, is_active=True)
        .select_related('author')
        .order_by('created_at')
    )
    limit = parse_limit(request, default=50)
    itens = [serialize_comment(c) for c in qs[:limit]]
    return JsonResponse({'ticket_id': ticket.pk, 'count': len(itens), 'results': itens})


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_assistente_comentario(request, pk):
    data = _json_body(request)
    interno = bool(data.get('interno') or data.get('is_interno'))
    return _service_response(
        send_assistente_message,
        pk,
        data.get('text', ''),
        interno=interno,
    )


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_ticket_priority(request, pk):
    data = _json_body(request)
    return _service_response(set_ticket_priority, pk, data.get('priority', ''))


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_ticket_status(request, pk):
    data = _json_body(request)
    return _service_response(set_ticket_status, pk, data.get('status', ''))


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_assistente_escalar(request, pk):
    data = _json_body(request)
    return _service_response(escalar_para_ti, pk, data.get('motivo', ''))


@require_GET
@requer_token_mcp
def get_categorias_especificas(request):
    return _service_response(listar_categorias_especificas)


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_triar_chamado(request, pk):
    data = _json_body(request)
    return _service_response(
        triar_chamado,
        pk,
        data.get('priority', ''),
        data.get('specific_category_id'),
    )


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_recusar_chamado(request, pk):
    data = _json_body(request)
    return _service_response(recusar_chamado, pk, data.get('motivo', ''))


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_limpar_recusa_chamado(request, pk):
    return _service_response(limpar_recusa_chamado, pk)


@require_GET
@requer_token_mcp
def get_ticket_anexos(request, pk):
    return _service_response(listar_anexos_ticket, pk)


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_ler_imagem_anexo(request, pk):
    data = _json_body(request)
    return _service_response(descrever_imagem_anexo, pk, data.get('attachment_ref', ''))


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_ler_pdf_anexo(request, pk):
    data = _json_body(request)
    return _service_response(extrair_texto_pdf_anexo, pk, data.get('attachment_ref', ''))


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_ler_anexo_texto(request, pk):
    data = _json_body(request)
    return _service_response(ler_anexo_como_texto, pk, data.get('attachment_ref', ''))


@require_GET
@requer_token_mcp
def get_consultar_chips(request):
    q = (request.GET.get('q') or '').strip()
    return _service_response(consultar_chips, q)


@require_GET
@requer_token_mcp
def get_consultar_usuario(request):
    q = (request.GET.get('q') or '').strip()
    return _service_response(consultar_usuario, q)


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_atualizar_solicitante(request, pk):
    data = _json_body(request)
    return _service_response(
        atualizar_solicitante,
        pk,
        data.get('user_id'),
        data.get('nome_livre', ''),
    )


@csrf_exempt
@require_http_methods(['POST'])
@requer_token_mcp
def post_atualizar_descricao(request, pk):
    data = _json_body(request)
    return _service_response(
        atualizar_descricao_chamado,
        pk,
        data.get('description', ''),
        data.get('title'),
    )
