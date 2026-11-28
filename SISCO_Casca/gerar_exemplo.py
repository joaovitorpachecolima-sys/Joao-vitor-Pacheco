"""Gera os arquivos de partida da casca, todos com dados inventados:

  entrada/SISCO_dados_exemplo.xlsx   ocorrências de exemplo (Usina A, Pessoa B...), relativas à data de hoje
  modelos/SISCO_dados_modelo.xlsx    a mesma planilha, vazia, com a aba "Como preencher"
  modelos/registro_modelo.docx       modelo do Word do registro (com os campos {{...}} que o SISCO preenche)

Uso:  python gerar_exemplo.py            (datas relativas a hoje)
      python gerar_exemplo.py --hoje 2026-11-25
"""
from __future__ import annotations

import argparse
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from importer import OCORRENCIA
from planilha import escrever

ROOT = Path(__file__).resolve().parent

# Usina, coordenação, unidades geradoras (0 = usina solar, informada como um todo).
USINAS = [("Usina A", "Coordenação Norte", 3), ("Usina B", "Coordenação Norte", 2), ("Usina C", "Coordenação Norte", 0),
          ("Usina D", "Coordenação Sul", 2), ("Usina E", "Coordenação Sul", 0), ("Usina F", "Coordenação Sul", 3),
          ("Usina G", "Coordenação Leste", 2), ("Usina H", "Coordenação Leste", 0)]
PESSOAS = ["Pessoa A", "Pessoa B", "Pessoa C", "Pessoa D", "Pessoa E"]
CLASSIFICACOES = [  # nome, tipo de fluxo, acompanha, registro formal, e-mail vai para, peso no sorteio
    ("Manutenção", "COMPLETO", True, True, "Coordenação", 45), ("Distribuidora", "SEM_COORD", True, False, "Coordenação", 15),
    ("Operacional", "SEM_COORD", True, False, "Coordenação", 15), ("TI", "TI", True, False, "Classificação", 7),
    ("Civil", "INFORMATIVO", False, False, "Coordenação", 5), ("Informativo", "INFORMATIVO", False, False, "Coordenação", 8),
    ("Ambiental", "INFORMATIVO", False, False, "Coordenação", 5)]
CASOS = {  # classificação -> (equipamento, descrição); {ug} vira a unidade sorteada
    "Manutenção": [("UG0{ug}", "Desarme da UG0{ug} por atuação da proteção de sobrecorrente. Equipe de manutenção acionada para inspeção."),
                   ("Mancal guia da UG0{ug}", "Temperatura elevada no mancal guia da UG0{ug}. Unidade retirada de operação por segurança."),
                   ("Regulador de velocidade da UG0{ug}", "Oscilação de potência na UG0{ug} causada pelo regulador de velocidade."),
                   ("Transformador elevador", "Alarme de nível de óleo baixo no transformador elevador. Inspeção solicitada."),
                   ("Comporta da tomada d'água", "Comporta da tomada d'água travada na posição intermediária.")],
    "Distribuidora": [("Linha de 69 kV", "Desligamento da linha de 69 kV pela distribuidora. Usina parada aguardando normalização."),
                      ("Religador da distribuidora", "Atuação do religador da distribuidora; tensão normalizada após o religamento.")],
    "Operacional": [("Painel de serviços auxiliares", "Falta de tensão no painel de serviços auxiliares durante manobra."),
                    ("UG0{ug}", "Partida da UG0{ug} sem sucesso por falha de sequência. Nova tentativa programada.")],
    "TI": [("Sistema de supervisão", "Perda de comunicação com o sistema de supervisão da usina."),
           ("Rede de dados", "Link de dados da usina indisponível; telemetria sem atualização.")],
    "Civil": [("Acesso à usina", "Erosão no acesso principal após chuva forte; trânsito de veículos restrito.")],
    "Informativo": [("Usina", "Visita técnica programada à usina, sem impacto na geração."),
                    ("Usina", "Teste de alarmes realizado com sucesso.")],
    "Ambiental": [("Reservatório", "Vegetação acumulada na grade de proteção do reservatório; limpeza programada.")],
}
# Usinas solares não têm UG, comporta nem mancal: nos casos de equipamento, usam estes.
CASOS_SOLAR = {
    "Manutenção": [("Inversor 0{ug}", "Inversor 0{ug} fora de operação após falha de comunicação. Geração reduzida."),
                   ("String box 0{ug}", "Fusível queimado na string box 0{ug}; parte dos módulos sem geração."),
                   ("Transformador elevador", "Alarme de temperatura no transformador elevador. Inspeção solicitada.")],
    "Operacional": [("Inversor 0{ug}", "Religamento do inversor 0{ug} sem sucesso após desligamento da rede. Nova tentativa programada.")],
    "Ambiental": [("Área dos módulos", "Vegetação alta entre as fileiras de módulos; roçada programada.")],
}


