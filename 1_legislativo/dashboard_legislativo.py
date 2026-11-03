#!/usr/bin/env python3
"""
Dashboard de Atividade Legislativa - Câmara dos Deputados

Usa a API de Dados Abertos da Câmara (https://dadosabertos.camara.leg.br/api/v2)
para montar quatro análises:

  ranking    -> deputados que mais apresentaram proposições sobre um tema (barras horizontais)
  atividade  -> proposições e votações por mês ao longo de um ano (linhas)
  heatmap    -> participação em votações por partido x mês (mapa de calor)
  despesas   -> gastos (cota parlamentar) de um deputado, por tipo e por mês

Exemplos:
  python dashboard_legislativo.py ranking --tema saude --ano 2025
  python dashboard_legislativo.py atividade --ano 2025 --tema educacao
  python dashboard_legislativo.py heatmap --ano 2025
  python dashboard_legislativo.py despesas --deputado "Tabata Amaral" --ano 2025
"""
import argparse
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import calendar

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

BASE_URL = "https://dadosabertos.camara.leg.br/api/v2"
PASTA_SAIDA = "saida"
MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

# Erros que podem acontecer numa chamada à API (rede, HTTP, ou nosso próprio RuntimeError)
ERROS_API = (RuntimeError, requests.RequestException)

sessao = requests.Session()
sessao.headers.update({
    "Accept": "application/json",
    "User-Agent": "estudo-dashboard-legislativo/1.0",
})

# A API da Câmara é aberta e não exige chave. Mas, se você tiver um token (ou passar
# por algum proxy que exija), basta definir CAMARA_TOKEN no ambiente.
if os.getenv("CAMARA_TOKEN"):
    sessao.headers["Authorization"] = f"Bearer {os.environ['CAMARA_TOKEN']}"


# --------------------------------------------------------------------------
# Funções de acesso à API
# --------------------------------------------------------------------------

def pegar_json(url, params=None, tentativas=4):
    """GET com algumas tentativas: a API às vezes responde 429/5xx quando está sobrecarregada."""
    ultimo_erro = None
    for t in range(tentativas):
        try:
            resp = sessao.get(url, params=params, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** t)  # espera 1s, 2s, 4s, 8s...
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as erro:
            ultimo_erro = erro
            time.sleep(2 ** t)
    raise RuntimeError(f"Falha ao acessar {url}: {ultimo_erro or 'muitas tentativas'}")


def buscar_todas_paginas(caminho, params=None, max_paginas=None):
    """
    Percorre a paginação da API. Cada resposta traz uma lista "links" e, quando
    existe mais página, um link com rel="next" que já vem com todos os parâmetros.
    """
    params = dict(params or {})
    params.setdefault("itens", 100)   # máximo permitido por página
    params.setdefault("pagina", 1)
    url = f"{BASE_URL}{caminho}"
    resultados = []
    paginas = 0

    while url:
        dados = pegar_json(url, params)
        resultados.extend(dados.get("dados", []))
        paginas += 1
        if max_paginas and paginas >= max_paginas:
            break
        url = next((l["href"] for l in dados.get("links", []) if l.get("rel") == "next"), None)
        params = None  # o link "next" já carrega a query string completa

    return resultados


def id_da_uri(uri):
    """As URIs vêm como .../deputados/204554 -> pegamos só o número final."""
    return int(uri.rstrip("/").split("/")[-1])


def autores_deputados(id_proposicao, apenas_principal=False):
    """
    Dado -> relacional: proposição -> autores. Um projeto pode ter autores que não são
    deputados (senadores, comissões, Executivo), então filtramos pela URI.
    """
    try:
        autores = pegar_json(f"{BASE_URL}/proposicoes/{id_proposicao}/autores").get("dados", [])
    except ERROS_API:
        return []  # se falhar para uma proposição, seguimos sem ela

    deputados = [(id_da_uri(a["uri"]), a["nome"]) for a in autores
                 if "/deputados/" in (a.get("uri") or "")]
    if apenas_principal and deputados:
        # o autor principal é o primeiro na ordem de assinatura
        ordenados = sorted([a for a in autores if "/deputados/" in (a.get("uri") or "")],
                           key=lambda a: a.get("ordemAssinatura", 999))
        return [(id_da_uri(ordenados[0]["uri"]), ordenados[0]["nome"])]
    return deputados


def partido_e_uf(id_deputado):
    """Busca partido/UF atual do deputado (usado só para enfeitar o gráfico)."""
    try:
        status = pegar_json(f"{BASE_URL}/deputados/{id_deputado}")["dados"]["ultimoStatus"]
        return f"{status.get('siglaPartido', '?')}-{status.get('siglaUf', '?')}"
    except (KeyError, *ERROS_API):
        return "?"


