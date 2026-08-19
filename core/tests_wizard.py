import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import CustomUser
from core.wizard import usuario_pode_wizard
from integracoes.gestor_runtime import (
    executar_tool_gestor,
    mensagem_confirma_mutacao,
    montar_contexto_pagina,
)
from integracoes.markdown_safe import render_markdown_leve
from integracoes.wizard_anexos import extrair_anexos_wizard


class WizardConfirmacaoTest(TestCase):
    def test_confirma_frases_explicidas(self):
        self.assertTrue(mensagem_confirma_mutacao('confirma'))
        self.assertTrue(mensagem_confirma_mutacao('Sim'))
        self.assertTrue(mensagem_confirma_mutacao('pode executar'))
        self.assertTrue(mensagem_confirma_mutacao('faça'))
        self.assertTrue(mensagem_confirma_mutacao('Faça'))
        self.assertTrue(mensagem_confirma_mutacao('ok, faz'))
        self.assertFalse(mensagem_confirma_mutacao('confirma se o ramal está livre'))
        self.assertFalse(mensagem_confirma_mutacao('libera os inativos'))

    def test_snapshot_entra_no_contexto(self):
        texto = montar_contexto_pagina({
            'path': '/discador/',
            'query': '?tab=acessos',
            'title': 'JoyTec',
            'tabelas': 'Agatha Roberta\t2758011',
            'ticket_id': None,
        })
        self.assertIn('Agatha Roberta', texto)
        self.assertIn('/discador/', texto)
        self.assertIn('tab=acessos', texto)

    def test_mutacao_sem_confirma_nao_executa(self):
        ctx = {'confirma': False, 'ticket_id': None, 'actor': None, 'mutacoes': []}
        out = json.loads(executar_tool_gestor(
            'liberar_acesso_discador', {'acesso_id': 9}, ctx,
        ))
        self.assertTrue(out.get('precisa_confirmacao'))
        self.assertFalse(out.get('ok'))
        self.assertEqual(ctx['mutacoes'], [])
        self.assertEqual(len(ctx.get('plano') or []), 1)
        self.assertEqual(ctx['plano'][0]['tool'], 'liberar_acesso_discador')
        self.assertEqual(ctx['plano'][0]['args']['acesso_id'], 9)

    def test_criar_ramal_sem_confirma_nao_executa(self):
        ctx = {'confirma': False, 'ticket_id': None, 'actor': None, 'mutacoes': []}
        out = json.loads(executar_tool_gestor(
            'criar_ramal_discador', {'numero': '1001'}, ctx,
        ))
        self.assertTrue(out.get('precisa_confirmacao'))
        self.assertEqual(ctx['mutacoes'], [])
        self.assertEqual(ctx['plano'][0]['tool'], 'criar_ramal_discador')

    def test_tabela_markdown_vira_table(self):
        html = render_markdown_leve('| a | b |\n|---|---|\n| 1 | 2 |')
        self.assertIn('<table', html)
        self.assertIn('memoria-md-table', html)
        self.assertIn('<th>', html)
        self.assertIn('1', html)


