"""Relatórios diários (Checklist Operacional): leitura das UGs e cores cruzadas com as ocorrências."""
from datetime import datetime
import unittest

import checklist

AGORA = datetime(2026, 11, 9, 12, 0)


def relatorio(pasta, linha, relato="N/A", recebido="2026-11-09T05:30:00"):
    texto = (f"Data: 09/11/2026\n{linha}\n" + "-" * 40 + "\n Posição das comportas: \n Adução \n Abertas \n" + "-" * 40 +
             f"\n Relato Operativo das últimas 24h: \n {relato} \n Solicitação de Manutenção: \n N/A \n * E-mail automático, não responder.")
    return {"pasta": pasta, "recebido": recebido, "assunto": f"{pasta} | Checklist", "texto": texto}


def ocorrencia(id_, usina, equipamento, inicio="2026-11-05", fim="", descricao="", status=""):
    return {"id": id_, "legacy_id": str(id_), "forms_id": "", "values": {"plant": usina, "equipment": equipamento, "start_date": inicio,
            "end_date": fim, "end_time": "10:00" if fim else "", "description": descricao, "maintenance_status": status}}


class LeituraDasUnidades(unittest.TestCase):
    def unidades(self, linha, pasta="PCH USINA B"):
        return [(u["nome"], u["estado"], u["mw"]) for u in checklist.ler_relatorio(relatorio(pasta, linha))["unidades"]]

    def test_formatos_reais(self):
        self.assertEqual(self.unidades("Geração Atual Total: 12,57MW \n UG01: 3,24MW UG02: 3,16MW UG03: Parado Disponivel UG04: Parado Disponivel UG05: 6,17MW"),
                         [("UG01", "generating", 3.24), ("UG02", "generating", 3.16), ("UG03", "available", None), ("UG04", "available", None), ("UG05", "generating", 6.17)])
        self.assertEqual(self.unidades("Geração Atual Total: 6.3 MW | UG01: 3.3 MW UG02: Parada disponível. UG03: 3. MW"),
                         [("UG01", "generating", 3.3), ("UG02", "available", None), ("UG03", "generating", 3.0)])
        self.assertEqual(self.unidades("Geração Atual Total: 0Kw | UG01: 0Kw UG02: 0Kw"), [("UG01", "stopped", 0.0), ("UG02", "stopped", 0.0)])
        self.assertEqual(self.unidades("Geração Atual Total: O MW | UG01: parada em manutenção UG02: parada disponivel"),
                         [("UG01", "unavailable", None), ("UG02", "available", None)])
        self.assertEqual(self.unidades("Geração Atual Total: 14,24MW | UG01: ligada com 7,15MW UG02: ligada com 7,09 MW"),
                         [("UG01", "generating", 7.15), ("UG02", "generating", 7.09)])
        self.assertEqual(self.unidades("Geração Atual Total: 8,2 MW | UG01: 8,2 MW UG02: Parada e disponível"),
                         [("UG01", "generating", 8.2), ("UG02", "available", None)])

    def test_numero_sem_unidade_de_10_11(self):
        self.assertEqual(self.unidades("Geração Atual Total: 12.58MW | UG01: 3.24MW UG02: 3.17 UG03: Parada disponivel"),
                         [("UG01", "generating", 3.24), ("UG02", "generating", None), ("UG03", "available", None)])
        self.assertEqual(self.unidades("Geração Atual da Usina: 0", "USINA G"), [("Usina", "stopped", None)])

    def test_usina_de_uma_unidade_e_kw(self):
        self.assertEqual(self.unidades("Geração Atual da Usina: 463 KW", "USINA G"), [("Usina", "generating", 0.463)])
        self.assertEqual(self.unidades("Geração Atual da Usina: 0 MW", "GGH USINA H"), [("Usina", "stopped", 0.0)])

    def test_solar_e_relato(self):
        solar = checklist.ler_relatorio({"pasta": "UFV USINA V", "texto": "Checklist Diário\n Inversor está em seu funcionamento normal? \n Sim"})
        self.assertTrue(solar["solar"])
        self.assertEqual(solar["unidades"], [])
        lido = checklist.ler_relatorio(relatorio("ECL", "Geração Atual Total: 0 MW | UG01: Parada em manutenção.", "Usina encontra-se parada devido o rompimento da tubulação."))
        self.assertEqual(lido["relato"], "Usina encontra-se parada devido o rompimento da tubulação")
        self.assertEqual(checklist.ler_relatorio(relatorio("ARB", "Geração Atual Total: 1 MW | UG01: 1 MW", "Usina N/A BARR N/A"))["relato"], "")

    def test_nome_da_pasta_casa_com_a_usina_do_controle(self):
        for pasta, usina in [("PCH USINA B", "Usina B"), ("GGH USINA H", "Usina H"), ("UFVs USINA I & USINA T", "UFV Usina I/Usina T"),
                             ("UFV USINA V", "UFV Usina V"), ("USINA E", "Usina E"), ("PCH USINA M", "Usina M")]:
            self.assertEqual(checklist.chave_usina(pasta), checklist.chave_usina(usina), pasta)