def meses_do_ano(ano):
    """Lista (mes, data_inicio, data_fim) do ano, ignorando meses que ainda não chegaram."""
    hoje = date.today()
    saida = []
    for mes in range(1, 13):
        if ano == hoje.year and mes > hoje.month:
            break
        if ano > hoje.year:
            break
        ultimo_dia = calendar.monthrange(ano, mes)[1]
        saida.append((mes, f"{ano}-{mes:02d}-01", f"{ano}-{mes:02d}-{ultimo_dia:02d}"))
    return saida


# --------------------------------------------------------------------------
# Funções de gráfico
# --------------------------------------------------------------------------

def finalizar_figura(nome_arquivo, mostrar):
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    caminho = os.path.join(PASTA_SAIDA, nome_arquivo)
    plt.tight_layout()
    plt.savefig(caminho, dpi=150)
    print(f"Gráfico salvo em {caminho}")
    if mostrar:
        plt.show()
    plt.close()


def grafico_barras_horizontais(rotulos, valores, titulo, xlabel, arquivo, mostrar):
    fig, ax = plt.subplots(figsize=(10, 0.45 * len(rotulos) + 2))
    posicoes = np.arange(len(rotulos))
    barras = ax.barh(posicoes, valores, color="#2a6f97")
    ax.set_yticks(posicoes)
    ax.set_yticklabels(rotulos)
    ax.invert_yaxis()  # o maior fica no topo
    ax.set_xlabel(xlabel)
    ax.set_title(titulo, fontsize=13, weight="bold")
    ax.bar_label(barras, padding=3)  # escreve o valor na ponta de cada barra
    ax.spines[["top", "right"]].set_visible(False)
    finalizar_figura(arquivo, mostrar)


# --------------------------------------------------------------------------
# Comando 1: ranking de proposições por tema
# --------------------------------------------------------------------------

