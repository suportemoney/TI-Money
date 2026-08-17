from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from core.models import CustomUser, Equipe
from helpdesk.forms import TicketCreateForm, TicketUpdateForm
from helpdesk.models import Ticket, TicketCategory, Comment, TicketContestation
from helpdesk.ticket_access import (
    filtrar_chamados_para_usuario,
    usuario_pode_acessar_chamado,
    usuario_pode_comentar_chamado,
    usuario_pode_contestar_chamado,
    usuario_pode_operar_kanban,
    usuario_ve_todos_chamados,
)


class SupervisorRequesterTestCase(TestCase):
    def setUp(self):
        self.equipe1 = Equipe.objects.create(name="TI", is_active=True)
        self.equipe2 = Equipe.objects.create(name="Suporte", is_active=True)
        self.categoria = TicketCategory.objects.get_or_create(name="Dúvidas", defaults={"is_active": True})[0]

        self.supervisor = CustomUser.objects.create_user(
            username="test_supervisor",
            password="password123",
            first_name="Supervisor",
            last_name="Test",
            role=CustomUser.RoleChoices.SUPERVISOR,
        )
        self.supervisor.equipes.add(self.equipe1)

        self.admin = CustomUser.objects.create_user(
            username="test_admin",
            password="password123",
            first_name="Admin",
            last_name="Test",
            role=CustomUser.RoleChoices.ADMIN,
        )

    def test_supervisor_create_form_fields_and_validation(self):
        form = TicketCreateForm(user=self.supervisor)
        self.assertIn('tipo_solicitante', form.fields)
        choices = form.fields['tipo_solicitante'].choices
        self.assertEqual(len(choices), 2)
        self.assertEqual(choices[0][0], 'eu')
        self.assertEqual(choices[1][0], 'texto')
        self.assertNotIn('requester_user', form.fields)
        self.assertIn('requester_name', form.fields)

        data_eu = {
            'tipo_solicitante': 'eu',
            'title': 'Test ticket by self',
            'description': 'Description of test ticket',
            'category': self.categoria.id,
        }
        form_eu = TicketCreateForm(data=data_eu, user=self.supervisor)
        self.assertTrue(form_eu.is_valid(), form_eu.errors)
        ticket_eu = form_eu.save(created_by=self.supervisor)
        self.assertEqual(ticket_eu.requester_name, "Supervisor Test")
        self.assertEqual(ticket_eu.requester_user, self.supervisor)

        data_texto = {
            'tipo_solicitante': 'texto',
            'requester_name': 'Fulano de Tal',
            'title': 'Test ticket by text name',
            'description': 'Description of test ticket',
            'category': self.categoria.id,
        }
        form_texto = TicketCreateForm(data=data_texto, user=self.supervisor)
        self.assertTrue(form_texto.is_valid(), form_texto.errors)
        ticket_texto = form_texto.save(created_by=self.supervisor)
        self.assertEqual(ticket_texto.requester_name, "Fulano de Tal")
        self.assertIsNone(ticket_texto.requester_user)

    def test_admin_create_form_fields(self):
        form = TicketCreateForm(user=self.admin)
        self.assertIn('tipo_solicitante', form.fields)
        choices = form.fields['tipo_solicitante'].choices
        self.assertEqual(len(choices), 2)
        self.assertEqual(choices[0][0], 'texto')
        self.assertEqual(choices[1][0], 'usuario')
        self.assertIn('requester_user', form.fields)
        self.assertIn('requester_name', form.fields)

    def test_supervisor_update_form_fields_and_initial_values(self):
        ticket_eu = Ticket.objects.create(
            title="Ticket original eu",
            description="Desc original",
            category=self.categoria,
            created_by=self.supervisor,
            requester_name=self.supervisor.get_full_name(),
            requester_user=self.supervisor,
        )
        ticket_outro_user = Ticket.objects.create(
            title="Ticket original outro",
            description="Desc original",
            category=self.categoria,
            created_by=self.supervisor,
            requester_name=self.admin.get_full_name(),
            requester_user=self.admin,
        )
        ticket_nome_livre = Ticket.objects.create(
            title="Ticket original nome livre",
            description="Desc original",
            category=self.categoria,
            created_by=self.supervisor,
            requester_name="Beltrano",
        )

        form_eu = TicketUpdateForm(instance=ticket_eu, user=self.supervisor)
        self.assertEqual(form_eu.fields['tipo_solicitante'].initial, 'eu')

        form_outro = TicketUpdateForm(instance=ticket_outro_user, user=self.supervisor)
        self.assertEqual(form_outro.fields['tipo_solicitante'].initial, 'texto')

        form_livre = TicketUpdateForm(instance=ticket_nome_livre, user=self.supervisor)
        self.assertEqual(form_livre.fields['tipo_solicitante'].initial, 'texto')
        self.assertEqual(form_livre.fields['requester_name'].initial, "Beltrano")


class RbacHelpdeskTestCase(TestCase):
    def setUp(self):
        self.equipe = Equipe.objects.create(name="Comercial", is_active=True)
        self.categoria = TicketCategory.objects.get_or_create(name="Software", defaults={"is_active": True})[0]

        self.it_user = CustomUser.objects.create_user(
            username="ti_user", password="x", role=CustomUser.RoleChoices.IT_USER,
        )
        self.supervisor = CustomUser.objects.create_user(
            username="supervisor", password="x", role=CustomUser.RoleChoices.SUPERVISOR,
        )
        self.team_leader = CustomUser.objects.create_user(
            username="lider", password="x", role=CustomUser.RoleChoices.TEAM_LEADER,
        )
        self.multiplier = CustomUser.objects.create_user(
            username="multi", password="x", role=CustomUser.RoleChoices.MULTIPLIER,
        )
        self.standard = CustomUser.objects.create_user(
            username="padrao", password="x", role=CustomUser.RoleChoices.STANDARD,
        )
        self.colega = CustomUser.objects.create_user(
            username="colega", password="x", role=CustomUser.RoleChoices.STANDARD,
        )

        self.team_leader.equipes.add(self.equipe)
        self.supervisor.equipes.add(self.equipe)
        self.multiplier.equipes.add(self.equipe)
        self.colega.equipes.add(self.equipe)

        self.ticket_equipe = Ticket.objects.create(
            title="Chamado equipe",
            description="Desc",
            category=self.categoria,
            equipe=self.equipe,
            created_by=self.colega,
            requester_name="Colega",
            requester_user=self.colega,
        )
        self.ticket_outro = Ticket.objects.create(
            title="Chamado externo",
            description="Desc",
            category=self.categoria,
            created_by=self.standard,
            requester_name="Padrao",
            requester_user=self.standard,
        )

    def test_supervisor_ve_apenas_equipe(self):
        self.assertFalse(usuario_ve_todos_chamados(self.supervisor))
        qs = filtrar_chamados_para_usuario(Ticket.objects.all(), self.supervisor)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().pk, self.ticket_equipe.pk)

    def test_lider_ve_apenas_equipe(self):
        qs = filtrar_chamados_para_usuario(Ticket.objects.all(), self.team_leader)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().pk, self.ticket_equipe.pk)

    def test_lider_nao_comenta_chamado_alheio(self):
        self.assertTrue(usuario_pode_acessar_chamado(self.team_leader, self.ticket_equipe))
        self.assertFalse(usuario_pode_comentar_chamado(self.team_leader, self.ticket_equipe))

    def test_lider_comenta_proprio_chamado(self):
        self.ticket_equipe.created_by = self.team_leader
        self.ticket_equipe.save(update_fields=['created_by'])
        self.assertTrue(usuario_pode_comentar_chamado(self.team_leader, self.ticket_equipe))

    def test_supervisor_comenta_apenas_visiveis(self):
        self.assertFalse(usuario_pode_comentar_chamado(self.supervisor, self.ticket_outro))
        self.assertTrue(usuario_pode_comentar_chamado(self.supervisor, self.ticket_equipe))

    def test_supervisor_nao_move_kanban(self):
        self.assertFalse(usuario_pode_operar_kanban(self.supervisor))

    def test_it_user_move_kanban(self):
        self.assertTrue(usuario_pode_operar_kanban(self.it_user))

    def test_multiplicador_co_autor(self):
        form = TicketCreateForm(user=self.multiplier)
        self.assertEqual(len(form.fields['tipo_solicitante'].choices), 2)
        self.assertNotIn('co_autor_user', form.fields)

        data = {
            'tipo_solicitante': 'texto',
            'requester_name': self.colega.get_full_name() or self.colega.username,
            'title': 'Chamado multiplicador',
            'description': 'Descricao',
            'category': self.categoria.id,
        }
        form = TicketCreateForm(data=data, user=self.multiplier)
        self.assertTrue(form.is_valid(), form.errors)
        ticket = form.save(created_by=self.multiplier)
        self.assertTrue(ticket.co_authors.filter(pk=self.colega.pk).exists())
        self.assertTrue(usuario_pode_acessar_chamado(self.colega, ticket))
        self.assertTrue(usuario_pode_comentar_chamado(self.colega, ticket))

    def test_multiplicador_ve_apenas_proprios_e_co_autor(self):
        ticket = Ticket.objects.create(
            title="Proprio",
            description="Desc",
            category=self.categoria,
            created_by=self.multiplier,
            requester_name="Multi",
            requester_user=self.multiplier,
        )
        qs = filtrar_chamados_para_usuario(Ticket.objects.all(), self.multiplier)
        self.assertEqual(qs.count(), 1)
        self.ticket_equipe.co_authors.add(self.multiplier)
        qs = filtrar_chamados_para_usuario(Ticket.objects.all(), self.multiplier)
        self.assertEqual(qs.count(), 2)


class ArquivamentoAutomaticoTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name="Rede", defaults={"is_active": True})[0]

    def test_arquiva_resolvido_apos_24h_por_resolved_at(self):
        """Comentários não devem adiar arquivamento — usa resolved_at, não updated_at."""
        ticket = Ticket.objects.create(
            title="Chamado antigo",
            description="Desc",
            category=self.categoria,
            requester_name="Teste",
            status=Ticket.StatusChoices.RESOLVED,
        )
        Ticket.objects.filter(pk=ticket.pk).update(
            resolved_at=timezone.now() - timedelta(hours=25),
            updated_at=timezone.now()
        )
        ticket.refresh_from_db()

        Ticket.archive_old_tickets()
        ticket.refresh_from_db()
        self.assertTrue(ticket.is_archived)

    def test_nao_arquiva_resolvido_recente(self):
        ticket = Ticket.objects.create(
            title="Chamado novo",
            description="Desc",
            category=self.categoria,
            requester_name="Teste",
            status=Ticket.StatusChoices.RESOLVED,
            resolved_at=timezone.now() - timedelta(hours=2),
        )
        Ticket.archive_old_tickets()
        ticket.refresh_from_db()
        self.assertFalse(ticket.is_archived)

    def test_arquiva_mesmo_com_resolved_at_recente_se_comentario_antigo(self):
        """resolved_at errado (updated_at recente) não deve impedir arquivamento."""
        ticket = Ticket.objects.create(
            title="Finalizado há dias",
            description="Desc",
            category=self.categoria,
            requester_name="Teste",
            status=Ticket.StatusChoices.RESOLVED,
            resolved_at=timezone.now(),
        )
        Comment.objects.create(
            ticket=ticket,
            text='Chamado finalizado.\nObservação: ok',
        )
        Comment.objects.filter(ticket=ticket).update(
            created_at=timezone.now() - timedelta(hours=30),
        )
        # Simula resolved_at preenchido errado pelo backfill antigo
        Ticket.objects.filter(pk=ticket.pk).update(
            resolved_at=timezone.now(),
        )

        Ticket.archive_old_tickets()
        ticket.refresh_from_db()
        self.assertTrue(ticket.is_archived)
        self.assertLess(ticket.resolved_at, timezone.now() - timedelta(hours=24))

    def test_save_define_resolved_at_ao_finalizar(self):
        ticket = Ticket.objects.create(
            title="Em atendimento",
            description="Desc",
            category=self.categoria,
            requester_name="Teste",
            status=Ticket.StatusChoices.IN_PROGRESS,
        )
        ticket.status = Ticket.StatusChoices.RESOLVED
        ticket.save()
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.resolved_at)


class HistoryExportCsvTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name="Hardware", defaults={"is_active": True})[0]
        self.it_user = CustomUser.objects.create_user(
            username="ti_export",
            password="x",
            role=CustomUser.RoleChoices.IT_USER,
        )
        self.supervisor = CustomUser.objects.create_user(
            username="super_export",
            password="x",
            role=CustomUser.RoleChoices.SUPERVISOR,
        )
        self.ticket_novo = Ticket.objects.create(
            title="Impressora quebrada",
            description="Não imprime",
            category=self.categoria,
            requester_name="João Silva",
            status=Ticket.StatusChoices.NEW,
        )
        self.ticket_resolvido = Ticket.objects.create(
            title="VPN lenta",
            description="Conexão instável",
            category=self.categoria,
            requester_name="Maria",
            status=Ticket.StatusChoices.RESOLVED,
        )
        Ticket.objects.filter(pk=self.ticket_novo.pk).update(
            created_at=timezone.now() - timedelta(days=5),
        )
        Ticket.objects.filter(pk=self.ticket_resolvido.pk).update(
            created_at=timezone.now() - timedelta(days=3),
        )
        self.ticket_novo.refresh_from_db()
        self.ticket_resolvido.refresh_from_db()

        self.date_from = (timezone.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        self.date_to = timezone.now().strftime('%Y-%m-%d')

    def _url_export(self, **params):
        from django.urls import reverse
        base = reverse('helpdesk:history_export')
        query = {'date_from': self.date_from, 'date_to': self.date_to, **params}
        return f"{base}?{'&'.join(f'{k}={v}' for k, v in query.items())}"

    def test_it_user_exporta_csv_com_dados(self):
        self.client.force_login(self.it_user)
        response = self.client.get(self._url_export())
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('attachment', response['Content-Disposition'])
        conteudo = response.content.decode('utf-8-sig')
        self.assertIn('ID;Título;Descrição;Solicitante', conteudo)
        self.assertIn('Impressora quebrada', conteudo)
        self.assertIn('João Silva', conteudo)
        self.assertIn('Hardware', conteudo)

    def test_supervisor_sem_permissao_export(self):
        self.client.force_login(self.supervisor)
        response = self.client.get(self._url_export())
        self.assertEqual(response.status_code, 403)

    def test_export_sem_periodo_redireciona_com_erro(self):
        self.client.force_login(self.it_user)
        from django.urls import reverse
        response = self.client.get(reverse('helpdesk:history_export'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('erro=export_periodo', response.url)

    def test_export_respeita_filtro_status(self):
        self.client.force_login(self.it_user)
        response = self.client.get(self._url_export(status=Ticket.StatusChoices.RESOLVED))
        self.assertEqual(response.status_code, 200)
        conteudo = response.content.decode('utf-8-sig')
        self.assertIn('VPN lenta', conteudo)
        self.assertNotIn('Impressora quebrada', conteudo)


class HistoricoFiltroArquivadoTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name="Rede", defaults={"is_active": True})[0]
        self.it_user = CustomUser.objects.create_user(
            username="ti_historico",
            password="x",
            role=CustomUser.RoleChoices.IT_USER,
        )
        self.ticket_ativo = Ticket.objects.create(
            title="Chamado ativo",
            description="Desc",
            category=self.categoria,
            requester_name="Teste",
            status=Ticket.StatusChoices.NEW,
            is_archived=False,
        )
        self.ticket_arquivado = Ticket.objects.create(
            title="Chamado arquivado",
            description="Desc",
            category=self.categoria,
            requester_name="Teste",
            status=Ticket.StatusChoices.RESOLVED,
            is_archived=True,
            resolved_at=timezone.now() - timedelta(hours=48),
        )
        Ticket.objects.filter(pk=self.ticket_arquivado.pk).update(
            created_at=timezone.now() - timedelta(days=30),
        )

    def _aplicar_filtro(self, archived=None):
        from django.test import RequestFactory
        from helpdesk.views.history import aplicar_filtros_historico, queryset_historico_base

        rf = RequestFactory()
        query = {}
        if archived is not None:
            query['archived'] = archived
        request = rf.get('/helpdesk/history/', query)
        qs = queryset_historico_base(self.it_user)
        return aplicar_filtros_historico(qs, request)

    def test_filtro_sim_so_arquivados(self):
        qs = self._aplicar_filtro('yes')
        self.assertEqual(qs.count(), 1)
        self.assertTrue(qs.filter(pk=self.ticket_arquivado.pk).exists())
        self.assertFalse(qs.filter(pk=self.ticket_ativo.pk).exists())

    def test_filtro_nao_so_nao_arquivados(self):
        qs = self._aplicar_filtro('no')
        self.assertEqual(qs.count(), 1)
        self.assertTrue(qs.filter(pk=self.ticket_ativo.pk).exists())
        self.assertFalse(qs.filter(pk=self.ticket_arquivado.pk).exists())

    def test_filtro_ambos_inclui_os_dois(self):
        qs = self._aplicar_filtro('')
        self.assertEqual(qs.count(), 2)
        self.assertTrue(qs.filter(pk=self.ticket_ativo.pk).exists())
        self.assertTrue(qs.filter(pk=self.ticket_arquivado.pk).exists())

    def test_filtro_ambos_sem_parametro(self):
        from django.test import RequestFactory
        from helpdesk.views.history import aplicar_filtros_historico, queryset_historico_base

        rf = RequestFactory()
        request = rf.get('/helpdesk/history/')
        qs = aplicar_filtros_historico(queryset_historico_base(self.it_user), request)
        self.assertEqual(qs.count(), 2)

    def test_ambos_paginacao_exibe_arquivados_em_pagina_seguinte(self):
        from django.urls import reverse

        agora = timezone.now()
        for i in range(22):
            ticket = Ticket.objects.create(
                title=f"Recente {i}",
                description="Desc",
                category=self.categoria,
                requester_name="Teste",
                status=Ticket.StatusChoices.NEW,
                is_archived=False,
            )
            Ticket.objects.filter(pk=ticket.pk).update(
                created_at=agora - timedelta(hours=i),
            )
        for i in range(8):
            ticket = Ticket.objects.create(
                title=f"Arquivado antigo {i}",
                description="Desc",
                category=self.categoria,
                requester_name="Teste",
                status=Ticket.StatusChoices.RESOLVED,
                is_archived=True,
                resolved_at=agora - timedelta(days=10),
            )
            Ticket.objects.filter(pk=ticket.pk).update(
                created_at=agora - timedelta(days=30, hours=i),
            )

        self.client.force_login(self.it_user)
        url = reverse('helpdesk:history')

        response_p1 = self.client.get(url)
        self.assertEqual(response_p1.status_code, 200)
        self.assertContains(response_p1, 'Página 1 de')
        self.assertNotContains(response_p1, 'Arquivado antigo 0')

        response_p2 = self.client.get(f'{url}?page=2')
        self.assertEqual(response_p2.status_code, 200)
        self.assertContains(response_p2, 'Arquivado antigo')
        self.assertContains(response_p2, 'Arq')


class ComentarioFinalizadoTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name="Software", defaults={"is_active": True})[0]
        self.it_user = CustomUser.objects.create_user(
            username="ti_final", password="x", role=CustomUser.RoleChoices.IT_USER,
        )
        self.admin = CustomUser.objects.create_user(
            username="admin_final", password="x", role=CustomUser.RoleChoices.ADMIN,
        )
        self.supervisor = CustomUser.objects.create_user(
            username="sup_final", password="x", role=CustomUser.RoleChoices.SUPERVISOR,
        )
        self.standard = CustomUser.objects.create_user(
            username="pad_final", password="x", role=CustomUser.RoleChoices.STANDARD,
        )
        self.ticket = Ticket.objects.create(
            title="Chamado resolvido",
            description="Desc",
            category=self.categoria,
            created_by=self.standard,
            requester_name="Padrao",
            requester_user=self.standard,
            status=Ticket.StatusChoices.RESOLVED,
            resolved_at=timezone.now(),
        )

    def test_standard_nao_comenta_finalizado(self):
        self.assertFalse(usuario_pode_comentar_chamado(self.standard, self.ticket))

    def test_supervisor_nao_comenta_finalizado(self):
        self.assertFalse(usuario_pode_comentar_chamado(self.supervisor, self.ticket))

    def test_it_user_comenta_finalizado(self):
        self.assertTrue(usuario_pode_comentar_chamado(self.it_user, self.ticket))

    def test_admin_comenta_finalizado(self):
        self.assertTrue(usuario_pode_comentar_chamado(self.admin, self.ticket))

    def test_standard_comenta_nao_finalizado(self):
        self.ticket.status = Ticket.StatusChoices.NEW
        self.ticket.save()
        self.assertTrue(usuario_pode_comentar_chamado(self.standard, self.ticket))


class ContestacaoChamadoTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name="Rede", defaults={"is_active": True})[0]
        self.it_user = CustomUser.objects.create_user(
            username="ti_contest", password="x", role=CustomUser.RoleChoices.IT_USER,
        )
        self.standard = CustomUser.objects.create_user(
            username="sol_contest", password="x", role=CustomUser.RoleChoices.STANDARD,
        )
        self.outro = CustomUser.objects.create_user(
            username="outro_contest", password="x", role=CustomUser.RoleChoices.STANDARD,
        )
        self.resolved_at = timezone.now() - timedelta(hours=2)
        self.ticket = Ticket.objects.create(
            title="Chamado para contestar",
            description="Desc",
            category=self.categoria,
            created_by=self.standard,
            requester_name="Solicitante",
            requester_user=self.standard,
            status=Ticket.StatusChoices.RESOLVED,
            resolved_at=self.resolved_at,
            resolved_by=self.it_user,
            assigned_to=self.it_user,
        )

    def test_solicitante_pode_contestar(self):
        self.assertTrue(usuario_pode_contestar_chamado(self.standard, self.ticket))

    def test_operador_nao_contesta(self):
        self.assertFalse(usuario_pode_contestar_chamado(self.it_user, self.ticket))

    def test_usuario_alheio_nao_contesta(self):
        self.assertFalse(usuario_pode_contestar_chamado(self.outro, self.ticket))

    def test_arquivado_nao_contesta(self):
        self.ticket.is_archived = True
        self.ticket.save()
        self.assertFalse(usuario_pode_contestar_chamado(self.standard, self.ticket))

    def test_nao_finalizado_nao_contesta(self):
        self.ticket.status = Ticket.StatusChoices.IN_PROGRESS
        self.ticket.save()
        self.assertFalse(usuario_pode_contestar_chamado(self.standard, self.ticket))

    def test_co_autor_pode_contestar(self):
        self.ticket.created_by = self.outro
        self.ticket.requester_user = self.outro
        self.ticket.co_authors.add(self.standard)
        self.ticket.save()
        self.assertTrue(usuario_pode_contestar_chamado(self.standard, self.ticket))

    def test_recusado_pode_ser_contestado(self):
        self.ticket.is_rejected = True
        self.ticket.rejection_reason = 'Fora de escopo'
        self.ticket.save()
        self.assertTrue(usuario_pode_contestar_chamado(self.standard, self.ticket))

    def test_post_contest_reabre_como_novo(self):
        from django.urls import reverse
        import json

        self.client.force_login(self.standard)
        url = reverse('helpdesk:ticket_contest', args=[self.ticket.pk])
        response = self.client.post(
            url,
            data=json.dumps({'reason': 'Problema persiste'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.StatusChoices.NEW)
        self.assertFalse(self.ticket.is_rejected)
        self.assertIsNone(self.ticket.resolved_at)
        self.assertIsNone(self.ticket.resolved_by)
        from helpdesk.models import TicketUnread
        self.assertTrue(TicketUnread.objects.filter(ticket=self.ticket).exists())

        contestacao = TicketContestation.objects.get(ticket=self.ticket)
        self.assertEqual(contestacao.contested_by, self.standard)
        self.assertEqual(contestacao.finalized_by, self.it_user)
        self.assertEqual(contestacao.reason, 'Problema persiste')
        self.assertFalse(contestacao.was_rejected)

        comentario = Comment.objects.filter(ticket=self.ticket, text__startswith='Contestação do chamado').first()
        self.assertIsNotNone(comentario)
        self.assertIn('Problema persiste', comentario.text)
        self.assertIn(self.it_user.get_full_name() or self.it_user.username, comentario.text)

    def test_post_contest_sem_permissao(self):
        from django.urls import reverse
        import json

        self.client.force_login(self.outro)
        url = reverse('helpdesk:ticket_contest', args=[self.ticket.pk])
        response = self.client.post(
            url,
            data=json.dumps({'reason': 'Tentativa inválida'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)


class DestinatariosNotificacaoTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name='Cat Notif', defaults={"is_active": True})[0]
        self.admin = CustomUser.objects.create_user(
            username='notif_admin', password='pass', role=CustomUser.RoleChoices.ADMIN,
        )
        self.it_user = CustomUser.objects.create_user(
            username='notif_it', password='pass', role=CustomUser.RoleChoices.IT_USER,
        )
        self.it_user2 = CustomUser.objects.create_user(
            username='notif_it2', password='pass', role=CustomUser.RoleChoices.IT_USER,
        )
        self.supervisor = CustomUser.objects.create_user(
            username='notif_sup', password='pass', role=CustomUser.RoleChoices.SUPERVISOR,
        )
        self.standard = CustomUser.objects.create_user(
            username='notif_std', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        self.ticket = Ticket.objects.create(
            title='Chamado notif',
            description='Desc',
            category=self.categoria,
            created_by=self.standard,
            requester_user=self.standard,
            requester_name='Std',
            status=Ticket.StatusChoices.NEW,
        )

    def test_broadcast_novos_so_operadores_ti_sem_supervisor(self):
        from helpdesk.notifications import destinatarios_notificacao
        ids = {u.pk for u in destinatarios_notificacao(self.ticket, self.standard)}
        self.assertIn(self.admin.pk, ids)
        self.assertIn(self.it_user.pk, ids)
        self.assertNotIn(self.supervisor.pk, ids)
        self.assertNotIn(self.standard.pk, ids)

    def test_silencio_ti_nao_notifica_outro_ti(self):
        from helpdesk.notifications import destinatarios_notificacao
        self.ticket.assigned_to = self.it_user
        self.ticket.save(update_fields=['assigned_to'])
        ids = {u.pk for u in destinatarios_notificacao(self.ticket, self.it_user2)}
        self.assertNotIn(self.admin.pk, ids)
        self.assertNotIn(self.it_user.pk, ids)
        self.assertIn(self.standard.pk, ids)

    def test_finalizacao_so_nao_operadores(self):
        from helpdesk.notifications import destinatarios_finalizacao
        self.ticket.assigned_to = self.it_user
        self.ticket.save(update_fields=['assigned_to'])
        ids = {u.pk for u in destinatarios_finalizacao(self.ticket, self.it_user)}
        self.assertIn(self.standard.pk, ids)
        self.assertNotIn(self.admin.pk, ids)
        self.assertNotIn(self.it_user2.pk, ids)


class MentionAccessTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name='Cat Mention', defaults={"is_active": True})[0]
        self.admin = CustomUser.objects.create_user(
            username='mention_admin', password='pass', role=CustomUser.RoleChoices.ADMIN,
        )
        self.standard = CustomUser.objects.create_user(
            username='mention_alvo', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        self.outro = CustomUser.objects.create_user(
            username='mention_outro', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        self.ticket = Ticket.objects.create(
            title='Chamado menção',
            description='Desc',
            category=self.categoria,
            created_by=self.outro,
            requester_user=self.outro,
            requester_name='Outro',
            status=Ticket.StatusChoices.IN_PROGRESS,
        )

    def test_mencao_concede_acesso_e_cria_ticketmention(self):
        from helpdesk.mentions import processar_mencoes
        from helpdesk.models import TicketMention

        self.assertFalse(usuario_pode_acessar_chamado(self.standard, self.ticket))
        comment = Comment.objects.create(
            ticket=self.ticket,
            author=self.admin,
            text='Olá @mention_alvo, pode verificar?',
        )
        mencionados = processar_mencoes(self.ticket, comment, self.admin)
        self.assertEqual(len(mencionados), 1)
        self.assertEqual(mencionados[0].pk, self.standard.pk)
        self.assertTrue(usuario_pode_acessar_chamado(self.standard, self.ticket))
        self.assertTrue(
            TicketMention.objects.filter(
                ticket=self.ticket, user=self.standard, comment=comment, seen_at__isnull=True,
            ).exists()
        )

    def test_usuario_nao_operador_nao_processa_mencao(self):
        from helpdesk.mentions import processar_mencoes
        comment = Comment.objects.create(
            ticket=self.ticket,
            author=self.outro,
            text='@mention_alvo teste',
        )
        mencionados = processar_mencoes(self.ticket, comment, self.outro)
        self.assertEqual(mencionados, [])
        self.assertFalse(usuario_pode_acessar_chamado(self.standard, self.ticket))

    def test_ti_menciona_outro_ti_entra_no_nao_lido(self):
        """Menção TI→TI gera badge mesmo com silêncio geral entre operadores."""
        from helpdesk.models import TicketUnread
        from helpdesk.views.kanban import adicionar_nao_lido

        it_alvo = CustomUser.objects.create_user(
            username='mention_it_alvo',
            password='pass',
            role=CustomUser.RoleChoices.IT_USER,
        )
        comment = Comment.objects.create(
            ticket=self.ticket,
            author=self.admin,
            text='Ei @mention_it_alvo, olha isso',
        )
        from helpdesk.mentions import processar_mencoes
        mencionados = processar_mencoes(self.ticket, comment, self.admin)
        self.assertEqual([u.pk for u in mencionados], [it_alvo.pk])

        adicionar_nao_lido(self.ticket, self.admin, usuarios_extra=mencionados)
        self.assertTrue(
            TicketUnread.objects.filter(ticket=self.ticket, user=it_alvo).exists()
        )


class FilaPosicaoTestCase(TestCase):
    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(name='Cat Fila', defaults={"is_active": True})[0]
        self.user = CustomUser.objects.create_user(
            username='fila_user', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )

    def _criar(self, pk_force, priority, status=Ticket.StatusChoices.NEW):
        t = Ticket(
            title=f'T{pk_force}',
            description='d',
            category=self.categoria,
            created_by=self.user,
            requester_name='u',
            priority=priority,
            status=status,
        )
        t.save()
        # Ajusta pk via update só se necessário — usamos ordem natural dos ids criados
        return t

    def test_ordem_exemplo_plano(self):
        from helpdesk.queue import calcular_posicoes_fila

        # Cria na ordem dos números do exemplo (mais antigo primeiro)
        t123 = self._criar(123, Ticket.PriorityChoices.HIGH)
        t234 = self._criar(234, Ticket.PriorityChoices.LOW)
        t456 = self._criar(456, Ticket.PriorityChoices.MEDIUM)
        t567 = self._criar(567, Ticket.PriorityChoices.MEDIUM)
        t789 = self._criar(789, Ticket.PriorityChoices.URGENT)

        # Garante pks relativos iguais à ordem de criação
        tickets = [t123, t234, t456, t567, t789]
        self.assertEqual(
            [t.pk for t in tickets],
            sorted(t.pk for t in tickets),
        )

        posicoes = calcular_posicoes_fila(tickets)
        # Urgente (último criado = maior pk) em 1º; depois High; Medias por pk; Low por último
        ordem = sorted(tickets, key=lambda t: posicoes[t.pk])
        self.assertEqual(
            [t.priority for t in ordem],
            [
                Ticket.PriorityChoices.URGENT,
                Ticket.PriorityChoices.HIGH,
                Ticket.PriorityChoices.MEDIUM,
                Ticket.PriorityChoices.MEDIUM,
                Ticket.PriorityChoices.LOW,
            ],
        )
        self.assertEqual(ordem[0].pk, t789.pk)
        self.assertEqual(ordem[1].pk, t123.pk)
        self.assertEqual(ordem[2].pk, t456.pk)
        self.assertEqual(ordem[3].pk, t567.pk)
        self.assertEqual(ordem[4].pk, t234.pk)

    def test_in_progress_entra_no_ranking(self):
        from helpdesk.queue import calcular_posicoes_fila

        novo = self._criar(1, Ticket.PriorityChoices.HIGH, Ticket.StatusChoices.NEW)
        andamento = self._criar(2, Ticket.PriorityChoices.URGENT, Ticket.StatusChoices.IN_PROGRESS)
        posicoes = calcular_posicoes_fila([novo, andamento])
        self.assertEqual(posicoes[andamento.pk], 1)
        self.assertEqual(posicoes[novo.pk], 2)

    def test_em_atendimento_pesa_mais_que_novo(self):
        """
        Novo: 123 Média, 125 Alta, 126 Baixa
        Em Atendimento: 124 Média, 120 Alta, 127 Urgente
        Ordem: 127, 120, 124, 125, 123, 126
        """
        from helpdesk.queue import calcular_posicoes_fila

        t123 = self._criar(123, Ticket.PriorityChoices.MEDIUM, Ticket.StatusChoices.NEW)
        t125 = self._criar(125, Ticket.PriorityChoices.HIGH, Ticket.StatusChoices.NEW)
        t126 = self._criar(126, Ticket.PriorityChoices.LOW, Ticket.StatusChoices.NEW)
        t124 = self._criar(124, Ticket.PriorityChoices.MEDIUM, Ticket.StatusChoices.IN_PROGRESS)
        t120 = self._criar(120, Ticket.PriorityChoices.HIGH, Ticket.StatusChoices.IN_PROGRESS)
        t127 = self._criar(127, Ticket.PriorityChoices.URGENT, Ticket.StatusChoices.IN_PROGRESS)

        tickets = [t123, t125, t126, t124, t120, t127]
        posicoes = calcular_posicoes_fila(tickets)
        ordem = sorted(tickets, key=lambda t: posicoes[t.pk])
        self.assertEqual(
            [t.pk for t in ordem],
            [t127.pk, t120.pk, t124.pk, t125.pk, t123.pk, t126.pk],
        )

    def test_posicao_global_igual_para_usuario_com_visao_filtrada(self):
        """Usuário padrão vê só o próprio card, mas a posição é a da fila global."""
        from helpdesk.queue import aplicar_posicoes_fila, calcular_posicoes_fila_global

        outros = [
            self._criar(i, Ticket.PriorityChoices.URGENT)
            for i in range(4)
        ]
        meu = self._criar(99, Ticket.PriorityChoices.LOW)

        posicoes_globais = calcular_posicoes_fila_global()
        # Só o card do usuário na lista “visível”
        aplicar_posicoes_fila([meu], [])
        self.assertEqual(meu.queue_position, posicoes_globais[meu.pk])
        self.assertEqual(meu.queue_position, len(outros) + 1)


class ChamadoRestritoCriador25TestCase(TestCase):
    """Chamados do criador restrito só aparecem para o TI exclusivo (e stakeholders)."""

    def setUp(self):
        from unittest.mock import patch
        self.categoria = TicketCategory.objects.get_or_create(name='Cat Restrito', defaults={"is_active": True})[0]
        self.criador = CustomUser.objects.create_user(
            username='criador_restrito', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        self.ti_exclusivo = CustomUser.objects.create_user(
            username='ti_exclusivo', password='pass', role=CustomUser.RoleChoices.IT_USER,
        )
        self.outro_ti = CustomUser.objects.create_user(
            username='outro_ti', password='pass', role=CustomUser.RoleChoices.IT_USER,
        )
        self.admin = CustomUser.objects.create_user(
            username='admin_restrito', password='pass', role=CustomUser.RoleChoices.ADMIN,
        )
        self.ticket = Ticket.objects.create(
            title='Chamado restrito',
            description='d',
            category=self.categoria,
            created_by=self.criador,
            requester_user=self.criador,
            requester_name='Criador',
        )
        self._patcher = patch.multiple(
            'helpdesk.ticket_access',
            CRIADOR_CHAMADOS_RESTRITOS_ID=self.criador.pk,
            TI_VISUALIZADOR_EXCLUSIVO_ID=self.ti_exclusivo.pk,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_ti_exclusivo_ve_e_outro_ti_nao(self):
        self.assertTrue(usuario_pode_acessar_chamado(self.ti_exclusivo, self.ticket))
        self.assertFalse(usuario_pode_acessar_chamado(self.outro_ti, self.ticket))
        self.assertFalse(usuario_pode_acessar_chamado(self.admin, self.ticket))

        qs_ti = filtrar_chamados_para_usuario(Ticket.objects.all(), self.ti_exclusivo)
        qs_outro = filtrar_chamados_para_usuario(Ticket.objects.all(), self.outro_ti)
        self.assertIn(self.ticket, qs_ti)
        self.assertNotIn(self.ticket, qs_outro)

    def test_criador_ainda_ve_proprio_chamado(self):
        self.assertTrue(usuario_pode_acessar_chamado(self.criador, self.ticket))

    def test_solicitante_restrito_mesmo_com_outro_criador(self):
        """User 25 como solicitante também esconde o chamado dos outros TI."""
        outro_user = CustomUser.objects.create_user(
            username='abre_para_restrito', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        ticket = Ticket.objects.create(
            title='Aberto para solicitante restrito',
            description='d',
            category=self.categoria,
            created_by=outro_user,
            requester_user=self.criador,
            requester_name='Criador Restrito',
        )
        self.assertTrue(usuario_pode_acessar_chamado(self.ti_exclusivo, ticket))
        self.assertFalse(usuario_pode_acessar_chamado(self.outro_ti, ticket))
        self.assertTrue(usuario_pode_acessar_chamado(self.criador, ticket))
        self.assertTrue(usuario_pode_acessar_chamado(outro_user, ticket))

        qs_outro = filtrar_chamados_para_usuario(Ticket.objects.all(), self.outro_ti)
        self.assertNotIn(ticket, qs_outro)


class AssistenteContextualTestCase(TestCase):
    """Testes dos ajustes: menção, tags, PENDING, histórico, Central, presença."""

    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(
            name='Dúvidas', defaults={'is_active': True},
        )[0]
        self.ti = CustomUser.objects.create_user(
            username='ti_ctx', password='pass', role=CustomUser.RoleChoices.IT_USER,
        )
        self.user = CustomUser.objects.create_user(
            username='user_ctx', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        self.ticket = Ticket.objects.create(
            title='JoyTec 524 timeout',
            description='crm.joytec.com.br login timeout loja CAN',
            category=self.categoria,
            created_by=self.user,
            requester_user=self.user,
            requester_name='User Ctx',
        )

    def test_texto_menciona_assistente(self):
        from helpdesk.mentions import texto_menciona_assistente
        self.assertTrue(texto_menciona_assistente('Oi @assistente ajuda'))
        self.assertTrue(texto_menciona_assistente('@Assistente por favor'))
        self.assertFalse(texto_menciona_assistente('fala com @ti_ctx'))

    def test_definir_tag_unica(self):
        from helpdesk.assistente_services import definir_tag_chamado
        r1 = definir_tag_chamado(self.ticket.pk, 'joytec-524')
        self.assertTrue(r1['ok'])
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.tag.nome, 'joytec-524')
        r2 = definir_tag_chamado(self.ticket.pk, 'sem-internet')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.tag.nome, 'sem-internet')
        r3 = definir_tag_chamado(self.ticket.pk, limpar=True)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.tag_id)

    def test_set_status_pending_bloqueado_assistente(self):
        from helpdesk.assistente_services import AssistenteServiceError, set_ticket_status
        with self.assertRaises(AssistenteServiceError):
            set_ticket_status(self.ticket.pk, 'PENDING', via_assistente=True)
        # Humano / via API sem flag ainda pode
        ok = set_ticket_status(self.ticket.pk, 'IN_PROGRESS', via_assistente=True)
        self.assertTrue(ok['ok'])

    def test_escalar_nao_move_para_pending(self):
        from helpdesk.assistente_services import escalar_para_ti
        self.ticket.status = Ticket.StatusChoices.NEW
        self.ticket.save(update_fields=['status'])
        r = escalar_para_ti(self.ticket.pk, motivo='Erro 524 detalhado para TI')
        self.ticket.refresh_from_db()
        self.assertTrue(r['ok'])
        self.assertTrue(self.ticket.assistente_escalado)
        self.assertEqual(self.ticket.status, Ticket.StatusChoices.NEW)
        # Público breve + interno detalhado
        pubs = Comment.objects.filter(
            ticket=self.ticket, is_assistente=True, is_interno=False,
        )
        ints = Comment.objects.filter(
            ticket=self.ticket, is_assistente=True, is_interno=True,
        )
        self.assertEqual(pubs.count(), 1)
        self.assertIn('Encaminhei', pubs.first().text)
        self.assertNotIn('524', pubs.first().text)
        self.assertEqual(ints.count(), 1)
        self.assertIn('524', ints.first().text)

    def test_antirrepeticao_mensagem_publica(self):
        from helpdesk.assistente_services import AssistenteServiceError, send_assistente_message
        send_assistente_message(self.ticket.pk, 'Já verifiquei o acesso do usuário.')
        with self.assertRaises(AssistenteServiceError):
            send_assistente_message(self.ticket.pk, 'Já verifiquei o acesso do usuário.')

    def test_historico_15_5_5(self):
        from integracoes.assistente_runtime import _selecionar_historico_recente
        for i in range(20):
            Comment.objects.create(
                ticket=self.ticket, author=None, text=f'IA {i}', is_assistente=True,
            )
        for i in range(8):
            Comment.objects.create(
                ticket=self.ticket, author=self.ti, text=f'TI {i}',
            )
        for i in range(8):
            Comment.objects.create(
                ticket=self.ticket, author=self.user, text=f'User {i}',
            )
        sel = _selecionar_historico_recente(self.ticket)
        n_ia = sum(1 for c in sel if c.is_assistente)
        n_ti = sum(1 for c in sel if not c.is_assistente and c.author_id == self.ti.pk)
        n_user = sum(1 for c in sel if not c.is_assistente and c.author_id == self.user.pk)
        self.assertLessEqual(n_ia, 15)
        self.assertLessEqual(n_ti, 5)
        self.assertLessEqual(n_user, 5)
        self.assertEqual(n_ia, 15)
        self.assertEqual(n_ti, 5)
        self.assertEqual(n_user, 5)

    def test_central_retrieval_por_palavra_chave(self):
        from helpdesk.informative_retrieval import buscar_comunicados_relevantes
        from helpdesk.models import InformativeMessage
        InformativeMessage.objects.create(
            text='JoyTec fora do ar — abrir chamado com a discadora.',
            palavras_chave='joytec, discador, 524',
            created_by=self.ti,
            ativo=True,
            arquivado=False,
            valido_ate=timezone.now() + timedelta(hours=2),
        )
        itens = buscar_comunicados_relevantes(self.ticket, limite=3)
        self.assertTrue(itens)
        self.assertIn('JoyTec', itens[0]['texto'])

    def test_presence_heartbeat(self):
        from helpdesk.models import UserPresence
        from helpdesk.presence import listar_ti_online_resumo, registrar_heartbeat
        registrar_heartbeat(self.ti)
        self.assertTrue(UserPresence.objects.filter(user=self.ti).exists())
        online = listar_ti_online_resumo()
        self.assertTrue(any(u['username'] == 'ti_ctx' for u in online))

    def test_chip_tool_sem_autorizacao(self):
        from integracoes.assistente_runtime import TOOLS_CHIP_SENSIVEIS, _executar_tool, _rodada_ctx
        token = _rodada_ctx.set({'autoriza_chips': False})
        try:
            raw = _executar_tool(self.ticket.pk, 'criar_chip_operacional', {
                'line_number': '51999999999',
                'operator_id': 1,
            })
            data = __import__('json').loads(raw)
            self.assertFalse(data.get('ok'))
            self.assertIn('INTERNA', data.get('error', ''))
        finally:
            _rodada_ctx.reset(token)
        self.assertIn('criar_chip_operacional', TOOLS_CHIP_SENSIVEIS)

    def test_antirrepeticao_mensagem_interna(self):
        from helpdesk.assistente_services import AssistenteServiceError, send_assistente_message
        texto = (
            'Preciso que o comando venha em mensagem INTERNA com @assistente '
            'para eu criar o chip e transferir para o usuário solicitante.'
        )
        send_assistente_message(self.ticket.pk, texto, interno=True)
        with self.assertRaises(AssistenteServiceError):
            send_assistente_message(self.ticket.pk, texto, interno=True)
        # Variação mínima de redação também é bloqueada
        with self.assertRaises(AssistenteServiceError):
            send_assistente_message(
                self.ticket.pk, texto.replace('Preciso que', 'Por favor,'), interno=True,
            )

    def test_operadora_resolvida_por_nome(self):
        from chips.models import Operator
        from helpdesk.assistente_services import (
            AssistenteServiceError,
            _resolver_operadora_chip,
            listar_operadoras_chips,
        )
        tim = Operator.objects.create(name='TIM')
        self.assertEqual(_resolver_operadora_chip(None, 'tim').pk, tim.pk)
        self.assertEqual(_resolver_operadora_chip(tim.pk, '').pk, tim.pk)
        self.assertTrue(listar_operadoras_chips()['count'] >= 1)
        with self.assertRaises(AssistenteServiceError) as ctx:
            _resolver_operadora_chip(9999, 'Inexistente')
        # Erro deve listar as opções para a IA não pedir id à TI
        self.assertIn('TIM', str(ctx.exception))

    def test_autorizacao_chip_persiste_em_sessao(self):
        from unittest.mock import patch

        from helpdesk.presence import registrar_heartbeat
        registrar_heartbeat(self.ti)
        c1 = Comment.objects.create(
            ticket=self.ticket,
            author=self.ti,
            text='@assistente criar chip 51999999999 operadora TIM',
            is_interno=True,
        )
        with patch('integracoes.assistente_runtime._processar_assistente_inner'):
            from integracoes.assistente_runtime import processar_assistente
            processar_assistente(self.ticket.pk, comment_id=c1.pk, gatilho='mencao')
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.assistente_chip_autorizado)
        self.assertEqual(self.ticket.assistente_chip_auth_por_id, self.ti.pk)

        # Complemento interno sem @assistente mantém a autorização
        c2 = Comment.objects.create(
            ticket=self.ticket, author=self.ti, text='usar operadora TIM', is_interno=True,
        )
        capturado = {}

        def _fake(ticket_id, **kwargs):
            from integracoes.assistente_runtime import _rodada_ctx
            capturado.update(_rodada_ctx.get() or {})

        with patch('integracoes.assistente_runtime._processar_assistente_inner', _fake):
            from integracoes.assistente_runtime import processar_assistente
            processar_assistente(self.ticket.pk, comment_id=c2.pk, gatilho='continuacao')
        self.assertTrue(capturado.get('autoriza_chips'))

    def test_deepseek_desliga_thinking(self):
        from integracoes.llm import thinking_payload_para_provedor
        from integracoes.models import IntegracaoIA
        self.assertEqual(
            thinking_payload_para_provedor(IntegracaoIA.Provider.DEEPSEEK),
            {'type': 'disabled'},
        )
        self.assertIsNone(thinking_payload_para_provedor(IntegracaoIA.Provider.CHATGPT))

    def test_mencao_interna_fallback_quando_llm_falha(self):
        """@assistente interno não pode ficar mudo se a IA falhar (ex.: thinking V4)."""
        from unittest.mock import patch

        from integracoes.assistente_runtime import processar_assistente
        from integracoes.llm import LlmError
        from integracoes.models import AssistenteConfig

        cfg = AssistenteConfig.get_solo()
        cfg.ativo = True
        cfg.save(update_fields=['ativo'])

        self.ticket.requester_user = self.ti
        self.ticket.requester_name = 'TI Ctx'
        self.ticket.assigned_to = self.ti
        self.ticket.status = Ticket.StatusChoices.IN_PROGRESS
        self.ticket.save()

        c1 = Comment.objects.create(
            ticket=self.ticket,
            author=self.ti,
            text='@assistente oi tu ta ai?',
            is_interno=True,
        )
        with patch(
            'integracoes.assistente_runtime.chat_completion',
            side_effect=LlmError('timeout'),
        ):
            processar_assistente(self.ticket.pk, comment_id=c1.pk, gatilho='mencao')

        internos = Comment.objects.filter(
            ticket=self.ticket, is_assistente=True, is_interno=True,
        )
        self.assertTrue(internos.exists())
        self.assertIn('@assistente', internos.first().text)

    def test_consultar_email_acha_com_sobrenome_extra(self):
        from emails.models import EmailAccount, EmailDomain
        from helpdesk.assistente_services import consultar_email
        from mcp_api.serializers import serialize_email_account

        dominio = EmailDomain.objects.create(name='gmail.com')
        conta = EmailAccount.objects.create(
            username='vitoriacamargo.moneypromotora',
            domain=dominio,
            employee_name='Vitoria Silva',
            status=EmailAccount.StatusChoices.ACTIVE,
        )
        serial = serialize_email_account(conta)
        self.assertEqual(serial['address'], 'vitoriacamargo.moneypromotora@gmail.com')
        self.assertNotIn('last_password_reset', serial)

        r = consultar_email('Vitoria Silva Camargo')
        self.assertGreaterEqual(r['count'], 1)
        enderecos = [i['address'] for i in r['results']]
        self.assertIn('vitoriacamargo.moneypromotora@gmail.com', enderecos)

    def test_assistente_menciona_usuario_nao_ti(self):
        from helpdesk.assistente_services import send_assistente_message
        from helpdesk.models import TicketMention

        leticia = CustomUser.objects.create_user(
            username='leticia',
            password='pass',
            first_name='Leticia',
            role=CustomUser.RoleChoices.STANDARD,
        )
        r = send_assistente_message(
            self.ticket.pk,
            '@leticia e-mail vitoriacamargo.moneypromotora@gmail.com '
            'número (51) 98219-0991.',
            interno=True,
        )
        self.assertIn('leticia', r.get('mencionados') or [])
        self.assertTrue(
            TicketMention.objects.filter(ticket=self.ticket, user=leticia).exists()
        )
        self.assertTrue(self.ticket.co_authors.filter(pk=leticia.pk).exists())

    def test_limpar_recusa_reabre_chamado(self):
        from helpdesk.assistente_services import limpar_recusa_chamado, recusar_chamado

        recusar_chamado(self.ticket.pk, 'Sem resposta')
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.is_rejected)
        self.assertEqual(self.ticket.status, Ticket.StatusChoices.RESOLVED)

        r = limpar_recusa_chamado(self.ticket.pk)
        self.assertTrue(r['ok'])
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_rejected)
        self.assertEqual((self.ticket.rejection_reason or ''), '')
        self.assertEqual(self.ticket.status, Ticket.StatusChoices.IN_PROGRESS)

    def test_set_status_em_atendimento_limpa_recusa(self):
        from helpdesk.assistente_services import set_ticket_status

        self.ticket.status = Ticket.StatusChoices.RESOLVED
        self.ticket.is_rejected = True
        self.ticket.rejection_reason = 'Sem resposta'
        self.ticket.save()
        set_ticket_status(self.ticket.pk, 'IN_PROGRESS', via_assistente=True)
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_rejected)
        self.assertEqual((self.ticket.rejection_reason or ''), '')
        self.assertEqual(self.ticket.status, Ticket.StatusChoices.IN_PROGRESS)


class AssistenteQuestionarioTestCase(TestCase):
    """Questionário com opções e esclarecimento público longo."""

    def setUp(self):
        self.categoria = TicketCategory.objects.get_or_create(
            name='Dúvidas', defaults={'is_active': True},
        )[0]
        self.user = CustomUser.objects.create_user(
            username='user_q', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        self.ticket = Ticket.objects.create(
            title='Sem internet',
            description='Não entra no sistema',
            category=self.categoria,
            created_by=self.user,
            requester_user=self.user,
            requester_name='User Q',
        )

    def test_enviar_pergunta_opcoes_cria_payload(self):
        from helpdesk.assistente_services import enviar_pergunta_opcoes

        r = enviar_pergunta_opcoes(
            self.ticket.pk,
            'Qual é o problema?',
            ['Sem internet', 'Senha bloqueada', 'Outro'],
            contexto_curto='Para eu te ajudar melhor:',
        )
        self.assertTrue(r['ok'])
        comment = Comment.objects.get(pk=r['comment_id'])
        self.assertTrue(comment.is_assistente)
        self.assertFalse(comment.is_interno)
        payload = comment.structured_payload
        self.assertEqual(payload['type'], 'questionario')
        self.assertEqual(payload['status'], 'aberto')
        self.assertEqual(len(payload['opcoes']), 3)
        self.assertEqual(payload['opcoes'][0]['id'], 'a')
        self.assertIn('Qual é o problema?', comment.text)

    def test_responder_opcao_valida_e_invalida(self):
        from helpdesk.assistente_services import (
            AssistenteServiceError,
            enviar_pergunta_opcoes,
            responder_opcao_questionario,
        )

        r = enviar_pergunta_opcoes(
            self.ticket.pk,
            'Escolha o tipo',
            ['Rede', 'Acesso'],
        )
        comment = Comment.objects.get(pk=r['comment_id'])

        ok = responder_opcao_questionario(self.ticket, comment, 'b', self.user)
        self.assertTrue(ok['ok'])
        self.assertEqual(ok['escolhida_id'], 'b')
        comment.refresh_from_db()
        self.assertEqual(comment.structured_payload['status'], 'respondido')
        self.assertEqual(comment.structured_payload['escolhida_id'], 'b')

        resposta = Comment.objects.get(pk=ok['resposta_comment_id'])
        self.assertEqual(resposta.author_id, self.user.pk)
        self.assertIn('Opção selecionada: B', resposta.text)
        self.assertIn('Acesso', resposta.text)

        with self.assertRaises(AssistenteServiceError):
            responder_opcao_questionario(self.ticket, comment, 'a', self.user)

        r2 = enviar_pergunta_opcoes(
            self.ticket.pk,
            'Nova pergunta',
            ['Sim', 'Não'],
        )
        comment2 = Comment.objects.get(pk=r2['comment_id'])
        with self.assertRaises(AssistenteServiceError):
            responder_opcao_questionario(self.ticket, comment2, 'z', self.user)

    def test_esclarecimento_nao_corta_em_280(self):
        from helpdesk.assistente_services import enviar_esclarecimento

        longo = (
            'Para eu entender o caso, preciso de mais detalhes. '
            'Descreva o horário em que o erro aparece, se há mensagem na tela, '
            'se outros colegas na mesma loja também são afetados e se já tentou '
            'reiniciar o computador. '
        ) * 4
        self.assertGreater(len(longo), 280)
        r = enviar_esclarecimento(
            self.ticket.pk,
            longo,
            lacunas=['horário do erro', 'mensagem na tela'],
        )
        self.assertTrue(r['ok'])
        comment = Comment.objects.get(pk=r['comment_id'])
        self.assertEqual(comment.structured_payload['type'], 'esclarecimento')
        self.assertGreater(len(comment.text), 280)
        self.assertLessEqual(len(comment.text), 1400)
        self.assertIn('horário do erro', comment.text)


class ConsultarChipsTitularTestCase(TestCase):
    """Busca por titular atual (TRANSFER) e número com DDI."""

    def setUp(self):
        from chips.models import Batch, Chip, ChipMovement, Operator

        self.operator = Operator.objects.create(name='TIM Teste Chip')
        self.batch = Batch.objects.create(nome='Env Teste')
        self.actor = CustomUser.objects.create_user(
            username='ti_chip_q', password='x', role=CustomUser.RoleChoices.IT_USER,
        )
        # Chip entregue a outra pessoa e depois transferido para Kamilly
        self.chip = Chip.objects.create(
            line_number='51982163409',
            operator=self.operator,
            batch=self.batch,
            usage_status=Chip.UsageChoices.IN_USE,
            status=Chip.StatusChoices.ACTIVE,
        )
        ChipMovement.objects.create(
            chip=self.chip,
            employee_name='Outra Pessoa',
            action=ChipMovement.ActionChoices.DELIVERY,
            registered_by=self.actor,
        )
        ChipMovement.objects.create(
            chip=self.chip,
            employee_name='Kamilly Oliveira',
            action=ChipMovement.ActionChoices.TRANSFER,
            registered_by=self.actor,
        )

    def test_busca_por_nome_acha_titular_via_transfer(self):
        from helpdesk.assistente_services import consultar_chips

        r = consultar_chips('Kamilly')
        self.assertTrue(r['ok'])
        ids = [x['id'] for x in r['results']]
        self.assertIn(self.chip.pk, ids)
        item = next(x for x in r['results'] if x['id'] == self.chip.pk)
        self.assertEqual(item['employee_name'], 'Kamilly Oliveira')
        self.assertEqual(item['match'], 'titular_atual')

    def test_busca_por_numero_com_ddi_55(self):
        from helpdesk.assistente_services import consultar_chips

        r = consultar_chips('55 51 8216-3409')
        self.assertTrue(r['ok'])
        ids = [x['id'] for x in r['results']]
        self.assertIn(self.chip.pk, ids)
        item = next(x for x in r['results'] if x['id'] == self.chip.pk)
        self.assertEqual(item['employee_name'], 'Kamilly Oliveira')


class CentralInformativaValidadeTestCase(TestCase):
    """Validade 2h, archive, keywords e exclus�o da IA."""

    def setUp(self):
        self.ti = CustomUser.objects.create_user(
            username='ti_info', password='pass', role=CustomUser.RoleChoices.IT_USER,
        )
        self.user = CustomUser.objects.create_user(
            username='user_info', password='pass', role=CustomUser.RoleChoices.STANDARD,
        )
        self.categoria = TicketCategory.objects.get_or_create(
            name='D�vidas', defaults={'is_active': True},
        )[0]
        self.ticket = Ticket.objects.create(
            title='JoyTec 524 timeout',
            description='discador fora',
            category=self.categoria,
            created_by=self.user,
            requester_user=self.user,
            requester_name='User Info',
        )

    def test_gerar_keywords_e_validade_padrao(self):
        from helpdesk.informative_services import gerar_palavras_chave, validade_padrao
        from helpdesk.models import InformativeMessage

        chaves = gerar_palavras_chave('Instabilidade nas fichas e fluxo de chamadas JoyTec')
        self.assertIn('joytec', chaves.lower())
        self.assertNotIn('nas', chaves.split(', '))

        agora = timezone.now()
        msg = InformativeMessage.objects.create(
            text='Instabilidade nas fichas JoyTec discador',
            created_by=self.ti,
            palavras_chave=gerar_palavras_chave('Instabilidade nas fichas JoyTec discador'),
            valido_ate=validade_padrao(agora=agora),
            letreiro=True,
        )
        delta = msg.valido_ate - agora
        self.assertAlmostEqual(delta.total_seconds(), 2 * 3600, delta=5)

    def test_arquivar_expirado_esconde_de_normal_e_ia(self):
        from helpdesk.informative_retrieval import buscar_comunicados_relevantes
        from helpdesk.informative_services import arquivar_comunicados_expirados
        from helpdesk.models import InformativeMessage
        from helpdesk.ticket_access import filtrar_mensagens_informativas

        msg = InformativeMessage.objects.create(
            text='JoyTec discador 524 inst�vel',
            palavras_chave='joytec, discador, 524',
            created_by=self.ti,
            valido_ate=timezone.now() - timedelta(minutes=1),
            ativo=True,
            arquivado=False,
        )
        ids = arquivar_comunicados_expirados()
        self.assertIn(msg.pk, ids)
        msg.refresh_from_db()
        self.assertTrue(msg.arquivado)
        self.assertFalse(msg.ativo)

        ids_user = set(
            filtrar_mensagens_informativas(self.user).values_list('pk', flat=True)
        )
        self.assertNotIn(msg.pk, ids_user)
        ids_ti = set(
            filtrar_mensagens_informativas(self.ti).values_list('pk', flat=True)
        )
        self.assertIn(msg.pk, ids_ti)

        itens = buscar_comunicados_relevantes(self.ticket, limite=5)
        self.assertFalse(any(i['id'] == msg.pk for i in itens))

    def test_prorrogar_desarquiva(self):
        from helpdesk.models import InformativeMessage

        msg = InformativeMessage.objects.create(
            text='Aviso curto',
            created_by=self.ti,
            valido_ate=timezone.now() - timedelta(minutes=5),
            arquivado=True,
            ativo=False,
            arquivado_em=timezone.now(),
        )
        msg.prorrogar(horas=2)
        msg.refresh_from_db()
        self.assertFalse(msg.arquivado)
        self.assertTrue(msg.ativo)
        self.assertGreater(msg.valido_ate, timezone.now())

    def test_letreiro_so_vigente_com_flag(self):
        from helpdesk.informative_services import mensagens_letreiro_vigentes
        from helpdesk.models import InformativeMessage

        InformativeMessage.objects.create(
            text='No letreiro',
            created_by=self.ti,
            letreiro=True,
            valido_ate=timezone.now() + timedelta(hours=1),
            arquivado=False,
            ativo=True,
        )
        InformativeMessage.objects.create(
            text='Sem flag',
            created_by=self.ti,
            letreiro=False,
            valido_ate=timezone.now() + timedelta(hours=1),
            arquivado=False,
            ativo=True,
        )
        InformativeMessage.objects.create(
            text='Expirado no letreiro',
            created_by=self.ti,
            letreiro=True,
            valido_ate=timezone.now() - timedelta(minutes=1),
            arquivado=True,
            ativo=False,
        )
        textos = [m.text for m in mensagens_letreiro_vigentes()]
        self.assertIn('No letreiro', textos)
        self.assertNotIn('Sem flag', textos)
        self.assertNotIn('Expirado no letreiro', textos)
