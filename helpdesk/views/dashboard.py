import re
from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Count, OuterRef, Subquery, TextField
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import TemplateView

from core.audit import logs_do_modulo
from core.permissions import MODULO_HELPDESK, ModuloObrigatorioMixin, resposta_sem_permissao
from helpdesk.models import Comment, Ticket
from helpdesk.ticket_access import filtrar_chamados_para_usuario, usuario_pode_acessar_dashboard_e_historico

MOTIVO_VAZIO = 'Sem motivo informado'
TOP_MOTIVOS = 10
LIMITE_ROTULO_GRAFICO = 42


def _periodo_padrao():
    """Primeiro dia do mês vigente até a data de hoje (fuso local)."""
    hoje = timezone.localdate()
    return hoje.replace(day=1), hoje


def parsear_periodo_dashboard(request):
    """Lê date_from/date_to da querystring; fallback para o mês vigente."""
    inicio_padrao, fim_padrao = _periodo_padrao()
    date_from = parse_date((request.GET.get('date_from') or '').strip()) or inicio_padrao
    date_to = parse_date((request.GET.get('date_to') or '').strip()) or fim_padrao
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def filtrar_por_periodo(queryset, date_from, date_to):
    """Filtra chamados pela data de abertura no intervalo (inclusive)."""
    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    fim_exclusivo = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min),
        tz,
    )
    return queryset.filter(created_at__gte=inicio, created_at__lt=fim_exclusivo)


def _normalizar_motivo(texto):
    """Padroniza texto livre para agrupar motivos iguais."""
    if not texto:
        return MOTIVO_VAZIO
    limpo = re.sub(r'\s+', ' ', str(texto).strip())
    return limpo if limpo else MOTIVO_VAZIO


def _truncar_rotulo(texto, limite=LIMITE_ROTULO_GRAFICO):
    if len(texto) <= limite:
        return texto
    return texto[: limite - 1] + '…'


def _extrair_observacao_resolucao(texto):
    """Pega a observação gravada no comentário de finalização."""
    if not texto:
        return ''
    marcador = 'Observação:'
    idx = texto.find(marcador)
    if idx >= 0:
        return texto[idx + len(marcador):].strip()
    partes = texto.split('\n', 1)
    return partes[1].strip() if len(partes) > 1 else ''


def agrupar_motivos(textos, top_n=TOP_MOTIVOS):
    """Agrupa textos, devolve top N + Outros com contagem e percentual."""
    contagem = Counter(_normalizar_motivo(t) for t in textos)
    total = sum(contagem.values())
    ordenados = contagem.most_common()
    top = ordenados[:top_n]
    outros = sum(n for _, n in ordenados[top_n:])
    itens = []
    for label, qtd in top:
        itens.append({
            'label': label,
            'label_curto': _truncar_rotulo(label),
            'total': qtd,
            'percent': round(100.0 * qtd / total, 1) if total else 0,
        })
    if outros:
        itens.append({
            'label': 'Outros',
            'label_curto': 'Outros',
            'total': outros,
            'percent': round(100.0 * outros / total, 1) if total else 0,
        })
    return {
        'labels': [i['label_curto'] for i in itens],
        'values': [i['total'] for i in itens],
        'items': itens,
        'total': total,
    }


def _motivos_resolucao(tickets_resolvidos):
    """Último comentário 'Chamado finalizado' de cada chamado resolvido."""
    ultimo = (
        Comment.objects.filter(
            ticket_id=OuterRef('pk'),
            is_active=True,
            text__startswith='Chamado finalizado',
        )
        .order_by('-created_at')
        .values('text')[:1]
    )
    textos = tickets_resolvidos.annotate(
        obs_final=Subquery(ultimo, output_field=TextField()),
    ).values_list('obs_final', flat=True)
    return [_extrair_observacao_resolucao(t) for t in textos]