def _agora_em(hoje: date) -> datetime:
    return datetime.combine(hoje, time(9, 30))


def ocorrencias(hoje: date, quantidade: int = 96, semente: int = 7):
    rng = random.Random(semente)
    agora = _agora_em(hoje)
    pesos = [c[5] for c in CLASSIFICACOES]
    linhas = []
    for numero in range(1, quantidade + 1):
        nome, unidades_qtd = (u := rng.choice(USINAS))[0], u[2]
        classe = rng.choices(CLASSIFICACOES, weights=pesos)[0]
        classificacao, tipo, acompanha, registro = classe[0], classe[1], classe[2], classe[3]
        ug = rng.randint(1, unidades_qtd or 4)
        casos = CASOS_SOLAR.get(classificacao, CASOS[classificacao]) if unidades_qtd == 0 else CASOS[classificacao]
        equipamento, descricao = rng.choice(casos)
        equipamento, descricao = equipamento.format(ug=ug), descricao.format(ug=ug)
        dias = rng.random() ** 1.3 * 150
        inicio = (agora - timedelta(days=dias)).replace(second=0, microsecond=0)
        inicio = inicio.replace(minute=rng.choice([0, 5, 10, 15, 20, 30, 40, 45, 50]))
        idade_h = (agora - inicio).total_seconds() / 3600
        linha = {"id": 1000 + numero, "inicio": inicio, "usina": nome, "equipamento": equipamento, "classificacao": classificacao,
                 "descricao": descricao, "status": "Aberta", "origem": rng.choice(PESSOAS), "revisada": idade_h > 30 or rng.random() < 0.5}
        if numero % 23 == 0:
            linha["classificacao"] = ""      # algumas sem classificação, para a triagem
            linha["revisada"] = False
        curta = rng.random() < 0.5 or idade_h < 26
        if curta:
            duracao = rng.uniform(0.4, 20)
            if idade_h > duracao:
                linha["fim"] = inicio + timedelta(hours=duracao)
                linha["status"] = rng.choice(["Aberta", "Aberta", "Contornada"])
        elif acompanha and linha["revisada"] and linha["classificacao"] and idade_h < 24 * 20 and rng.random() < 0.25:
            pass                              # passou de 24 h e o pedido de status ainda não saiu: "Aguardando envio"
        elif acompanha and linha["revisada"] and linha["classificacao"]:
            linha["pedido"] = inicio + timedelta(hours=26, minutes=rng.randint(0, 90))
            limite = min(24 * 14, idade_h - 1)
            if limite > 30 and rng.random() < 0.65:
                fim = inicio + timedelta(hours=rng.uniform(28, limite))
                linha["fim"], linha["manutencao"] = fim, "Finalizada"
                if registro and (agora - fim).days >= 1:
                    enviado = fim + timedelta(days=1, hours=2)
                    linha["registro_gerado"] = fim + timedelta(hours=20)
                    linha["registro_enviado"] = enviado
                    if (agora - enviado).days >= 9 and rng.random() < 0.75:
                        linha["retorno"] = (enviado + timedelta(days=rng.randint(3, 8))).date()
            else:
                linha["manutencao"] = "Em Aberto"
                linha["previsao"] = (agora + timedelta(days=rng.randint(-6, 10))).date()
        elif not acompanha:
            linha["fim"] = inicio + timedelta(hours=rng.uniform(26, 60)) if idade_h > 62 else None
        linhas.append(linha)
    # Três ocorrências das últimas horas, ainda dentro das 24 h (fase de monitoramento).
    recentes = [("Usina A", "UG03", "Manutenção", "Vibração alta na UG03 após a partida. Unidade em observação."),
                ("Usina F", "Painel de serviços auxiliares", "Operacional", "Falta de tensão no painel de serviços auxiliares durante manobra."),
                ("Usina E", "Inversor 02", "Manutenção", "Inversor 02 desligado por sobretemperatura.")]
    for i, (usina, equipamento, classificacao, descricao) in enumerate(recentes):
        inicio = (agora - timedelta(hours=(3, 7, 15)[i])).replace(second=0, microsecond=0)
        linhas.append({"id": 1000 + quantidade + i + 1, "inicio": inicio, "usina": usina, "equipamento": equipamento,
                       "classificacao": classificacao, "descricao": descricao, "status": "Aberta", "origem": PESSOAS[i], "revisada": True})
    return linhas