class WizardGateHttpTest(TestCase):
    def setUp(self):
        self.gestor = CustomUser.objects.create_user(
            username='gestor.wizard',
            password='x',
            role=CustomUser.RoleChoices.IT_USER,
        )
        self.outro = CustomUser.objects.create_user(
            username='outro.wizard',
            password='x',
            role=CustomUser.RoleChoices.IT_USER,
        )

    def test_usuario_allowlist(self):
        with override_settings(GESTOR_WIZARD_USER_IDS=[self.gestor.pk]):
            self.assertTrue(usuario_pode_wizard(self.gestor))
            self.assertFalse(usuario_pode_wizard(self.outro))

    def test_outro_recebe_403(self):
        self.client.force_login(self.outro)
        with override_settings(GESTOR_WIZARD_USER_IDS=[self.gestor.pk]):
            resp = self.client.post(
                reverse('wizard_chat'),
                data=json.dumps({'message': 'oi', 'pagina': {'tabelas': 'NOME Agatha'}}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 403)

    @patch('core.wizard_views.processar_gestor')
    def test_allowlist_envia_snapshot(self, mock_proc):
        mock_proc.return_value = {'reply': 'plano ok', 'mutacoes': []}
        self.client.force_login(self.gestor)
        with override_settings(GESTOR_WIZARD_USER_IDS=[self.gestor.pk]):
            resp = self.client.post(
                reverse('wizard_chat'),
                data=json.dumps({
                    'message': 'nessa tabela, libere os inativos',
                    'pagina': {
                        'path': '/discador/',
                        'query': '?tab=acessos',
                        'title': 'JoyTec',
                        'tabelas': 'Agatha Roberta',
                    },
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get('ok'))
        self.assertEqual(payload.get('reply'), 'plano ok')
        kwargs = mock_proc.call_args.kwargs
        self.assertEqual(kwargs['pagina']['tabelas'], 'Agatha Roberta')
        self.assertIn('inativos', kwargs['mensagem'])

    @patch('core.wizard_views.processar_gestor')
    def test_post_repassa_aguardando_confirmacao(self, mock_proc):
        plano = [{
            'tool': 'atualizar_acesso_discador',
            'args': {'acesso_id': 12, 'titular_nome': 'Ana'},
        }]
        mock_proc.return_value = {
            'reply': 'confira o plano',
            'mutacoes': [],
            'plano': plano,
            'aguardando_confirmacao': True,
        }
        self.client.force_login(self.gestor)
        with override_settings(GESTOR_WIZARD_USER_IDS=[self.gestor.pk]):
            resp = self.client.post(
                reverse('wizard_chat'),
                data=json.dumps({'message': 'corrija o nome', 'pagina': {}}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get('aguardando_confirmacao'))
        self.assertEqual(payload.get('plano'), plano)

    def test_fab_so_para_allowlist(self):
        with override_settings(GESTOR_WIZARD_USER_IDS=[self.gestor.pk]):
            self.client.force_login(self.gestor)
            html_ok = self.client.get(reverse('dashboard')).content.decode()
            self.client.force_login(self.outro)
            html_nao = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('id="gestor-wizard-root"', html_ok)
        self.assertNotIn('{# Wizard', html_ok)
        self.assertNotIn('gestor-wizard-root', html_nao)


class WizardAnexosTest(TestCase):
    def test_csv_entra_no_texto(self):
        import base64
        bruto = 'nome;login\nAgatha Roberta;agatha.r\n'.encode('utf-8')
        texto = extrair_anexos_wizard([{
            'nome': 'lista.csv',
            'mime': 'text/csv',
            'data_base64': base64.b64encode(bruto).decode(),
        }])
        self.assertIn('Agatha Roberta', texto)
        self.assertIn('lista.csv', texto)

    @patch('core.wizard_views.processar_gestor')
    def test_post_envia_anexos_texto(self, mock_proc):
        import base64
        mock_proc.return_value = {'reply': 'vi o csv', 'mutacoes': []}
        gestor = CustomUser.objects.create_user(
            username='gestor.anexo',
            password='x',
            role=CustomUser.RoleChoices.IT_USER,
        )
        bruto = 'ramal,titular\n1001,Ana\n'.encode('utf-8')
        self.client.force_login(gestor)
        with override_settings(GESTOR_WIZARD_USER_IDS=[gestor.pk]):
            resp = self.client.post(
                reverse('wizard_chat'),
                data=json.dumps({
                    'message': 'cadastre esses ramais',
                    'pagina': {'path': '/discador/'},
                    'anexos': [{
                        'nome': 'ramais.csv',
                        'mime': 'text/csv',
                        'data_base64': base64.b64encode(bruto).decode(),
                    }],
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        kwargs = mock_proc.call_args.kwargs
        self.assertIn('Ana', kwargs['anexos_texto'])
        self.assertIn('ramais.csv', kwargs['anexos_texto'])