class DashboardView(ModuloObrigatorioMixin, TemplateView):
    template_name = 'helpdesk/dashboard.html'
    modulo_obrigatorio = MODULO_HELPDESK

    def dispatch(self, request, *args, **kwargs):
        if not usuario_pode_acessar_dashboard_e_historico(request.user):
            return resposta_sem_permissao(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        date_from, date_to = parsear_periodo_dashboard(self.request)
        tickets = filtrar_chamados_para_usuario(
            Ticket.objects.filter(is_active=True),
            self.request.user,
        )
        tickets = filtrar_por_periodo(tickets, date_from, date_to)

        status_new = Ticket.StatusChoices.NEW
        status_resolved = Ticket.StatusChoices.RESOLVED
        em_andamento_statuses = (
            Ticket.StatusChoices.IN_PROGRESS,
            Ticket.StatusChoices.PENDING,
        )

        total_tickets = tickets.count()
        total_atendidos = tickets.exclude(status=status_new).count()
        total_resolvidos = tickets.filter(status=status_resolved, is_rejected=False).count()
        total_recusados = tickets.filter(is_rejected=True).count()
        total_novos = tickets.filter(status=status_new).count()
        total_em_andamento = tickets.filter(status__in=em_andamento_statuses).count()

        context['date_from'] = date_from.isoformat()
        context['date_to'] = date_to.isoformat()
        context['total_tickets'] = total_tickets
        context['total_atendidos'] = total_atendidos
        context['total_resolvidos'] = total_resolvidos
        context['total_recusados'] = total_recusados
        context['total_novos'] = total_novos
        context['total_em_andamento'] = total_em_andamento
        context['total_archived'] = tickets.filter(is_archived=True).count()
        context['total_kanban_active'] = tickets.filter(is_archived=False).count()

        status_labels = dict(Ticket.StatusChoices.choices)
        priority_labels = dict(Ticket.PriorityChoices.choices)
        status_rows = list(
            tickets.values('status').annotate(total=Count('id')).order_by('-total')
        )
        priority_rows = list(
            tickets.values('priority').annotate(total=Count('id')).order_by('-total')
        )

        context['status_distribution'] = status_rows
        context['priority_distribution'] = priority_rows
        context['status_labels'] = status_labels
        context['priority_labels'] = priority_labels

        tickets_recusados = tickets.filter(is_rejected=True)
        tickets_resolvidos = tickets.filter(status=status_resolved, is_rejected=False)
        motivos_recusa = agrupar_motivos(
            tickets_recusados.values_list('rejection_reason', flat=True)
        )
        motivos_resolucao = agrupar_motivos(_motivos_resolucao(tickets_resolvidos))
        context['motivos_recusa'] = motivos_recusa
        context['motivos_resolucao'] = motivos_resolucao

        charts_data = {
            'resultado': {
                'labels': ['Resolvidos', 'Recusados', 'Em andamento', 'Novos'],
                'values': [
                    total_resolvidos,
                    total_recusados,
                    total_em_andamento,
                    total_novos,
                ],
            },
            'motivos_recusa': {
                'labels': motivos_recusa['labels'],
                'values': motivos_recusa['values'],
            },
            'motivos_resolucao': {
                'labels': motivos_resolucao['labels'],
                'values': motivos_resolucao['values'],
            },
            'status': {
                'labels': [
                    status_labels.get(row['status'], row['status'] or '—')
                    for row in status_rows
                ],
                'values': [row['total'] for row in status_rows],
            },
            'prioridade': {
                'labels': [
                    priority_labels.get(row['priority'], 'Sem prioridade')
                    if row['priority']
                    else 'Sem prioridade'
                    for row in priority_rows
                ],
                'values': [row['total'] for row in priority_rows],
            },
        }
        context['charts_data'] = charts_data

        if self.template_name == 'helpdesk/dashboard.html':
            context['audit_logs'] = logs_do_modulo(MODULO_HELPDESK, limite=50)
            context['audit_titulo'] = 'Últimas ações do Helpdesk'

        return context


class DashboardMetricsPartialView(DashboardView):
    """Retorna apenas os gráficos e KPIs atualizados para injeção via HTMX SSE."""
    template_name = 'helpdesk/_dashboard_metrics.html'