def _linha_planilha(o):
    fim = o.get("fim")
    return [o["id"], o["inicio"].date(), o["inicio"].time(), o["usina"], o["equipamento"], o["classificacao"], o["descricao"],
            o["status"], fim.date() if fim else None, fim.time() if fim else None, o["revisada"], None, o["origem"],
            o.get("manutencao"), o.get("previsao"), o.get("pedido"), None, o.get("registro_gerado"), o.get("registro_enviado"),
            None, None, o.get("retorno"), None]


def geracao(hoje: date, linhas):
    """Último relatório de cada usina: umas unidades gerando, outras paradas (com e sem ocorrência que explique)."""
    import re
    rng = random.Random(11)
    agora = _agora_em(hoje).replace(hour=7, minute=0)
    # Unidade parada por ocorrência em aberto recente (o SISCO só liga parada a ocorrências de até 120 dias).
    abertas = {(o["usina"], f"UG0{m.group(1)}") for o in linhas if not o.get("fim") and (agora - o["inicio"]).days < 100
               for m in [re.search(r"UG0(\d)", o["equipamento"])] if m}
    saida = []
    for usina, _, unidades in USINAS:
        for momento in (agora - timedelta(days=1), agora):
            if unidades == 0:
                saida.append([momento, usina, None, f"{rng.uniform(0.6, 2.4):.2f} MW".replace(".", ","), "Sem anormalidades."])
                continue
            for ug in range(1, unidades + 1):
                if (usina, f"UG0{ug}") in abertas:
                    situacao = "Indisponível - em manutenção"
                elif usina == "Usina D" and ug == 2:
                    situacao = "Parada"                     # parada sem ocorrência: aparece como alerta
                elif usina == "Usina G" and ug == 1:
                    situacao = "Disponível"                 # parada por decisão da coordenação
                else:
                    situacao = f"{rng.uniform(1.2, 6.5):.2f} MW".replace(".", ",")
                saida.append([momento, usina, f"UG0{ug}", situacao, "Operação normal." if ug == 1 else None])
    return saida


