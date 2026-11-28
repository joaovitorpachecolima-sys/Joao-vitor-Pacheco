"""Escreve planilhas .xlsx simples (várias abas, cabeçalho em negrito, datas e horas no formato brasileiro).

Usado para gerar a planilha modelo e o exemplo; não precisa de Excel nem de bibliotecas externas.
"""
from __future__ import annotations

from datetime import date, datetime, time
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

ESTILO_DATA, ESTILO_HORA, ESTILO_DATA_HORA, ESTILO_CABECALHO, ESTILO_TEXTO_LONGO = 1, 2, 3, 4, 5
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="3"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/><numFmt numFmtId="165" formatCode="hh:mm"/><numFmt numFmtId="166" formatCode="dd/mm/yyyy hh:mm"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF003080"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _coluna(numero):
    letras = ''
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def _serial(valor):
    if isinstance(valor, datetime):
        base = datetime(1899, 12, 30)
        return (valor - base).total_seconds() / 86400
    if isinstance(valor, date):
        return (valor - date(1899, 12, 30)).days
    return (valor.hour * 3600 + valor.minute * 60 + valor.second) / 86400


def _celula(ref, valor, estilo=0):
    if valor is None or valor == '':
        return ''
    if isinstance(valor, bool):
        valor = 'Sim' if valor else 'Não'
    if isinstance(valor, datetime):
        return f'<c r="{ref}" s="{estilo or ESTILO_DATA_HORA}"><v>{_serial(valor):.10f}</v></c>'
    if isinstance(valor, date):
        return f'<c r="{ref}" s="{estilo or ESTILO_DATA}"><v>{_serial(valor)}</v></c>'
    if isinstance(valor, time):
        return f'<c r="{ref}" s="{estilo or ESTILO_HORA}"><v>{_serial(valor):.10f}</v></c>'
    estilo_xml = ' s="%d"' % estilo if estilo else ''
    if isinstance(valor, (int, float)):
        return f'<c r="{ref}"{estilo_xml}><v>{valor}</v></c>'
    texto = escape(str(valor))
    return f'<c r="{ref}" t="inlineStr"{estilo_xml}><is><t xml:space="preserve">{texto}</t></is></c>'


def _aba(linhas, larguras, texto_longo):
    xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>']
    if larguras:
        xml.append('<cols>' + ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(larguras, 1)) + '</cols>')
    xml.append('<sheetData>')
    for numero, linha in enumerate(linhas, 1):
        celulas = ''.join(_celula(f'{_coluna(i)}{numero}', valor,
                                  ESTILO_CABECALHO if numero == 1 else (ESTILO_TEXTO_LONGO if (i - 1) in texto_longo else 0))
                          for i, valor in enumerate(linha, 1))
        xml.append(f'<row r="{numero}">{celulas}</row>')
    xml.append('</sheetData></worksheet>')
    return ''.join(xml)


def escrever(caminho, abas):
    """abas: lista de (nome, linhas, larguras, colunas de texto longo). A primeira linha é o cabeçalho."""
    with ZipFile(caminho, 'w', ZIP_DEFLATED) as z:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                            for i in range(1, len(abas) + 1))
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   f'{overrides}</Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        folhas = ''.join(f'<sheet name="{escape(nome)}" sheetId="{i}" r:id="rId{i}"/>' for i, (nome, *_rest) in enumerate(abas, 1))
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets>{folhas}</sheets></workbook>')
        rels = ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                       for i in range(1, len(abas) + 1))
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{rels}<Relationship Id="rId{len(abas) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        z.writestr('xl/styles.xml', _STYLES)
        for i, (nome, linhas, larguras, texto_longo) in enumerate(abas, 1):
            z.writestr(f'xl/worksheets/sheet{i}.xml', _aba(linhas, larguras, set(texto_longo or ())))
