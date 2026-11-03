# Três projetos com APIs abertas em Python

Três mini-projetos de análise e visualização de dados públicos:

| Pasta | Projeto | API | Autenticação |
|---|---|---|---|
| `1_legislativo/` | Dashboard de atividade legislativa | Dados Abertos da Câmara dos Deputados | Não precisa (token opcional) |
| `2_voos/` | Rastreador de voos sobre o Brasil | OpenSky Network | Opcional (obrigatória para chegadas/partidas) |
| `3_nasa/` | Dashboard de exploração espacial | NASA Open APIs, Exoplanet Archive e EONET | Chave gratuita da NASA |

## Instalação

Requer Python 3.9+ (recomendado 3.10 ou mais novo).

```bash
python -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 1. Dashboard Legislativo (Câmara dos Deputados)

Script: `1_legislativo/dashboard_legislativo.py`. Todos os gráficos são salvos como PNG na pasta `saida/`. Se quiser que o gráfico também abra numa janela, coloque `--mostrar` **antes** do nome do comando.

```bash
cd 1_legislativo

# Desafio 1: ranking de deputados por tema (barras horizontais)
python dashboard_legislativo.py ranking --tema saude --ano 2025 --top 15

# Desafio 2: heatmap de participação em votações por partido x mês
python dashboard_legislativo.py heatmap --ano 2025

# Extras
python dashboard_legislativo.py atividade --ano 2025 --tema educacao
python dashboard_legislativo.py despesas --deputado "Nome do Deputado" --ano 2025
```

**Como funciona**

- **Paginação:** a função `buscar_todas_paginas` segue o link `rel="next"` que a API devolve em cada resposta, até acabar as páginas.
- **Dados relacionais:** no ranking, a lista de proposições (`/proposicoes?keywords=...`) não traz o autor. Por isso o script consulta `/proposicoes/{id}/autores` para cada uma e conta quantas proposições cada deputado assinou. No fim, busca partido e UF só dos deputados que entraram no top.
- **Heatmap:** para cada mês, pega uma amostra de votações, busca os votos de cada uma (`/votacoes/{id}/votos`) e agrupa por `deputado_.siglaPartido` (JSON aninhado). O valor de cada célula é a **média de votos do partido por votação nominal**, o que mantém os meses comparáveis mesmo analisando quantidades diferentes de votações. Votações simbólicas não têm voto individual e ficam de fora, e meses sem nenhuma votação nominal aparecem em cinza.
- **Autenticação:** a API da Câmara é aberta. Se precisar, defina `CAMARA_TOKEN` no ambiente e o script envia como `Bearer`.

**Cuidados**

- O ranking faz 1 chamada por proposição. Por padrão limita a 500 proposições (`--max-proposicoes`). Aumente com calma.
- O ranking depende da busca por palavra-chave da própria API (`keywords`). Termos sem acento costumam funcionar melhor, e o resultado não cobre todas as proposições do tema, só as que a API encontra pela palavra.
- O Senado não foi incluído; a estrutura permite adicionar um módulo parecido usando `https://legis.senado.leg.br/dadosabertos/`.

---

## 2. Rastreador de Voos (OpenSky Network)

Script: `2_voos/rastreador_voos.py`.

```bash
cd 2_voos

# Desafio 1: mapa interativo com todas as aeronaves sobre o Brasil
python rastreador_voos.py mapa
python rastreador_voos.py mapa --categoria helicopteros     # só helicópteros
python rastreador_voos.py mapa --categoria comerciais       # só comerciais

# Desafio 2: trajetória de uma aeronave pelo ICAO24 (últimas horas)
python rastreador_voos.py trajetoria --icao24 e48c3a --horas 6

# Extra: chegadas e partidas de um aeroporto (exige login)
python rastreador_voos.py aeroporto --icao SBGR
```

O mapa é salvo como HTML (`mapa_voos_brasil.html`, `trajetoria.html`); é só abrir no navegador.

**Como funciona**

- **Bounding box:** a consulta `/states/all` recebe `lamin`, `lamax`, `lomin` e `lomax`, que desenham um retângulo em volta do Brasil.
- **Filtro por categoria:** o mapa tem um controle de camadas no canto superior direito para ligar e desligar cada grupo (comerciais, helicópteros, leves, outros, sem informação). O parâmetro `--categoria` filtra já na geração.
- **Trajetória:** usa `/tracks/all`. Os pontos são desenhados em segmentos coloridos pela altitude, com marcadores de início e de último ponto.

**Autenticação (opcional)**

O acesso anônimo funciona, mas com limite pequeno de créditos. Com uma conta OpenSky, crie um *API client* nas configurações da conta e exporte:

```bash
export OPENSKY_CLIENT_ID="seu_client_id"
export OPENSKY_CLIENT_SECRET="seu_client_secret"
```

O script troca as credenciais por um token OAuth2 automaticamente.

**Cuidados**

- Muitas aeronaves chegam sem categoria informada (códigos 0 ou 1), então o grupo "Sem categoria" costuma ser grande.
- `/tracks/all` é um endpoint experimental: pode falhar, devolver poucos pontos, ou exigir login dependendo da sua conta. Para voos já encerrados, passe `--tempo` com um timestamp Unix de qualquer instante durante o voo.
- Chegadas e partidas são processadas em lote pela OpenSky, por isso o script consulta o dia anterior (UTC).
- Se aparecer erro de limite (429), espere o tempo indicado na mensagem.

---

## 3. Dashboard de Exploração Espacial (NASA)

Script: `3_nasa/dashboard_nasa.py` (Streamlit).

```bash
cd 3_nasa
export NASA_API_KEY="sua_chave"     # Windows (PowerShell): $env:NASA_API_KEY="sua_chave"
streamlit run dashboard_nasa.py
```

Pegue a chave gratuita em <https://api.nasa.gov>. Sem chave, o app usa `DEMO_KEY`, que tem uma cota bem baixa e esgota rápido. Você também pode colar a chave direto na barra lateral do app.

**Abas**

1. **Asteroides (Desafio 1):** escolha um intervalo de datas e veja a dispersão distância x velocidade, com o tamanho da bolha representando o diâmetro e a cor indicando se o asteroide é potencialmente perigoso. Há opção de escala logarítmica e um histograma de tamanhos.
2. **Imagem do dia (Desafio 2a):** APOD com título, data e explicação. Trata o caso de o conteúdo do dia ser um vídeo.
3. **Exoplanetas (Desafio 2b):** barras com os métodos de descoberta, consultados via TAP do NASA Exoplanet Archive. A escala logarítmica vem ligada porque o método de trânsito domina.
4. **Eventos na Terra (Desafio 2c):** mapa dos eventos do EONET nas últimas 48h, colorido por categoria.

**Detalhes técnicos**

- O endpoint `/neo/rest/v1/feed` aceita no máximo 7 dias por chamada, então o código divide o intervalo em blocos de 7 dias e junta os resultados. O limite no app é de 30 dias.
- Os resultados ficam em cache (`st.cache_data`) para não gastar a cota da API em cada interação.
- O JSON do NeoWs é bem aninhado (`near_earth_objects` → data → asteroide → `close_approach_data`), e os valores numéricos chegam como texto, por isso há conversão com `float()`.
- O Exoplanet Archive e o EONET não exigem chave.

---

## Estrutura

```
.
├── README.md
├── requirements.txt
├── 1_legislativo/dashboard_legislativo.py
├── 2_voos/rastreador_voos.py
└── 3_nasa/dashboard_nasa.py
```
