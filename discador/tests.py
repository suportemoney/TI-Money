from django.test import SimpleTestCase

from integracoes.moneyconsig_client import consulta_indica_inativo, normalizar_nome_pessoa


class MoneyconsigInativoDiscadorTest(SimpleTestCase):
    def test_normaliza_junior_e_acento(self):
        self.assertEqual(
            normalizar_nome_pessoa('Alexandre Júnior'),
            normalizar_nome_pessoa('Alexandre Junior'),
        )

    def test_inativo_por_is_active_false(self):
        payload = {
            'ok': True,
            'results': [{'nome': 'Agatha Roberta', 'is_active': False}],
        }
        self.assertTrue(consulta_indica_inativo(payload, 'Agatha Roberta'))

    def test_ativo_nao_marca(self):
        payload = {
            'ok': True,
            'usuario': {'nome': 'Amanda Heck', 'is_active': True},
        }
        self.assertFalse(consulta_indica_inativo(payload, 'Amanda Heck'))

    def test_nao_encontrado_nao_marca(self):
        payload = {'ok': True, 'encontrado': False, 'results': []}
        self.assertFalse(consulta_indica_inativo(payload, 'Nome Inexistente'))

    def test_funcionario_inativo_aninhado(self):
        payload = {
            'ok': True,
            'usuario': {
                'nome': 'Alexandre Junior',
                'is_active': True,
                'funcionario': {'nome': 'Alexandre Júnior', 'ativo': False},
            },
        }
        self.assertTrue(consulta_indica_inativo(payload, 'Alexandre Júnior'))

    def test_nome_diferente_nao_marca(self):
        payload = {
            'ok': True,
            'results': [{'nome': 'Outra Pessoa', 'is_active': False}],
        }
        self.assertFalse(consulta_indica_inativo(payload, 'Agatha Roberta'))

    def test_ambiguidade_nao_marca(self):
        payload = {
            'ok': True,
            'results': [
                {'nome': 'Maria Silva', 'is_active': False},
                {'nome': 'Maria Silva', 'is_active': False},
            ],
        }
        self.assertFalse(consulta_indica_inativo(payload, 'Maria Silva'))
