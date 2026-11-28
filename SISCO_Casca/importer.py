"""Leitura da fonte de dados: uma planilha simples (.xlsx ou .xlsm) ou um arquivo .csv.

Uma linha por ocorrência, com os nomes das colunas numa linha de cabeçalho. A ordem das colunas
não importa e as colunas que faltarem ficam vazias; só o ID é obrigatório. O SISCO só lê os valores
salvos no arquivo: não abre o Excel, não roda macro e não grava nada na planilha.

Abas reconhecidas (todas opcionais, menos a de ocorrências):
  Ocorrencias      uma linha por ocorrência (se não existir, vale a primeira aba)
  Usinas           Usina -> Coordenação
  Classificacoes   Classificação -> tipo de fluxo e comportamento
  Contatos         e-mails por coordenação e por classificação
  Geracao          situação das unidades geradoras (página Geração)
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO, StringIO
import math
import os
from pathlib import Path
import posixpath
import re
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
EXTENSOES = (".xlsx", ".xlsm", ".csv")

# Colunas da aba de ocorrências: (campo interno, cabeçalho da planilha modelo, tipo, outros nomes aceitos).
OCORRENCIA = [
    ("id", "ID", "id", ("id da ocorrencia", "numero", "n", "no", "codigo")),
    ("start_date", "Data início", "date", ("data de inicio", "data da ocorrencia", "data")),
    ("start_time", "Hora início", "time", ("hora de inicio", "hora da ocorrencia", "hora", "horario")),
    ("plant", "Usina", "text", ("planta", "instalacao")),
    ("equipment", "Equipamento", "text", ("equipamento afetado", "ativo")),
    ("classification", "Classificação", "text", ("tipo de ocorrencia", "tipo", "categoria")),
    ("description", "Descrição", "text", ("relato", "descricao da ocorrencia", "resumo")),
    ("status", "Status inicial", "text", ("status cog", "status", "situacao inicial")),
    ("end_date", "Data fim", "date", ("data de fim", "data final", "data de encerramento")),
    ("end_time", "Hora fim", "time", ("hora de fim", "hora final", "hora de encerramento")),
    ("reviewed", "Revisada", "bool", ("revisao", "revisado")),
    ("coordination", "Coordenação", "text", ("regional", "area responsavel")),
    ("origin", "Origem", "text", ()),
    ("maintenance_status", "Status da manutenção", "text", ("status manutencao",)),
    ("forecast_date", "Previsão de encerramento", "date", ("previsao", "previsao de fim")),
    ("forms_sent_at", "Pedido de status enviado em", "datetime", ("pedido enviado em", "envio a manutencao")),
    ("forms_due_at", "Próxima cobrança", "datetime", ("proxima cobranca de status",)),
    ("word_generated", "Registro gerado em", "datetime", ("word gerado em",)),
    ("document_sent", "Registro enviado em", "datetime", ("word enviado em",)),
    ("document_deadline_date", "Prazo do registro", "date", ()),
    ("document_next_date", "Próxima cobrança do registro", "date", ()),
    ("document_return_date", "Retorno do registro", "date", ("data de retorno do registro",)),
    ("document_link", "Link do documento", "text", ()),
]
USINAS = [("plant", "Usina", "text", ("planta",)), ("coordination", "Coordenação", "text", ("regional",))]
CLASSIFICACOES = [("classification", "Classificação", "text", ("tipo de ocorrencia",)),
                  ("flow", "Tipo de fluxo", "text", ("fluxo", "fluxo base")),
                  ("followup", "Acompanha", "bool", ("acompanhamento",)),
                  ("coordinator", "Registro formal", "bool", ("precisa coordenador", "registro")),
                  ("recipient", "E-mail vai para", "text", ("destinatario", "enviar para"))]
CONTATOS = [("name", "Nome", "text", ("para quem", "coordenacao ou classificacao")),
            ("kind", "Tipo", "text", ()),
            ("to", "Para", "text", ("e-mail", "email", "destinatario")),
            ("cc", "CC", "text", ("copia",)),
            ("bcc", "CCO", "text", ("copia oculta", "co"))]
GERACAO = [("moment", "Data e hora", "datetime", ("data", "recebido em")),
           ("plant", "Usina", "text", ("planta",)),
           ("unit", "Unidade", "text", ("ug", "gerador", "unidade geradora")),
           ("situation", "Situação", "text", ("geracao", "status")),
           ("report", "Relato", "text", ("observacao", "observacoes"))]
ABAS = {"ocorrencias": (OCORRENCIA, ("ocorrencias", "ocorrencia")),
        "usinas": (USINAS, ("usinas",)),
        "classificacoes": (CLASSIFICACOES, ("classificacoes", "fluxos")),
        "contatos": (CONTATOS, ("contatos", "e-mails", "emails")),
        "geracao": (GERACAO, ("geracao",))}

# Campos que o resto do SISCO lê em cada ocorrência (os mesmos da leitura antiga).
CAMPOS = ("classification", "equipment", "origin", "description", "plant", "coordination", "start_date",
          "start_time", "end_date", "end_time", "status", "sla", "forms_sent", "forms_sent_at",
          "maintenance_status", "word_status", "document_sent", "forecast_date", "conclusion",
          "forms_due_at", "document_link", "document_return_date", "document_next_date", "document_deadline_date")
LABELS = {"start_date": "Data início", "start_time": "Hora início", "end_date": "Data fim", "end_time": "Hora fim"}
DIAS_REGISTRO = 7


def _text(value):
    return "" if value is None else str(value).strip()


def _normalize(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", _text(value))
                            if not unicodedata.combining(c)).casefold().split())


def _chave(value):
    """Nome de coluna ou de aba comparável: sem acento, maiúscula, pontuação ou espaço repetido."""
    return re.sub(r"[^a-z0-9]+", " ", _normalize(value)).strip()


def _id(value):
    text = _text(value)
    if not text or isinstance(value, bool):
        return ""
    try:
        number = Decimal(text)
        if number.is_finite() and number > 0 and number == number.to_integral_value():
            return str(int(number))
    except InvalidOperation:
        pass
    # IDs com letras (ex.: OC-2026-015) também valem, desde que sem espaços internos.
    return text if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,39}", text) else ""


def _date(value, date1904=False):
    if value is None or isinstance(value, bool) or _text(value) in ("", "-"):
        return ""
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < (0 if date1904 else 1):
            return ""
        value = int(value)                  # data com hora na mesma célula: vale o dia
        # Serial 60 is Excel's fictitious 1900-02-29; do not silently correct it.
        if not date1904 and value == 60:
            return ""
        try:
            epoch = date(1904, 1, 1) if date1904 else date(1899, 12, 31)
            offset = value if date1904 or value < 60 else value - 1
            return (epoch + timedelta(days=offset)).isoformat()
        except (OverflowError, ValueError):
            return ""
    text = _text(value).split(" ")[0].split("T")[0]
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return ""


def _time(value):
    if value is None or isinstance(value, bool) or _text(value) in ("", "-"):
        return ""
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0:
            return ""
        value = value % 1                   # data com hora na mesma célula: vale a hora
        seconds = min(86399, round(value * 86400))
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"
    text = _text(value)
    if " " in text and re.match(r"\d{1,4}[/-]", text):
        text = text.split(" ", 1)[1]        # "24/11/2026 08:30" -> "08:30"
    match = re.fullmatch(r"\s*(\d{1,2})\s*[:hH]\s*(\d{2})(?::(\d{2}))?\s*(?:[hH][sS]?|min(?:utos)?)?\s*", text)
    if not match:
        return ""
    hour, minute, second = (int(part or 0) for part in match.groups())
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 and second < 60 else ""


def _timestamp(value, date1904=False):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        day = _date(value, date1904)
        return day + 'T' + (_time(value % 1) or '00:00') if day else ''
    text = _text(value)
    for pattern in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                    '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(text, pattern).isoformat(timespec='minutes')
        except ValueError:
            pass
    return ''


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return _chave(value) in ("sim", "s", "x", "ok", "true", "verdadeiro", "1", "revisada", "revisado", "yes", "y")


def _open_readonly(path):
    try:
        return open(path, "rb")
    except PermissionError:
        if os.name != "nt":
            raise
        # O Excel pode estar com o arquivo aberto: pedir só leitura, sem atrapalhar quem já o usa.
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        handle = create(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
            raise
        return os.fdopen(fd, "rb")


def _read_bytes(path):
    with _open_readonly(path) as handle:
        before = os.fstat(handle.fileno())
        payload = handle.read()
        after = os.fstat(handle.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("A planilha mudou durante a leitura. Tente novamente após o salvamento.")
    return payload, datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat()


def _sheet(zip_file, path, strings):
    rows = {}
    for row in ET.fromstring(zip_file.read(path)).findall("s:sheetData/s:row", NS):
        cells = {}
        for node in row.findall("s:c", NS):
            address = node.get("r", "")
            col = re.sub(r"\d", "", address)
            value_node = node.find("s:v", NS)
            value = value_node.text if value_node is not None else None
            kind = node.get("t", "n")
            if kind == "s" and value is not None:
                value = strings[int(value)]
            elif kind == "inlineStr":
                value = "".join(n.text or "" for n in node.findall("s:is//s:t", NS))
            elif kind == "b":
                value = value == "1"
            elif kind == "n" and value is not None:
                number = float(value)
                value = int(number) if math.isfinite(number) and number.is_integer() else number
            cells[col] = {"value": value, "error": kind == "e",
                          "uncached": node.find("s:f", NS) is not None and value_node is None}
        rows[int(row.get("r"))] = cells
    return rows


def _value(row, col):
    return row.get(col, {}).get("value")


def _raw(row, headers, columns):
    return {f"{col} · {_text(_value(headers, col)) or col}": _value(row, col)
            for col in columns if col in row}


def _coluna(numero):
    letras = ""
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def _planilhas_xlsx(payload):
    """{nome da aba: linhas}, na ordem do arquivo, e se o arquivo usa o calendário de 1904."""
    try:
        with ZipFile(BytesIO(payload)) as archive:
            strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                strings = ["".join(t.text or "" for t in node.findall(".//s:t", NS))
                           for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = {node.get("Id"): node.get("Target")
                             for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                             if node.get("TargetMode") != "External"}
            sheets = {}
            for sheet in workbook.findall("s:sheets/s:sheet", NS):
                target = relationships.get(sheet.get(REL), "")
                caminho = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
                if caminho in archive.namelist():
                    sheets[sheet.get("name")] = _sheet(archive, caminho, strings)
            props = workbook.find("s:workbookPr", NS)
            return sheets, props is not None and props.get("date1904") in ("1", "true")
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError("Não foi possível ler a planilha. Salve-a como .xlsx (Pasta de Trabalho do Excel) e tente de novo.") from error


def _planilhas_csv(payload, nome):
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            texto = payload.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("O arquivo .csv não está em UTF-8 nem em Windows-1252.")
    amostra = texto[:5000]
    try:
        separador = csv.Sniffer().sniff(amostra, delimiters=";,\t").delimiter
    except csv.Error:
        separador = ";" if amostra.count(";") >= amostra.count(",") else ","
    rows = {}
    for numero, linha in enumerate(csv.reader(StringIO(texto), delimiter=separador), 1):
        rows[numero] = {_coluna(i): {"value": valor, "error": False, "uncached": False} for i, valor in enumerate(linha, 1) if valor != ""}
    return {Path(nome).stem: rows}, False


def _mapa_colunas(spec, sobrepor):
    """Cada nome de coluna aceito -> campo interno. `sobrepor` (configuracao.json) vale primeiro."""
    nomes = {}
    for campo, cabecalho, _, apelidos in spec:
        for nome in (cabecalho, campo, *apelidos):
            nomes.setdefault(_chave(nome), campo)
    for chave, nome_na_planilha in (sobrepor or {}).items():
        campo = next((c for c, cab, _, _ in spec if _chave(chave) in (_chave(c), _chave(cab))), None)
        if campo and _text(nome_na_planilha):
            nomes[_chave(nome_na_planilha)] = campo
    return nomes


def _tabela(rows, spec, sobrepor=None):
    """Acha a linha de cabeçalho (até a 10ª) e devolve (número dela, {coluna: campo}, cabeçalhos)."""
    nomes = _mapa_colunas(spec, sobrepor)
    melhor = (None, {}, {})
    for numero in sorted(rows)[:10]:
        linha = rows[numero]
        achadas = {}
        for col, cell in linha.items():
            campo = nomes.get(_chave(cell.get("value")))
            if campo and campo not in achadas.values():
                achadas[col] = campo
        if len(achadas) > len(melhor[1]):
            melhor = (numero, achadas, linha)
    return melhor


def _linhas(rows, inicio, colunas):
    for numero in sorted(rows):
        if numero <= inicio:
            continue
        linha = rows[numero]
        valores = {campo: _value(linha, col) for col, campo in colunas.items()}
        if any(_text(v) for v in valores.values()):
            yield numero, linha, valores


def _encontrar_aba(planilhas, chave, abas):
    """Nome da aba para cada papel: o informado em configuracao.json, senão pelo nome."""
    pedido = next((v for k, v in (abas or {}).items() if _chave(k) == chave), None)
    if pedido:
        nome = next((n for n in planilhas if _chave(n) == _chave(pedido)), None)
        if nome is None:
            raise ValueError(f'A aba "{pedido}" (configuracao.json) não existe no arquivo.')
        return nome
    return next((n for n in planilhas if _chave(n) in ABAS[chave][1]), None)


def _registro(numero, aba, linha, cabecalhos, valores, coordenacao_da_usina, date1904):
    avisos = []
    values = {campo: "" for campo in CAMPOS}
    for campo in ("plant", "equipment", "classification", "description", "coordination", "origin", "document_link"):
        values[campo] = _text(valores.get(campo))
    if not values["coordination"]:
        values["coordination"] = coordenacao_da_usina.get(_chave(values["plant"]), "")
    status = _chave(valores.get("status"))
    values["status"] = ("Aberta" if status in ("aberta", "aberto", "em aberto") else "Contornada" if status.startswith("contornad")
                        else "Finalizada informada" if status.startswith(("finalizad", "encerrad", "resolvid")) else _text(valores.get("status")))
    manut = _chave(valores.get("maintenance_status"))
    values["maintenance_status"] = ("Em Aberto" if manut in ("aberta", "aberto", "em aberto", "pendente", "em andamento") else
                                    "Finalizada" if manut.startswith(("finalizad", "encerrad", "concluid", "resolvid")) else _text(valores.get("maintenance_status")))
    if values["maintenance_status"] not in ("", "Em Aberto", "Finalizada"):
        avisos.append(f'Status da manutenção não reconhecido ("{values["maintenance_status"]}"): use Em Aberto ou Finalizada.')
    for campo in ("start_date", "end_date", "forecast_date", "document_deadline_date", "document_next_date", "document_return_date"):
        bruto = valores.get(campo)
        values[campo] = _date(bruto, date1904)
        if not values[campo] and _text(bruto) not in ("", "-"):
            avisos.append(f"{LABELS.get(campo, campo)}: data não reconhecida ({_text(bruto)}).")
    for campo in ("start_time", "end_time"):
        bruto = valores.get(campo)
        values[campo] = _time(bruto)
        if not values[campo] and _text(bruto) not in ("", "-"):
            avisos.append(f"{LABELS[campo]}: hora não reconhecida ({_text(bruto)}).")
    values["forms_sent_at"] = _timestamp(valores.get("forms_sent_at"), date1904)
    values["forms_sent"] = "Enviado" if values["forms_sent_at"] else ""
    values["forms_due_at"] = _timestamp(valores.get("forms_due_at"), date1904)
    if _timestamp(valores.get("word_generated"), date1904) or _bool(valores.get("word_generated")):
        values["word_status"] = "Word gerado"
    enviado = _timestamp(valores.get("document_sent"), date1904)
    if enviado:
        momento = datetime.fromisoformat(enviado)
        values["document_sent"] = f"Email enviado ({momento:%d/%m/%Y %H:%M})"
        values["word_status"] = "Word gerado"
        if not values["document_deadline_date"]:
            values["document_deadline_date"] = (momento + timedelta(days=DIAS_REGISTRO)).date().isoformat()
        if not values["document_next_date"]:
            values["document_next_date"] = (momento + timedelta(days=DIAS_REGISTRO + 1)).date().isoformat()
    for campo in ("start_date", "start_time"):
        if not values[campo]:
            avisos.append(f"{LABELS[campo]}: informação ausente ou inválida.")
    if not values["plant"]:
        avisos.append("Usina não informada.")
    if bool(values["end_date"]) != bool(values["end_time"]):
        avisos.append("Encerramento incompleto: preencha data e hora de fim.")
    if all(values[c] for c in ("start_date", "start_time", "end_date", "end_time")) and \
            values["end_date"] + values["end_time"] < values["start_date"] + values["start_time"]:
        avisos.append("O fim informado é anterior ao início.")
    revisada = _bool(valores.get("reviewed"))
    return {"source_key": f"id:{_id(valores.get('id'))}", "forms_id": "", "legacy_id": _id(valores.get("id")),
            "source_row": numero, "source_sheet": aba, "forms_row": None, "control_row": numero,
            "source_review": "Revisada" if revisada else "", "source_reviewed": revisada,
            "source_values": values, "raw_report": values["description"],
            "raw_fields": {"planilha": _raw(linha, cabecalhos, sorted(linha, key=lambda c: (len(c), c)))},
            "warnings": avisos, "preparation": {"basis": "Planilha de dados", "notes": ["Valores lidos da planilha de dados."]}}


def _configuracao(planilhas, abas, sobrepor, issues):
    """Classificações, tipos de fluxo e e-mails, quando a planilha trouxer essas abas."""
    from workflow import DEFAULT_FLOWS, DEFAULT_FLOW_TYPES
    nomes = {papel: _encontrar_aba(planilhas, papel, abas) for papel in ("usinas", "classificacoes", "contatos")}
    if not any(nomes.values()):
        return None, {}
    coordenacao_da_usina = {}
    if nomes["usinas"]:
        inicio, colunas, _ = _tabela(planilhas[nomes["usinas"]], USINAS, sobrepor)
        for _, _, v in _linhas(planilhas[nomes["usinas"]], inicio or 0, colunas):
            if _text(v.get("plant")) and _text(v.get("coordination")):
                coordenacao_da_usina[_chave(v["plant"])] = _text(v["coordination"])
    flows, tipos = [], {}
    if nomes["classificacoes"]:
        inicio, colunas, _ = _tabela(planilhas[nomes["classificacoes"]], CLASSIFICACOES, sobrepor)
        for numero, _, v in _linhas(planilhas[nomes["classificacoes"]], inicio or 0, colunas):
            nome = _text(v.get("classification"))
            if not nome:
                continue
            tipo = _text(v.get("flow")).upper() or nome.upper()
            comportamento = {"followup": _bool(v.get("followup")), "coordinator": _bool(v.get("coordinator")),
                             "recipient": "classificacao" if _chave(v.get("recipient")).startswith("classif") else "coordenacao"}
            if _chave(tipo) not in tipos:
                tipos[_chave(tipo)] = {"name": tipo, **comportamento}
            elif {k: tipos[_chave(tipo)][k] for k in comportamento} != comportamento:
                issues.append({"kind": "flow_type_mismatch", "source_row": numero,
                               "message": f'Classificações: o tipo de fluxo {tipo} aparece com comportamentos diferentes; vale a primeira linha dele.'})
            flows.append({"classification": nome, "flow": tipos[_chave(tipo)]["name"],
                          "coordinator": tipos[_chave(tipo)]["coordinator"], "followup": tipos[_chave(tipo)]["followup"]})
    if not flows:
        flows = [dict(f) for f in DEFAULT_FLOWS]
        tipos = {_chave(t["name"]): dict(t) for t in DEFAULT_FLOW_TYPES}
    routes, class_routes = [], []
    if nomes["contatos"]:
        coordenacoes = {_chave(c) for c in coordenacao_da_usina.values()}
        classificacoes = {_chave(f["classification"]) for f in flows}
        inicio, colunas, _ = _tabela(planilhas[nomes["contatos"]], CONTATOS, sobrepor)
        for numero, _, v in _linhas(planilhas[nomes["contatos"]], inicio or 0, colunas):
            nome, tipo = _text(v.get("name")), _chave(v.get("kind"))
            if not nome:
                continue
            emails = {"to": _text(v.get("to")), "cc": _text(v.get("cc")), "bcc": _text(v.get("bcc"))}
            if tipo.startswith("classif") or (not tipo and _chave(nome) in classificacoes):
                class_routes.append({"classification": nome, **emails})
            elif tipo.startswith("coord") or (not tipo and _chave(nome) in coordenacoes):
                routes.append({"coordination": nome, **emails})
            else:
                issues.append({"kind": "contact_kind", "source_row": numero,
                               "message": f'Contatos: "{nome}" não é uma coordenação nem uma classificação conhecida; informe o Tipo.'})
    return {"flows": flows, "routes": routes, "class_routes": class_routes, "flow_types": list(tipos.values())}, coordenacao_da_usina


def _geracao(planilhas, abas, sobrepor, issues, date1904):
    """Relatórios por usina no formato que a página Geração já lê (checklist.py)."""
    nome = _encontrar_aba(planilhas, "geracao", abas)
    if not nome:
        return None
    inicio, colunas, _ = _tabela(planilhas[nome], GERACAO, sobrepor)
    grupos = {}
    for numero, _, v in _linhas(planilhas[nome], inicio or 0, colunas):
        usina, momento = _text(v.get("plant")), _timestamp(v.get("moment"), date1904)
        if not usina or not momento:
            issues.append({"kind": "generation_row", "source_row": numero, "message": "Geração: linha sem usina ou sem data e hora; ignorada."})
            continue
        grupos.setdefault((usina, momento), []).append(v)
    relatorios = []
    for (usina, momento), linhas in sorted(grupos.items()):
        partes, relatos, total = [], [], 0.0
        for v in linhas:
            unidade, situacao = _text(v.get("unit")), _text(v.get("situation"))
            numero = re.search(r"\d+", unidade)
            if numero:
                partes.append(f"UG{int(numero.group()):02d}: {situacao}")
            else:
                partes.append(situacao)
            mw = re.search(r"(\d+(?:[.,]\d+)?)\s*mw", situacao, re.I)
            total += float(mw.group(1).replace(",", ".")) if mw else 0.0
            if _text(v.get("report")):
                relatos.append(_text(v.get("report")))
        if any(p.startswith("UG") for p in partes):
            texto = f"Geração atual total: {total:.2f} MW | " + " ".join(p for p in partes if p.startswith("UG"))
        else:
            texto = "Geração atual da usina: " + " ".join(partes)
        texto += " ------ Relato operativo: " + (" ".join(relatos) or "NA")
        relatorios.append({"pasta": usina, "recebido": momento, "assunto": f"Relatório diário · {usina}", "texto": texto})
    return relatorios


def ler_conteudo(payload: bytes, nome: str, colunas=None, abas=None) -> dict:
    """Lê o arquivo (bytes) e devolve as ocorrências, os pontos de atenção e a configuração encontrada."""
    extensao = Path(nome).suffix.lower()
    if extensao not in EXTENSOES:
        raise ValueError("Use uma planilha .xlsx (ou .xlsm) ou um arquivo .csv.")
    planilhas, date1904 = _planilhas_csv(payload, nome) if extensao == ".csv" else _planilhas_xlsx(payload)
    if not planilhas:
        raise ValueError("O arquivo não tem nenhuma aba com dados.")
    aba = _encontrar_aba(planilhas, "ocorrencias", abas) or next(iter(planilhas))
    rows = planilhas[aba]
    inicio, mapa, cabecalhos = _tabela(rows, OCORRENCIA, colunas)
    if "id" not in mapa.values():
        raise ValueError(f'A aba "{aba}" não tem a coluna ID (ou ela está com outro nome). '
                         'Use a planilha modelo ou informe o nome da coluna em configuracao.json.')
    issues = []
    configuracao, coordenacao_da_usina = _configuracao(planilhas, abas, colunas, issues) if extensao != ".csv" else (None, {})
    linhas = list(_linhas(rows, inicio, mapa))
    contagem = Counter(_id(v.get("id")) for _, _, v in linhas)
    records = []
    for numero, linha, valores in linhas:
        lid = _id(valores.get("id"))
        if not lid:
            issues.append({"kind": "invalid_control_id", "source_row": numero, "source_sheet": aba,
                           "message": f"Linha {numero} sem ID válido; não importada.",
                           "raw_fields": {"planilha": _raw(linha, cabecalhos, sorted(linha, key=lambda c: (len(c), c)))}})
            continue
        if contagem[lid] > 1:
            continue
        records.append(_registro(numero, aba, linha, cabecalhos, valores, coordenacao_da_usina, date1904))
    for lid, vezes in contagem.items():
        if lid and vezes > 1:
            issues.append({"kind": "duplicate_legacy_id", "legacy_id": lid, "source_sheet": aba,
                           "message": f"O ID {lid} aparece {vezes} vezes; nenhuma dessas linhas foi importada. Corrija na planilha.",
                           "rows": [{"source_row": n, "raw_fields": {"planilha": _raw(l, cabecalhos, sorted(l, key=lambda c: (len(c), c)))}}
                                    for n, l, v in linhas if _id(v.get("id")) == lid]})
    reconhecidas = sorted({campo for campo in mapa.values()})
    stats = {"source_rows": len(linhas), "records": len(records), "issue_count": len(issues),
             "records_with_warnings": sum(bool(r["warnings"]) for r in records)}
    resultado = {"records": records, "issues": issues, "stats": stats, "sheet": aba,
                 "columns": [cab for campo, cab, _, _ in OCORRENCIA if campo in reconhecidas]}
    if configuracao:
        resultado["configuration"] = configuracao
    geracao = _geracao(planilhas, abas, colunas, issues, date1904) if extensao != ".csv" else None
    if geracao is not None:
        resultado["geracao"] = geracao
        stats["issue_count"] = len(issues)
    return resultado


def read_source(path, colunas=None, abas=None) -> dict:
    """Uma leitura completa da fonte; nunca executa, atualiza ou grava o arquivo."""
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"Arquivo de dados não encontrado: {source_path.name}. Escolha a planilha em Sincronização.")
    payload, saved_at = _read_bytes(source_path)
    resultado = ler_conteudo(payload, source_path.name, colunas, abas)
    return {"source_path": str(source_path), "sha256": sha256(payload).hexdigest(), "saved_at": saved_at, **resultado}