def cmd_ranking(args):
    tipos = [t.strip().upper() for t in args.tipos.split(",")]
    max_paginas = -(-args.max_proposicoes // 100)  # divisão arredondando pra cima

    print(f"Buscando proposições de {args.ano} com o tema '{args.tema}' ({', '.join(tipos)})...")
    props = buscar_todas_paginas(
        "/proposicoes",
        {"ano": args.ano, "keywords": args.tema, "siglaTipo": ",".join(tipos)},
        max_paginas=max_paginas,
    )[:args.max_proposicoes]
    print(f"{len(props)} proposições encontradas. Consultando autores (pode levar um tempinho)...")

    # Uma chamada por proposição -> usamos threads, mas poucas, pra não irritar a API
    contagem = Counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futuros = [pool.submit(autores_deputados, p["id"], args.apenas_principal) for p in props]
        for i, futuro in enumerate(as_completed(futuros), 1):
            for dep_id, nome in futuro.result():
                contagem[(dep_id, nome)] += 1
            if i % 50 == 0:
                print(f"  ...{i}/{len(props)} proposições processadas")

    if not contagem:
        print("Nenhum deputado encontrado para esse filtro. Tente outro tema ou ano.")
        return

    top = contagem.most_common(args.top)
    rotulos = [f"{nome} ({partido_e_uf(dep_id)})" for (dep_id, nome), _ in top]
    valores = [qtd for _, qtd in top]

    tabela = pd.DataFrame({"deputado": rotulos, "proposicoes": valores})
    print("\n", tabela.to_string(index=False))
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    tabela.to_csv(os.path.join(PASTA_SAIDA, f"ranking_{args.tema}_{args.ano}.csv"), index=False)

    grafico_barras_horizontais(
        rotulos, valores,
        titulo=f"Deputados que mais apresentaram proposições sobre '{args.tema}' em {args.ano}",
        xlabel="Nº de proposições",
        arquivo=f"ranking_{args.tema}_{args.ano}.png",
        mostrar=args.mostrar,
    )


# --------------------------------------------------------------------------
# Comando 2: atividade mensal (proposições e votações)
# --------------------------------------------------------------------------

def cmd_atividade(args):
    tipos = [t.strip().upper() for t in args.tipos.split(",")]
    meses = meses_do_ano(args.ano)
    props_mes, votacoes_mes = [], []

    for mes, inicio, fim in meses:
        print(f"Mês {mes:02d}/{args.ano}...")
        filtro_props = {"dataApresentacaoInicio": inicio, "dataApresentacaoFim": fim,
                        "siglaTipo": ",".join(tipos)}
        if args.tema:
            filtro_props["keywords"] = args.tema
        # limite de 30 páginas (3000 itens) por mês, só pra não ficar infinito
        props_mes.append(len(buscar_todas_paginas("/proposicoes", filtro_props, max_paginas=30)))
        votacoes_mes.append(len(buscar_todas_paginas(
            "/votacoes", {"dataInicio": inicio, "dataFim": fim}, max_paginas=30)))

    nomes = [MESES[m - 1] for m, _, _ in meses]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    sufixo_tema = f" (tema: {args.tema})" if args.tema else ""
    ax1.plot(nomes, props_mes, marker="o", color="#2a6f97")
    ax1.set_title(f"Proposições apresentadas por mês em {args.ano}{sufixo_tema}")
    ax1.set_ylabel("Proposições")
    ax2.plot(nomes, votacoes_mes, marker="s", color="#c1121f")
    ax2.set_title(f"Votações realizadas por mês em {args.ano}")
    ax2.set_ylabel("Votações")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
        ax.spines[["top", "right"]].set_visible(False)
    finalizar_figura(f"atividade_{args.ano}.png", args.mostrar)


# --------------------------------------------------------------------------
# Comando 3: heatmap de participação em votações por partido x mês
# --------------------------------------------------------------------------

def votos_por_partido(id_votacao):
    """Conta quantos votos cada partido registrou numa votação (só votações nominais têm votos)."""
    try:
        votos = buscar_todas_paginas(f"/votacoes/{id_votacao}/votos")
    except ERROS_API:
        return Counter()
    contagem = Counter()
    for voto in votos:
        # JSON aninhado: o partido está dentro de "deputado_"
        partido = (voto.get("deputado_") or {}).get("siglaPartido")
        if partido:
            contagem[partido] += 1
    return contagem


def cmd_heatmap(args):
    meses = meses_do_ano(args.ano)
    por_mes = {}       # mes -> Counter de votos por partido
    nominais_mes = {}  # mes -> quantas votações nominais entraram na conta

    for mes, inicio, fim in meses:
        votacoes = buscar_todas_paginas(
            "/votacoes", {"dataInicio": inicio, "dataFim": fim,
                          "ordenarPor": "dataHoraRegistro", "ordem": "ASC"}, max_paginas=30)
        # Pegamos uma amostra espalhada pelo mês (em vez das N primeiras), senão
        # o gráfico só refletiria o começo do mês.
        passo = max(1, len(votacoes) // args.votacoes_por_mes)
        amostra = votacoes[::passo][:args.votacoes_por_mes]
        print(f"{MESES[mes-1]}/{args.ano}: {len(votacoes)} votações, analisando {len(amostra)}...")

        total = Counter()
        nominais = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for resultado in pool.map(votos_por_partido, [v["id"] for v in amostra]):
                if resultado:  # vazio = votação simbólica, sem voto individual
                    nominais += 1
                    total.update(resultado)
        por_mes[mes] = total
        nominais_mes[mes] = nominais

    # Monta a matriz partido x mês. Valor = média de votos do partido por votação nominal,
    # assim meses em que analisamos mais/menos votações continuam comparáveis.
    todos_partidos = Counter()
    for c in por_mes.values():
        todos_partidos.update(c)
    partidos = [p for p, _ in todos_partidos.most_common(args.partidos)]

    matriz = np.full((len(partidos), len(meses)), np.nan)
    for j, (mes, _, _) in enumerate(meses):
        if nominais_mes[mes] == 0:
            continue  # mês sem votação nominal fica vazio (cinza)
        for i, p in enumerate(partidos):
            matriz[i, j] = por_mes[mes][p] / nominais_mes[mes]

    if np.all(np.isnan(matriz)):
        print("Nenhuma votação nominal encontrada no período.")
        return

    fig, ax = plt.subplots(figsize=(11, 0.45 * len(partidos) + 2.5))
    cmap = plt.get_cmap("YlOrRd").copy()
    cmap.set_bad("#e0e0e0")  # NaN em cinza
    img = ax.imshow(matriz, cmap=cmap, aspect="auto")
    ax.set_yticks(range(len(partidos)))
    ax.set_yticklabels(partidos)
    ax.set_xticks(range(len(meses)))
    ax.set_xticklabels([f"{MESES[m-1]}\n(n={nominais_mes[m]})" for m, _, _ in meses])
    for i in range(len(partidos)):
        for j in range(len(meses)):
            if not np.isnan(matriz[i, j]):
                ax.text(j, i, f"{matriz[i, j]:.0f}", ha="center", va="center", fontsize=8)
    fig.colorbar(img, ax=ax, label="Votos médios por votação nominal")
    ax.set_title(f"Participação em votações por partido e mês - {args.ano}\n"
                 "(n = votações nominais analisadas no mês)", fontsize=12, weight="bold")

    # Resumo: mês mais e menos ativo considerando todos os partidos juntos
    atividade = {m: sum(por_mes[m].values()) / nominais_mes[m]
                 for m, _, _ in meses if nominais_mes[m] > 0}
    mais = max(atividade, key=atividade.get)
    menos = min(atividade, key=atividade.get)
    print(f"\nMês de maior participação: {MESES[mais-1]} ({atividade[mais]:.0f} votos/votação)")
    print(f"Mês de menor participação: {MESES[menos-1]} ({atividade[menos]:.0f} votos/votação)")

    finalizar_figura(f"heatmap_votacoes_{args.ano}.png", args.mostrar)


# --------------------------------------------------------------------------
# Comando 4: despesas de um deputado
# --------------------------------------------------------------------------

def cmd_despesas(args):
    achados = pegar_json(f"{BASE_URL}/deputados", {"nome": args.deputado, "itens": 10})["dados"]
    if not achados:
        print(f"Nenhum deputado encontrado com o nome '{args.deputado}'.")
        return
    dep = achados[0]
    if len(achados) > 1:
        outros = ", ".join(d["nome"] for d in achados[1:])
        print(f"Mais de um resultado; usando {dep['nome']}. Outros: {outros}")

    print(f"Buscando despesas de {dep['nome']} em {args.ano}...")
    despesas = buscar_todas_paginas(f"/deputados/{dep['id']}/despesas",
                                    {"ano": args.ano, "ordenarPor": "mes", "ordem": "ASC"},
                                    max_paginas=100)
    if not despesas:
        print("Sem despesas registradas nesse ano.")
        return

    df = pd.DataFrame(despesas)
    por_tipo = df.groupby("tipoDespesa")["valorLiquido"].sum().sort_values(ascending=False).head(8)
    por_mes = df.groupby("mes")["valorLiquido"].sum().reindex(range(1, 13), fill_value=0)
    print(f"Total gasto: R$ {df['valorLiquido'].sum():,.2f}")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    # nomes de despesa são longos; quebramos em 30 caracteres
    ax1.barh([t[:30] for t in por_tipo.index], por_tipo.values, color="#2a6f97")
    ax1.invert_yaxis()
    ax1.set_title("Maiores tipos de despesa (R$)")
    ax2.bar([MESES[m - 1] for m in por_mes.index], por_mes.values, color="#c1121f")
    ax2.set_title("Gasto mensal (R$)")
    fig.suptitle(f"Cota parlamentar de {dep['nome']} - {args.ano}", weight="bold")
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
    finalizar_figura(f"despesas_{dep['id']}_{args.ano}.png", args.mostrar)


# --------------------------------------------------------------------------
# Linha de comando
# --------------------------------------------------------------------------

def montar_parser():
    ano_atual = datetime.now().year
    parser = argparse.ArgumentParser(description="Dashboard de atividade legislativa (Câmara dos Deputados)")
    parser.add_argument("--mostrar", action="store_true", help="abre o gráfico numa janela além de salvar o PNG")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("ranking", help="ranking de deputados por proposições sobre um tema")
    p.add_argument("--tema", required=True, help='palavra-chave, ex.: "saude", "educacao"')
    p.add_argument("--ano", type=int, default=ano_atual)
    p.add_argument("--tipos", default="PL,PLP,PEC", help="tipos de proposição, separados por vírgula")
    p.add_argument("--top", type=int, default=15, help="quantos deputados mostrar")
    p.add_argument("--max-proposicoes", type=int, default=500,
                   help="limite de proposições analisadas (cada uma gera 1 chamada extra)")
    p.add_argument("--apenas-principal", action="store_true",
                   help="conta só o primeiro autor de cada proposição")
    p.set_defaults(func=cmd_ranking)

    p = sub.add_parser("atividade", help="proposições e votações por mês")
    p.add_argument("--ano", type=int, default=ano_atual)
    p.add_argument("--tema", help="filtra as proposições por palavra-chave (opcional)")
    p.add_argument("--tipos", default="PL,PLP,PEC")
    p.set_defaults(func=cmd_atividade)

    p = sub.add_parser("heatmap", help="heatmap de participação em votações por partido x mês")
    p.add_argument("--ano", type=int, default=ano_atual)
    p.add_argument("--votacoes-por-mes", type=int, default=20,
                   help="quantas votações analisar por mês (cada uma gera chamadas extras)")
    p.add_argument("--partidos", type=int, default=15, help="quantos partidos mostrar")
    p.set_defaults(func=cmd_heatmap)

    p = sub.add_parser("despesas", help="despesas de um deputado")
    p.add_argument("--deputado", required=True, help="nome (ou parte do nome)")
    p.add_argument("--ano", type=int, default=ano_atual)
    p.set_defaults(func=cmd_despesas)

    return parser


if __name__ == "__main__":
    argumentos = montar_parser().parse_args()
    argumentos.func(argumentos)