def _abas(hoje: date, com_dados: bool):
    cabecalho = [cab for _, cab, _, _ in OCORRENCIA]
    larguras = [8, 12, 10, 12, 30, 15, 70, 14, 12, 10, 10, 20, 14, 20, 14, 22, 18, 18, 18, 14, 18, 16, 30]
    linhas = ocorrencias(hoje) if com_dados else []
    ocorr = [cabecalho] + [_linha_planilha(o) for o in linhas]
    usinas = [["Usina", "Coordenação"]] + [[u, c] for u, c, _ in USINAS]
    classes = [["Classificação", "Tipo de fluxo", "Acompanha", "Registro formal", "E-mail vai para"]] + [list(c[:5]) for c in CLASSIFICACOES]
    contatos = [["Nome", "Tipo", "Para", "CC", "CCO"],
                ["Coordenação Norte", "Coordenação", "coordenacao.norte@exemplo.com", "pessoa.a@exemplo.com", ""],
                ["Coordenação Sul", "Coordenação", "coordenacao.sul@exemplo.com", "pessoa.b@exemplo.com", ""],
                ["Coordenação Leste", "Coordenação", "coordenacao.leste@exemplo.com", "pessoa.c@exemplo.com", ""],
                ["Manutenção", "Classificação", "", "manutencao@exemplo.com", "operacao@exemplo.com"],
                ["TI", "Classificação", "suporte.ti@exemplo.com", "", "operacao@exemplo.com"]]
    gera = [["Data e hora", "Usina", "Unidade", "Situação", "Relato"]] + (geracao(hoje, linhas) if com_dados else [])
    return [("Ocorrencias", ocorr, larguras, {6}),
            ("Usinas", usinas, [14, 22], ()),
            ("Classificacoes", classes, [16, 16, 12, 16, 18], ()),
            ("Contatos", contatos, [22, 14, 32, 28, 26], ()),
            ("Geracao", gera, [18, 12, 10, 30, 40], {4})]


EXPLICACAO = {
    "id": ("Sim", "Número ou código único da ocorrência. Não repita.", "1001"),
    "start_date": ("Sim", "Dia em que a ocorrência começou.", "24/11/2026"),
    "start_time": ("Sim", "Hora em que começou.", "08:30"),
    "plant": ("Sim", "Nome da usina (igual ao da aba Usinas).", "Usina A"),
    "equipment": ("", "Equipamento ou local afetado. Cite a unidade (UG01, UG02...) quando for o caso.", "UG01"),
    "classification": ("", "Uma das classificações da aba Classificacoes. Vazio = vai para a triagem.", "Manutenção"),
    "description": ("", "Relato objetivo do que aconteceu.", "Desarme da UG01..."),
    "status": ("", "Como estava ao ser registrada: Aberta ou Contornada.", "Aberta"),
    "end_date": ("", "Dia em que terminou (vazio = ainda aberta).", "25/11/2026"),
    "end_time": ("", "Hora em que terminou.", "14:10"),
    "reviewed": ("", "Sim quando alguém já conferiu os dados. Sem revisão o SISCO não pede status nem cobra.", "Sim"),
    "coordination": ("", "Coordenação responsável. Vazio = a da usina na aba Usinas.", "Coordenação Norte"),
    "origin": ("", "Quem registrou ou de onde veio a informação.", "Pessoa A"),
    "maintenance_status": ("", "Retorno da manutenção: Em Aberto ou Finalizada.", "Em Aberto"),
    "forecast_date": ("", "Previsão de encerramento informada pela manutenção.", "28/11/2026"),
    "forms_sent_at": ("", "Quando o pedido de status foi enviado à manutenção.", "25/11/2026 10:00"),
    "forms_due_at": ("", "Próxima cobrança de status, se já combinada.", "02/12/2026 09:00"),
    "word_generated": ("", "Quando o registro (Word) foi gerado.", "26/11/2026 16:00"),
    "document_sent": ("", "Quando o registro foi enviado à coordenação.", "27/11/2026 09:00"),
    "document_deadline_date": ("", "Prazo de devolução do registro. Vazio = 7 dias após o envio.", "04/12/2026"),
    "document_next_date": ("", "Próxima cobrança do registro. Vazio = o dia seguinte ao prazo.", "05/12/2026"),
    "document_return_date": ("", "Dia em que o registro voltou preenchido.", "03/12/2026"),
    "document_link": ("", "Link do documento, se houver.", "https://..."),
}


