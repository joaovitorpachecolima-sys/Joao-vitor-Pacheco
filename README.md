# Oficina de Código

Uma plataforma para aprender a programar do zero, construindo uma versão mini do **Meu Espaço**.

É um arquivo só (`index.html`), sem instalação e sem compilação, igual ao app original. O progresso fica salvo no navegador.

## Como abrir

- **No computador:** baixe o `index.html` e abra com dois cliques. Ele abre no navegador.
- **Como site:** em *Settings → Pages* deste repositório, escolha a branch e a pasta `/ (root)`. O GitHub publica o arquivo num endereço `https://...github.io/...`, que também abre no celular.

## O que tem dentro

- **36 lições curtas**, em 6 módulos: HTML, CSS, JavaScript, JavaScript na página, guardando dados e o projeto final.
- **Editor de código de verdade**, com cores, números de linha e teclas rápidas no celular.
- **Prévia ao vivo** do que você escreve, e um console que explica os erros em português.
- **Desafios corrigidos na hora**: o botão Verificar testa o seu código e marca cada objetivo cumprido.
- **Projeto final em 6 etapas**: um app com matérias, notas, média, situação (Aprovado, VS ou Reprovado), telas, tema escuro e dados salvos. No fim dá para baixar o seu app como arquivo `.html`.
- **Raio-X do Meu Espaço**: trechos reais do código do app explicados, cada um ligado à lição que o ensina.
- **Bloco livre**: um editor sem desafio, para testar ideias.

## P3 de Circuitos CC (`circuitos/index.html`)

Estudo visual para a P3 de TEE00190 (Circuitos Elétricos de Corrente Contínua, UFF): transitórios de 1ª e 2ª ordem.

- **8 etapas** com laboratórios: circuito RC/RL ao vivo com osciloscópio, as "três fotografias" (0⁻, 0⁺, ∞) com a corrente andando pelos fios, constante de tempo, curva de 1ª ordem, laboratório RLC e um resolvedor passo a passo de equações de 2ª ordem.
- **As provas de 2024.1 a 2026.1 resolvidas**: cada item tem campo de resposta com correção e resolução passo a passo, com o circuito redesenhado em cada instante.
- **Simulado**: os mesmos circuitos das provas, com valores sorteados, corrigidos por um simulador de circuitos (análise nodal) que roda na própria página.
- **Folha de fórmulas** para revisar na véspera.

As respostas das provas foram conferidas duas vezes: à mão e pelo simulador.

## Para curiosos

O próprio `index.html` usa as técnicas da trilha: variáveis CSS e tema escuro, `render()`, um ouvinte de clique com `data-acao` e o estado salvo no `localStorage`. As lições ficam em blocos `<textarea class="licao">` no meio do arquivo. Para criar uma lição nova, copie um desses blocos.
