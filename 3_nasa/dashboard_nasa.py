"""
Dashboard de Exploração Espacial (NASA Open APIs) - feito com Streamlit

Abas:
  1) Asteroides (NeoWs)  -> dispersão distância x velocidade num intervalo de datas
  2) APOD                -> imagem astronômica do dia
  3) Exoplanetas         -> barras com os métodos de descoberta (NASA Exoplanet Archive)
  4) Eventos naturais    -> mapa dos eventos da Terra nas últimas 48h (EONET)

Como rodar:
  export NASA_API_KEY="sua_chave"      # opcional; sem ela usamos a DEMO_KEY (bem limitada)
  streamlit run dashboard_nasa.py

A chave gratuita sai em https://api.nasa.gov. O Exoplanet Archive e o EONET não pedem chave.
"""
import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="Dashboard Espacial NASA", page_icon="🪐", layout="wide")

URL_NEO = "https://api.nasa.gov/neo/rest/v1/feed"
URL_APOD = "https://api.nasa.gov/planetary/apod"
URL_EXOPLANETAS = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
URL_EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"

LIMITE_DIAS = 30  # o feed do NeoWs aceita 7 dias por chamada; para 30 dias fazemos ~5 chamadas


# --------------------------------------------------------------------------
# Funções de acesso aos dados (com cache para não gastar a cota da API à toa)
# --------------------------------------------------------------------------

def get_json(url, params):
    """GET simples, com mensagem clara quando a cota da chave estoura."""
    resp = requests.get(url, params=params, timeout=40)
    if resp.status_code == 429:
        raise RuntimeError("Limite de requisições da chave atingido. Use sua própria chave "
                           "(a DEMO_KEY permite pouquíssimas chamadas por hora).")
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=3600, show_spinner="Consultando asteroides (NeoWs)...")
def buscar_asteroides(inicio: date, fim: date, chave: str) -> pd.DataFrame:
    """
    O endpoint /feed devolve no máximo 7 dias por vez, então quebramos o intervalo
    em blocos de 7 dias e juntamos tudo num DataFrame.
    O JSON é aninhado: near_earth_objects -> {data: [asteroides]} -> close_approach_data[0] -> ...
    """
    linhas = []
    cursor = inicio
    while cursor <= fim:
        fim_bloco = min(cursor + timedelta(days=6), fim)
        dados = get_json(URL_NEO, {"start_date": cursor.isoformat(),
                                   "end_date": fim_bloco.isoformat(), "api_key": chave})
        for dia, asteroides in dados["near_earth_objects"].items():
            for a in asteroides:
                aproximacao = a["close_approach_data"][0]
                diametro = a["estimated_diameter"]["meters"]
                linhas.append({
                    "nome": a["name"].strip("()"),
                    "data": dia,
                    "distancia_km": float(aproximacao["miss_distance"]["kilometers"]),
                    "distancia_lunar": float(aproximacao["miss_distance"]["lunar"]),
                    "velocidade_km_s": float(aproximacao["relative_velocity"]["kilometers_per_second"]),
                    "diametro_m": (diametro["estimated_diameter_min"] + diametro["estimated_diameter_max"]) / 2,
                    "perigoso": "Potencialmente perigoso" if a["is_potentially_hazardous_asteroid"] else "Não classificado como perigoso",
                    "url": a["nasa_jpl_url"],
                })
        cursor = fim_bloco + timedelta(days=1)
    return pd.DataFrame(linhas)


@st.cache_data(ttl=3600, show_spinner="Buscando a imagem do dia...")
def buscar_apod(chave: str) -> dict:
    return get_json(URL_APOD, {"api_key": chave})


@st.cache_data(ttl=86400, show_spinner="Consultando o Exoplanet Archive...")
def buscar_metodos_exoplanetas() -> pd.DataFrame:
    # A tabela pscomppars tem um registro por planeta confirmado.
    # O TAP aceita SQL (ADQL), então o agrupamento já vem pronto do servidor.
    consulta = ("select discoverymethod, count(*) as total from pscomppars "
                "group by discoverymethod order by total desc")
    dados = get_json(URL_EXOPLANETAS, {"query": consulta, "format": "json"})
    return pd.DataFrame(dados).rename(columns={"discoverymethod": "metodo"})


@st.cache_data(ttl=900, show_spinner="Buscando eventos naturais (EONET)...")
def buscar_eventos() -> pd.DataFrame:
    dados = get_json(URL_EONET, {"days": 2, "status": "all"})
    linhas = []
    for evento in dados.get("events", []):
        categoria = evento["categories"][0]["title"] if evento.get("categories") else "Outros"
        for geo in evento.get("geometry", []):
            coords = geo["coordinates"]
            if geo["type"] == "Polygon":
                coords = coords[0][0]  # polígono: usamos o primeiro vértice como ponto de referência
            elif geo["type"] != "Point":
                continue
            linhas.append({
                "titulo": evento["title"], "categoria": categoria,
                "data": geo["date"][:16].replace("T", " "),
                "lon": coords[0], "lat": coords[1],
                "link": evento.get("link", ""),
            })
    return pd.DataFrame(linhas)


# --------------------------------------------------------------------------
# Barra lateral: chave da API
# --------------------------------------------------------------------------

st.title("🪐 Dashboard de Exploração Espacial")
st.caption("Dados abertos da NASA: NeoWs, APOD, Exoplanet Archive e EONET")

with st.sidebar:
    st.header("Configuração")
    chave = st.text_input("Chave da NASA API", value=os.getenv("NASA_API_KEY", "DEMO_KEY"), type="password")
    st.caption("Sem chave própria, a DEMO_KEY esgota rápido. Pegue a sua em api.nasa.gov.")

