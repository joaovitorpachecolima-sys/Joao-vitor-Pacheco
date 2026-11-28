# SISCO — casca pronta para preencher

Controle de ocorrências operacionais (usinas, equipamentos, prazos de 24 h, retorno da manutenção, registro formal, KPIs e fluxo editável), rodando no próprio computador.

A casca vem com **dados inventados**: Usina A a H, Pessoa A a E, Coordenação Norte, Sul e Leste, e e-mails `@exemplo.com`. Para usar com dados de verdade, basta trocar a planilha.

## Ligar e desligar

- **Ligar:** dois cliques em `Iniciar SISCO.cmd`. O navegador abre em <http://127.0.0.1:8780>.
- **Desligar:** dois cliques em `Parar SISCO.cmd`.
- **Requisito:** Python 3.10 ou mais novo ([python.org](https://www.python.org)). Não precisa instalar mais nada.
- O site só abre neste computador.

## A fonte de dados é uma planilha

O SISCO lê uma planilha simples (`.xlsx`, `.xlsm` ou `.csv`) a cada minuto, **só para leitura**: nada é gravado nela.

As revisões, o histórico, os documentos e as regras ficam no banco do próprio SISCO, em `dados/`.

### Trocar a planilha

Há três jeitos; o primeiro é o mais simples.

1. **Pelo site:** Sincronização → **Trocar planilha…**. O arquivo é conferido antes. Se não tiver a coluna ID ou nenhuma ocorrência, é recusado e nada muda. Escolha antes:
   - **Atualizar a base atual:** são as mesmas ocorrências, com novas linhas ou correções. As revisões e o histórico ficam.
   - **Começar uma base nova:** são outros dados. A base anterior vai para `dados/arquivo/` (nada é apagado).
2. **Pelo arquivo:** coloque a planilha em `entrada/` e informe o nome em `configuracao.json`:

   ```json
   { "fonte_de_dados": "entrada/minha_planilha.xlsx" }
   ```

3. **Na linha de comando:** `python app.py --source caminho\da\planilha.xlsx`.

Em Sincronização também dá para **baixar a planilha modelo** (vazia, com instruções) e a **planilha atual**.

### Como preencher

Use `modelos/SISCO_dados_modelo.xlsx`. A aba **Como preencher** explica cada coluna.

- **Ocorrencias** (obrigatória): uma linha por ocorrência.
  - Só a coluna **ID** é obrigatória. As outras podem ficar vazias, e a ordem das colunas não importa.
  - Principais colunas: ID, Data início, Hora início, Usina, Equipamento, Classificação, Descrição, Status inicial, Data fim, Hora fim e Revisada.
  - Acompanhamento: Status da manutenção, Previsão de encerramento, Pedido de status enviado em e Próxima cobrança.
  - Registro formal: Registro gerado em, Registro enviado em, Prazo do registro e Retorno do registro.
  - Datas como `dd/mm/aaaa` e horas como `hh:mm`; data e hora também podem ir juntas na mesma célula.
  - **Revisada = Sim** faz a ocorrência entrar já revisada. Depois disso, a revisão é feita no SISCO.
  - ID repetido ou linha sem ID não entram, e aparecem em "Pontos de atenção".
- **Usinas** (opcional): Usina → Coordenação. Preenche a coordenação quando a ocorrência não traz.
- **Classificacoes** (opcional): para cada classificação, informe:
  - o tipo de fluxo;
  - se **acompanha** (pede status e cobra);
  - se exige **registro formal** (Word);
  - para quem vai o e-mail (Coordenação ou Classificação).

  Sem esta aba, valem as classificações padrão. Depois dá para mudar tudo no Flow Studio do site.
- **Contatos** (opcional): Nome, Tipo (Coordenação ou Classificação), Para, CC e CCO. Vários e-mails vão separados por `;`.
- **Geracao** (opcional): situação das unidades geradoras para a página Geração.
  - Colunas: Data e hora, Usina, Unidade (UG01, UG02…; vazio = a usina inteira), Situação e Relato.
  - Situação pode ser uma potência (`3,2 MW`), `Parada`, `Disponível` ou `Indisponível…`.

Se o `.csv` for o escolhido, ele vale como a aba Ocorrencias.

### Usar uma planilha que já existe

Se as colunas tiverem outros nomes, diga quais são em `configuracao.json`. A chave pode ser o nome da coluna da planilha modelo:

```json
{
  "fonte_de_dados": "entrada/controle_antigo.xlsx",
  "abas":    { "Ocorrencias": "Plan1" },
  "colunas": { "ID": "Nº", "Usina": "Planta", "Data início": "Data do evento" }
}
```

Vários nomes comuns já são reconhecidos sozinhos, como "Número", "Data de início", "Horário", "Tipo" e "Relato". O cabeçalho pode estar até a 10ª linha.

## Outras configurações (`configuracao.json`)

| Chave | Padrão | Para quê |
| --- | --- | --- |
| `fonte_de_dados` | `entrada/SISCO_dados_exemplo.xlsx` | Planilha lida pelo SISCO |
| `email_de_teste` | `voce@exemplo.com` | Único destinatário dos envios de teste (pelo Outlook clássico) |
| `porta` | `8780` | Porta do site |
| `colunas`, `abas` | vazio | Nomes diferentes de colunas e abas (veja acima) |

Mudanças em `email_de_teste` e `porta` valem depois de desligar e ligar de novo.

## Personalizar

- **Marca:** troque `static/marca/logo-branco.svg` (logo para fundo escuro) e `static/marca/simbolo.svg` pelos da sua empresa, com os mesmos nomes. Podem ser `.png`, desde que você mude os nomes em `static/index.html` e `static/app.js`.
- **Modelo do Word:** `modelos/registro_modelo.docx`. O SISCO preenche os campos `{{ID_OCORRENCIA}}`, `{{ANO}}`, `{{USINA}}`, `{{EQUIPAMENTO}}`, `{{DATA_OBSERVACAO}}` e `{{HORA_OBSERVACAO}}`.
- **Regras do processo:** pelo site, em Flow Studio (mapa, fluxo em BPMN e tabelas de decisão). Toda mudança passa por rascunho, simulação e publicação.

## O que o SISCO nunca faz

- Nunca grava na planilha de dados.
- Nunca envia e-mail sozinho: todo envio tem prévia e clique, e vai só para o `email_de_teste`.
- Não acessa a internet. Fora as bibliotecas que já estão em `static/vendor`, tudo roda local.

## Para quem mexe no código

- **Regerar os exemplos** (as datas ficam relativas a hoje): `python gerar_exemplo.py`.
- **Testes:** `python -m unittest discover`.
- **Código:**
  - `importer.py`: leitura da planilha;
  - `core.py`: banco local;
  - `workflow.py` e `fluxo.py`: regras e motor do fluxo;
  - `operations.py`: rascunhos, Word e e-mail;
  - `indicadores.py` e `gestao.py`: KPIs e gestão;
  - `app.py`: servidor e API;
  - `static/`: tela.
- **Bibliotecas de terceiros:** bpmn-js e dmn-js (licença MIT, em `static/vendor`). O logotipo do bpmn.io no canto do editor precisa continuar visível.

## Demonstração pública (no seu domínio)

Para mostrar o SISCO na internet com os dados inventados, sem depender do seu computador:

- **Modo demonstração:** com a variável `SISCO_DEMO=1`, o SISCO:
  - aceita acessos de fora, na porta da variável `PORT`;
  - aceita só os domínios de `SISCO_HOSTS`, separados por vírgula (vazio = qualquer um);
  - desliga o envio de e-mail e a troca de planilha;
  - gera os dados de exemplo com as datas de hoje e volta a base ao original a cada `SISCO_DEMO_RESET_HORAS` horas (padrão 6);
  - mostra um aviso no canto da tela.
- **Hospedagem:** o `Dockerfile` desta pasta já liga o modo demonstração. O `render.yaml`, na raiz do repositório, cria o site no Render (render.com) a partir do GitHub.
- **Domínio:** no Render, em Settings → Custom Domains, adicione o domínio. Depois, no painel de DNS do seu registrador, crie o registro que o Render indicar (normalmente um `CNAME` de `sisco` para `sisco-demo.onrender.com`). O HTTPS é emitido sozinho.

Sem a variável `SISCO_DEMO`, nada muda no uso local.