def _aba_instrucoes():
    linhas = [["Coluna (aba Ocorrencias)", "Obrigatória", "O que colocar", "Exemplo"]]
    linhas += [[cab, EXPLICACAO[campo][0], EXPLICACAO[campo][1], EXPLICACAO[campo][2]] for campo, cab, _, _ in OCORRENCIA]
    linhas += [[], ["Outras abas (opcionais)", "", "", ""],
               ["Usinas", "", "Usina e a coordenação responsável por ela.", "Usina A | Coordenação Norte"],
               ["Classificacoes", "", "Para cada classificação: tipo de fluxo, se acompanha (pede status e cobra), se exige registro formal (Word) e para quem vai o e-mail (Coordenação ou Classificação).", "Manutenção | COMPLETO | Sim | Sim | Coordenação"],
               ["Contatos", "", "E-mails de cada coordenação e de cada classificação (Tipo = Coordenação ou Classificação). Separe vários e-mails com ponto e vírgula.", "Coordenação Norte | Coordenação | coord@empresa.com"],
               ["Geracao", "", "Situação de cada unidade geradora: uma linha por usina, unidade e momento. Situação: potência (ex.: 3,2 MW), Parada, Disponível ou Indisponível.", "25/11/2026 07:00 | Usina A | UG01 | 3,2 MW"],
               [], ["Dicas", "", "", ""],
               ["", "", "Datas como dd/mm/aaaa e horas como hh:mm. A ordem das colunas não importa; colunas a mais são ignoradas.", ""],
               ["", "", "Se a sua planilha usa outros nomes de colunas, informe-os em configuracao.json (\"colunas\"). Veja o LEIA_ME.md.", ""],
               ["", "", "Para trocar a planilha: SISCO → Sincronização → Trocar planilha. O SISCO só lê o arquivo; nunca grava nele.", ""]]
    return ("Como preencher", linhas, [28, 12, 80, 34], {2})


def documento_modelo(caminho: Path):
    paragrafos = [("Comunicado de ocorrência superior a 24 horas", True), ("", False),
                  ("Ocorrência: {{ID_OCORRENCIA}}/{{ANO}}", True), ("Usina: {{USINA}}", False), ("Equipamento: {{EQUIPAMENTO}}", False),
                  ("Data e hora da observação: {{DATA_OBSERVACAO}} às{{HORA_OBSERVACAO}}", False), ("", False),
                  ("1. Descrição operacional", True), ("Relato, procedimento adotado e situação atual:", False), ("", False), ("", False),
                  ("2. Descrição técnica", True), ("Atendimento, causa e período da manutenção:", False), ("", False), ("", False),
                  ("3. Responsáveis", True), ("Elaborado por:", False), ("Validado por:", False), ("Data:", False)]

    def par(texto, negrito):
        corrida = f'<w:r>{"<w:rPr><w:b/></w:rPr>" if negrito else ""}<w:t xml:space="preserve">{escape(texto)}</w:t></w:r>' if texto else ''
        return f'<w:p>{corrida}</w:p>'

    corpo = ''.join(par(t, n) for t, n in paragrafos)
    with ZipFile(caminho, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body>{corpo}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                   '<w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
                   '</w:body></w:document>')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--hoje', type=date.fromisoformat, default=date.today(), help='Data de referência (AAAA-MM-DD).')
    args = parser.parse_args()
    (ROOT / 'entrada').mkdir(exist_ok=True)
    (ROOT / 'modelos').mkdir(exist_ok=True)
    escrever(ROOT / 'entrada' / 'SISCO_dados_exemplo.xlsx', _abas(args.hoje, True) + [_aba_instrucoes()])
    escrever(ROOT / 'modelos' / 'SISCO_dados_modelo.xlsx', [_aba_instrucoes()] + _abas(args.hoje, False))
    documento_modelo(ROOT / 'modelos' / 'registro_modelo.docx')
    print('Gerados: entrada/SISCO_dados_exemplo.xlsx, modelos/SISCO_dados_modelo.xlsx e modelos/registro_modelo.docx')


if __name__ == '__main__':
    main()