class Cores(unittest.TestCase):
    def cores(self, linha, itens, pasta="PCH USINA B"):
        usina = checklist.montar({"relatorios": [relatorio(pasta, linha)]}, itens, AGORA)["usinas"][0]
        return {u["nome"]: (u["cor"], [o["id"] for o in u["ocorrencias"]]) for u in usina["unidades"]}

    def test_verde_azul_vermelho_amarelo(self):
        linha = "Geração Atual Total: 3 MW | UG01: 3 MW UG02: Parado disponível UG03: Parada em manutenção UG04: 0 MW"
        itens = [ocorrencia(10, "Usina B", "UG-03"), ocorrencia(11, "Usina B", "GG04", fim="2026-11-06")]
        self.assertEqual(self.cores(linha, itens), {"UG01": ("verde", []), "UG02": ("azul", []), "UG03": ("vermelho", [10]), "UG04": ("amarelo", [])})

    def test_ocorrencia_da_usina_inteira_explica_e_a_da_ug_tem_preferencia(self):
        linha = "Geração Atual Total: 0 MW | UG01: Parada em manutenção UG02: parada"
        itens = [ocorrencia(20, "Usina E", "Usina", inicio="2026-10-26"), ocorrencia(21, "Usina E", "Turbina", descricao="Vazamento na UG 02")]
        cores = self.cores(linha, itens, "USINA E")
        self.assertEqual(cores["UG01"], ("vermelho", [20]))
        self.assertEqual(cores["UG02"], ("vermelho", [21]))

    def test_nao_explica_outra_usina_encerrada_antiga_ou_outra_ug(self):
        linha = "Geração Atual Total: 0 MW | UG01: Parada indisponível"
        itens = [ocorrencia(30, "Usina A", "UG01"), ocorrencia(31, "Usina B", "UG01", fim="2026-11-08"),
                 ocorrencia(32, "Usina B", "UG01", inicio="2026-05-01"), ocorrencia(33, "Usina B", "UG02")]
        self.assertEqual(self.cores(linha, itens), {"UG01": ("amarelo", [])})

    def test_manutencao_em_aberto_explica_mesmo_com_fim_preenchido(self):
        itens = [ocorrencia(40, "Usina B", "UG01", fim="2026-11-06", status="Em Aberto")]
        self.assertEqual(self.cores("Geração Atual Total: 0 MW | UG01: Parada em manutenção", itens), {"UG01": ("vermelho", [40])})

    def test_relatorio_mais_recente_e_desatualizado(self):
        dados = {"relatorios": [relatorio("USINA D", "Geração Atual Total: 1 MW | UG01: 1 MW", recebido="2026-11-09T05:00:00"),
                                relatorio("USINA D", "Geração Atual Total: 0 MW | UG01: 0 MW", recebido="2026-11-07T05:00:00")]}
        usina = checklist.montar(dados, [], AGORA)["usinas"][0]
        self.assertEqual(usina["unidades"][0]["cor"], "verde")
        self.assertFalse(usina["desatualizado"])
        velho = checklist.montar({"relatorios": [relatorio("USINA D", "UG01: 1 MW", recebido="2026-11-07T05:00:00")]}, [], AGORA)["usinas"][0]
        self.assertTrue(velho["desatualizado"])


if __name__ == "__main__":
    unittest.main()
