"""
Situação dos grupos geradores a partir dos relatórios diários (Checklist Operacional).

Os relatórios vêm da aba "Geracao" da planilha de dados (uma linha por usina, unidade e momento) e ficam em
dados/checklist.json. Aqui o texto é interpretado e cruzado com as ocorrências em aberto:

  verde    = gerando
  azul     = parada e disponível (o coordenador optou por deixar parada)
  vermelho = parada/indisponível e há ocorrência em aberto que explica
  amarelo  = parada/indisponível e NENHUMA ocorrência explica (abrir ocorrência?)
  cinza    = texto não reconhecido
Nenhum efeito externo neste módulo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import re
import unicodedata

PREFIXOS_USINA = re.compile(r"^(pch|cgh|ggh|ufvs?|uhe)\s+", re.I)
TRECHO_GERACAO = re.compile(r"gera[çc][ãa]o\s+atual\s+(?:total|da\s+usina)\s*:\s*(?P<resto>.*?)(?:-{6,}|posi[çc][ãa]o\s+d|relato\s+operativo|$)", re.I | re.S)
TOTAL = re.compile(r"^\s*(?P<total>[^|\n]*?)\s*(?:\||\n|\bUG|\bGG|$)", re.I)
UNIDADE = re.compile(r"\b(?:UG|GG)\s*-?\s*0*(?P<n>\d{1,2})\s*:\s*(?P<texto>.*?)(?=\b(?:UG|GG)\s*-?\s*0*\d{1,2}\s*:|$)", re.I | re.S)
POTENCIA = re.compile(r"(?P<valor>\d+(?:[.,]\d*)?)\s*(?P<unidade>mw|kw)\b", re.I)
RELATO = re.compile(r"relato\s+operativo[^:\n]*:\s*(?P<relato>.*?)(?:solicita[çc][ãa]o\s+de\s+manuten|\*\s*e-mail\s+autom|$)", re.I | re.S)
MENCAO_UG = re.compile(r"\b(?:ug|gg|unidade\s+geradora)\s*-?\s*0*(\d{1,2})\b", re.I)
SOLAR = re.compile(r"inversor\s+est[áa]\s+em\s+seu\s+funcionamento", re.I)
INDISPONIVEL = ("indisponiv", "manutenc", "falha", "trip", "desarm", "bloque", "defeito", "avaria", "quebr")
DIAS_OCORRENCIA = 120     # ocorrência sem encerramento mais antiga que isso não explica parada de hoje
HORAS_DESATUALIZADO = 30  # relatório mais velho que isso aparece como desatualizado


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def compacto(texto) -> str:
    return re.sub(r"[^a-z0-9]", "", normalizar(texto))


def chave_usina(nome) -> str:
    """'PCH USINA A' = 'Usina A'; 'UFVs USINA B & USINA C' = 'UFV Usina B/Usina C'."""
    return compacto(PREFIXOS_USINA.sub("", normalizar(nome).strip()))


def limpar(texto) -> str:
    texto = str(texto or "").replace("ㅤ", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", texto).strip(" |.;-")


def potencia_mw(texto) -> float | None:
    # "O MW" (letra O) aparece nos relatórios como zero.
    texto = re.sub(r"(?<![\w,.])[oO](?=\s*(?:mw|kw)\b)", "0", str(texto or ""), flags=re.I)
    achado = POTENCIA.search(texto)
    if not achado:
        return None
    valor = float(achado["valor"].replace(",", ".").rstrip(".") or 0)
    return valor / 1000 if achado["unidade"].lower() == "kw" else valor


def estado(texto) -> str:
    """generating, available, unavailable, stopped (parada sem dizer se está disponível) ou unknown."""
    t, mw = normalizar(texto), potencia_mw(texto)
    numero = re.fullmatch(r"\s*(\d+(?:[.,]\d*)?)\s*", str(texto or ""))
    if numero and mw is None:
        # Número sem unidade ("0", "3.17"): a unidade foi esquecida, mas zero é parada e acima de zero é geração.
        return "generating" if float(numero[1].replace(",", ".").rstrip(".") or 0) > 0 else "stopped"
    if mw is not None and mw > 0:
        return "generating"
    if any(palavra in t for palavra in INDISPONIVEL):
        return "unavailable"
    if "disponiv" in t:
        return "available"
    if mw == 0 or "parad" in t or "deslig" in t or "fora" in t:
        return "stopped"
    if "ligad" in t or "operand" in t or "gerando" in t:
        return "generating"
    return "unknown"


def ler_relatorio(relatorio: dict) -> dict:
    texto = str(relatorio.get("texto") or "").replace("ㅤ", " ")
    base = {"pasta": relatorio.get("pasta", ""), "recebido": relatorio.get("recebido", ""),
            "assunto": relatorio.get("assunto", ""), "solar": False, "total": "", "total_mw": None,
            "unidades": [], "relato": ""}
    relato = RELATO.search(texto)
    if relato:
        base["relato"] = "" if compacto(relato["relato"]) in ("na", "usinanabarrna", "") else limpar(relato["relato"])[:600]
    geracao = TRECHO_GERACAO.search(texto)
    if not geracao:
        base["solar"] = bool(SOLAR.search(texto))
        return base
    resto = geracao["resto"]
    total = TOTAL.search(resto)
    base["total"] = limpar(total["total"]) if total else ""
    base["total_mw"] = potencia_mw(base["total"])
    unidades = [{"nome": f"UG{int(m['n']):02d}", "texto": limpar(m["texto"])} for m in UNIDADE.finditer(resto)]
    if not unidades and re.search(r"da\s+usina", geracao.group(0), re.I):
        # Usina de uma unidade só: o relatório informa só a geração da usina.
        unidades = [{"nome": "Usina", "texto": base["total"]}]
    for unidade in unidades:
        unidade["estado"] = estado(unidade["texto"])
        unidade["mw"] = potencia_mw(unidade["texto"])
    base["unidades"] = unidades
    return base


def _data(valor) -> date | None:
    try:
        return datetime.strptime(str(valor or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def em_aberto(item, hoje: date, dias: int = DIAS_OCORRENCIA) -> bool:
    """Sem encerramento registrado (ou manutenção 'Em Aberto') e iniciada nos últimos `dias` dias."""
    v = item.get("values") or {}
    inicio = _data(v.get("start_date"))
    if not inicio or (hoje - inicio).days > dias:
        return False
    sem_fim = not (v.get("end_date") and v.get("end_time"))
    return sem_fim or normalizar(v.get("maintenance_status")).strip() == "em aberto"


def unidades_citadas(item) -> set[int]:
    v = item.get("values") or {}
    return {int(n) for n in MENCAO_UG.findall(f"{v.get('equipment', '')} {v.get('description', '')}")}


def explica(item, unidade: str, usina: str) -> str | None:
    """'unidade' quando a ocorrência cita a UG; 'usina' quando é da usina inteira; None se não explica."""
    v = item.get("values") or {}
    citadas = unidades_citadas(item)
    if unidade == "Usina":
        return "usina"
    if int(unidade[2:]) in citadas:
        return "unidade"
    equipamento = compacto(v.get("equipment"))
    if not citadas and (not equipamento or equipamento.startswith("usina") or chave_usina(v.get("equipment")) == usina):
        return "usina"
    return None


def montar(dados: dict, itens: list[dict], agora: datetime | None = None, dias: int = DIAS_OCORRENCIA) -> dict:
    """Situação de cada usina pelo relatório mais recente, com a cor de cada grupo gerador."""
    agora = agora or datetime.now()
    hoje = agora.date()
    ultimos = {}
    for relatorio in dados.get("relatorios") or []:
        chave = chave_usina(relatorio.get("pasta"))
        if chave and (chave not in ultimos or str(relatorio.get("recebido")) > str(ultimos[chave].get("recebido"))):
            ultimos[chave] = relatorio
    abertas = [item for item in itens if em_aberto(item, hoje, dias)]
    usinas = []
    for chave, relatorio in sorted(ultimos.items(), key=lambda par: par[1].get("pasta", "")):
        lido = ler_relatorio(relatorio)
        da_usina = [item for item in abertas if chave_usina((item.get("values") or {}).get("plant")) == chave]
        for unidade in lido["unidades"]:
            unidade["ocorrencias"] = []
            if unidade["estado"] in ("unavailable", "stopped"):
                candidatas = [(explica(item, unidade["nome"], chave), item) for item in da_usina]
                especificas = [item for tipo, item in candidatas if tipo == "unidade"]
                gerais = [item for tipo, item in candidatas if tipo == "usina"]
                escolhidas = sorted(especificas or gerais, key=lambda i: str((i.get("values") or {}).get("start_date")), reverse=True)
                unidade["ocorrencias"] = [{"id": item["id"], "legacy_id": item.get("legacy_id", ""), "forms_id": item.get("forms_id", ""),
                                           "inicio": (item.get("values") or {}).get("start_date", ""),
                                           "equipamento": (item.get("values") or {}).get("equipment", ""),
                                           "descricao": str((item.get("values") or {}).get("description", ""))[:240],
                                           "da_usina": not especificas} for item in escolhidas[:3]]
                unidade["cor"] = "vermelho" if escolhidas else "amarelo"
            else:
                unidade["cor"] = {"generating": "verde", "available": "azul"}.get(unidade["estado"], "cinza")
        try:
            idade = (agora - datetime.fromisoformat(lido["recebido"])).total_seconds() / 3600
        except ValueError:
            idade = None
        lido.update(chave=chave, desatualizado=idade is None or idade > HORAS_DESATUALIZADO,
                    usina=next(((item.get("values") or {}).get("plant") for item in itens
                                if chave_usina((item.get("values") or {}).get("plant")) == chave), lido["pasta"]))
        usinas.append(lido)
    return {"coletado_em": dados.get("coletado_em", ""), "situacao": dados.get("situacao", "sem_dados"),
            "erro": dados.get("erro", ""), "usinas": usinas}


def carregar(caminho) -> dict:
    try:
        with open(caminho, encoding="utf-8-sig") as arquivo:
            return json.load(arquivo)
    except FileNotFoundError:
        return {"situacao": "sem_dados", "relatorios": []}
    except (OSError, ValueError) as erro:
        return {"situacao": "erro", "erro": f"Leitura dos relatórios: {erro}", "relatorios": []}
