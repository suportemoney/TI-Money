from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from emails.models import EmailAccount, EmailDomain
from mcp_api.auth import requer_token_mcp
from mcp_api.serializers import parse_limit, serialize_domain, serialize_email_account, filtro_q_email


@require_GET
@requer_token_mcp
def list_domains(request):
    qs = EmailDomain.objects.all().order_by('name')
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    limit = parse_limit(request)
    itens = [serialize_domain(d) for d in qs[:limit]]
    return JsonResponse({'count': len(itens), 'results': itens})


@require_GET
@requer_token_mcp
def list_accounts(request):
    qs = EmailAccount.objects.select_related('domain').order_by('-updated_at')

    status = (request.GET.get('status') or '').strip()
    if status:
        qs = qs.filter(status=status)

    domain = (request.GET.get('domain') or '').strip()
    if domain:
        qs = qs.filter(domain__name__iexact=domain)

    q = (request.GET.get('q') or '').strip()
    if q:
        qs = filtro_q_email(qs, q)

    limit = parse_limit(request)
    itens = [serialize_email_account(a) for a in qs[:limit]]
    return JsonResponse({'count': len(itens), 'results': itens})


@require_GET
@requer_token_mcp
def get_account(request, pk):
    account = get_object_or_404(EmailAccount.objects.select_related('domain'), pk=pk)
    return JsonResponse(serialize_email_account(account))
