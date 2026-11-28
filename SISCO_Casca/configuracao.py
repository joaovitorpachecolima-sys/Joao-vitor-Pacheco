"""Configuração da casca: um arquivo JSON pequeno na pasta do programa (configuracao.json).

Tudo tem valor padrão; o arquivo só precisa ter o que você quer mudar. Exemplo:

    {
      "fonte_de_dados": "entrada/minha_planilha.xlsx",
      "email_de_teste": "voce@suaempresa.com",
      "porta": 8780
    }

Opcional, para usar uma planilha com outros nomes de colunas ou de abas (veja LEIA_ME.md):

      "colunas": {"id": "Número", "plant": "Planta"},
      "abas": {"ocorrencias": "Plan1"}
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARQUIVO = ROOT / 'configuracao.json'
PADRAO = {
    'fonte_de_dados': 'entrada/SISCO_dados_exemplo.xlsx',
    'email_de_teste': 'voce@exemplo.com',
    'porta': 8780,
    'colunas': {},
    'abas': {},
}


def ler(arquivo: Path | None = None) -> dict:
    """Configuração atual (padrões + o que estiver no arquivo)."""
    arquivo = arquivo or ARQUIVO
    try:
        salvo = json.loads(arquivo.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        salvo = {}
    except (OSError, ValueError) as erro:
        raise ValueError(f'O arquivo configuracao.json não pôde ser lido: {erro}') from None
    if not isinstance(salvo, dict):
        raise ValueError('O arquivo configuracao.json precisa ser um objeto JSON: { ... }.')
    config = {**PADRAO, **{chave: valor for chave, valor in salvo.items() if chave in PADRAO}}
    if not isinstance(config['colunas'], dict) or not isinstance(config['abas'], dict):
        raise ValueError('Em configuracao.json, "colunas" e "abas" precisam ser objetos: { ... }.')
    return config


def gravar(alteracoes: dict, arquivo: Path | None = None) -> dict:
    """Atualiza só as chaves informadas, preservando o resto do arquivo."""
    arquivo = arquivo or ARQUIVO
    try:
        atual = json.loads(arquivo.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        atual = {}
    atual.update({chave: valor for chave, valor in alteracoes.items() if chave in PADRAO})
    temporario = arquivo.with_suffix('.tmp')
    temporario.write_text(json.dumps(atual, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporario.replace(arquivo)
    return ler(arquivo)


def caminho(relativo: str | Path) -> Path:
    """Caminho absoluto: relativo à pasta do programa quando não for absoluto."""
    destino = Path(relativo).expanduser()
    return (destino if destino.is_absolute() else ROOT / destino).resolve()


def email_de_teste() -> str:
    try:
        return str(ler()['email_de_teste']).strip() or PADRAO['email_de_teste']
    except ValueError:
        return PADRAO['email_de_teste']