aba_neo, aba_apod, aba_exo, aba_eventos = st.tabs(
    ["☄️ Asteroides", "🌌 Imagem do dia", "🔭 Exoplanetas", "🌍 Eventos na Terra"])


# --------------------------------------------------------------------------
# Aba 1: asteroides
# --------------------------------------------------------------------------
with aba_neo:
    st.subheader("Distância x velocidade dos asteroides próximos")
    hoje = date.today()
    intervalo = st.date_input("Intervalo de datas", value=(hoje - timedelta(days=6), hoje),
                              max_value=hoje + timedelta(days=30))

    if not (isinstance(intervalo, tuple) and len(intervalo) == 2):
        st.info("Selecione a data inicial e a final no calendário.")
    else:
        inicio, fim = intervalo
        if (fim - inicio).days + 1 > LIMITE_DIAS:
            st.warning(f"Intervalo máximo de {LIMITE_DIAS} dias (para poupar a cota da API).")
        else:
            try:
                df = buscar_asteroides(inicio, fim, chave)
            except (RuntimeError, requests.RequestException) as erro:
                st.error(f"Não consegui buscar os asteroides: {erro}")
                df = pd.DataFrame()

            if df.empty:
                st.info("Nenhum asteroide no período.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Asteroides no período", len(df))
                c2.metric("Potencialmente perigosos", int((df["perigoso"] == "Potencialmente perigoso").sum()))
                mais_proximo = df.loc[df["distancia_km"].idxmin()]
                c3.metric("Mais próximo", f"{mais_proximo['distancia_lunar']:.1f} distâncias lunares")

                escala_log = st.checkbox("Escala logarítmica na distância", value=True)
                fig = px.scatter(
                    df, x="distancia_km", y="velocidade_km_s", color="perigoso",
                    size="diametro_m", size_max=30, hover_name="nome",
                    hover_data={"data": True, "distancia_lunar": ":.1f", "diametro_m": ":.0f"},
                    log_x=escala_log,
                    labels={"distancia_km": "Distância da Terra (km)",
                            "velocidade_km_s": "Velocidade relativa (km/s)",
                            "perigoso": "Classificação", "diametro_m": "Diâmetro (m)"},
                    color_discrete_map={"Potencialmente perigoso": "#d62728",
                                        "Não classificado como perigoso": "#1f77b4"},
                )
                fig.update_layout(legend_title_text="")
                st.plotly_chart(fig, use_container_width=True)
                st.caption("O tamanho de cada bolha representa o diâmetro estimado do asteroide.")

                fig2 = px.histogram(df, x="diametro_m", nbins=30, log_x=True,
                                    labels={"diametro_m": "Diâmetro estimado (m, escala log)"},
                                    title="Distribuição de tamanhos")
                st.plotly_chart(fig2, use_container_width=True)

                with st.expander("Ver tabela de dados"):
                    st.dataframe(df.drop(columns=["distancia_lunar"]), use_container_width=True)


# --------------------------------------------------------------------------
# Aba 2: APOD
# --------------------------------------------------------------------------
with aba_apod:
    try:
        apod = buscar_apod(chave)
        st.subheader(apod["title"])
        st.caption(f"{apod['date']}" + (f" - © {apod['copyright'].strip()}" if apod.get("copyright") else ""))
        if apod.get("media_type") == "image":
            st.image(apod.get("hdurl") or apod["url"], use_container_width=True)
        else:
            # às vezes o APOD do dia é um vídeo (YouTube, por exemplo)
            st.video(apod["url"])
        st.write(apod["explanation"])  # texto original da NASA, em inglês
    except (RuntimeError, requests.RequestException) as erro:
        st.error(f"Não consegui carregar o APOD: {erro}")


# --------------------------------------------------------------------------
# Aba 3: exoplanetas
# --------------------------------------------------------------------------
with aba_exo:
    try:
        metodos = buscar_metodos_exoplanetas()
        st.subheader(f"{int(metodos['total'].sum()):,} exoplanetas confirmados".replace(",", "."))
        usar_log = st.checkbox("Escala logarítmica", value=True, key="log_exo",
                               help="Trânsito domina tanto que os outros métodos somem na escala normal.")
        fig = px.bar(metodos, x="total", y="metodo", orientation="h", text="total",
                     log_x=usar_log,
                     labels={"total": "Planetas descobertos", "metodo": "Método de descoberta"})
        fig.update_yaxes(categoryorder="total ascending")
        st.plotly_chart(fig, use_container_width=True)
    except (RuntimeError, requests.RequestException) as erro:
        st.error(f"Não consegui consultar o Exoplanet Archive: {erro}")


# --------------------------------------------------------------------------
# Aba 4: eventos naturais (EONET)
# --------------------------------------------------------------------------
with aba_eventos:
    try:
        eventos = buscar_eventos()
        if eventos.empty:
            st.info("Nenhum evento registrado nas últimas 48 horas.")
        else:
            st.subheader(f"{eventos['titulo'].nunique()} eventos nas últimas 48 horas")
            fig = px.scatter_geo(eventos, lat="lat", lon="lon", color="categoria",
                                 hover_name="titulo", hover_data={"data": True, "lat": False, "lon": False},
                                 projection="natural earth")
            fig.update_traces(marker=dict(size=9))
            fig.update_layout(height=550, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)
            with st.expander("Ver lista de eventos"):
                st.dataframe(eventos[["titulo", "categoria", "data", "link"]], use_container_width=True)
    except (RuntimeError, requests.RequestException) as erro:
        st.error(f"Não consegui consultar o EONET: {erro}")
