#!/usr/bin/env python3
"""
Rastreador de Voos com dados abertos da OpenSky Network

Três comandos:
  mapa        -> mapa interativo (HTML) com as aeronaves sobrevoando o Brasil agora,
                 com filtro por categoria (comerciais, helicópteros, leves...)
  trajetoria  -> dado o ICAO24 de uma aeronave, plota no mapa o trajeto das últimas horas
  aeroporto   -> chegadas e partidas de um aeroporto (código ICAO, ex.: SBGR)

Exemplos:
  python rastreador_voos.py mapa
  python rastreador_voos.py mapa --categoria helicopteros
  python rastreador_voos.py trajetoria --icao24 e48c3a --horas 6
  python rastreador_voos.py aeroporto --icao SBGR

Autenticação (opcional):
  O uso anônimo funciona para vetores de estado, mas com limite baixo de créditos.
  Se você tem conta na OpenSky, crie um "API client" no painel da conta e exporte:
      OPENSKY_CLIENT_ID=...   OPENSKY_CLIENT_SECRET=...
  Os endpoints de chegadas/partidas exigem login.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import branca.colormap as cm
import folium
import requests

BASE = "https://opensky-network.org/api"
URL_TOKEN = ("https://auth.opensky-network.org/auth/realms/opensky-network"
             "/protocol/openid-connect/token")

# Caixa geográfica (bounding box) que cobre o Brasil com uma folguinha
BRASIL = {"lamin": -33.75, "lamax": 5.30, "lomin": -74.00, "lomax": -34.70}

# Grupos de categoria -> (códigos de categoria da OpenSky, cor, descrição).
# Códigos: 2 leve, 3 pequeno, 4 grande, 5 grande/vórtice alto, 6 pesado,
# 7 alto desempenho, 8 helicóptero, 9 planador, 10 balão, 11 paraquedista,
# 12 ultraleve, 14 drone. 0 e 1 significam "sem informação".
GRUPOS = {
    "comerciais":   ({3, 4, 5, 6}, "#1f77b4", "Comerciais (pequeno a pesado)"),
    "helicopteros": ({8},          "#d62728", "Helicópteros"),
    "leves":        ({2, 12},      "#2ca02c", "Leves e ultraleves"),
    "outros":       ({7, 9, 10, 11, 14, 15}, "#9467bd", "Outros (planador, balão, drone...)"),
    "sem_info":     ({0, 1},       "#7f7f7f", "Sem categoria informada"),
}


# --------------------------------------------------------------------------
# Acesso à API
# --------------------------------------------------------------------------

_cache_token = {"valor": None, "expira": 0}


def cabecalhos_auth():
    """
    Se as variáveis de ambiente existirem, pega um token OAuth2 (client credentials)
    e devolve o cabeçalho Authorization. Sem credenciais, segue como anônimo.
    """
    client_id = os.getenv("OPENSKY_CLIENT_ID")
    segredo = os.getenv("OPENSKY_CLIENT_SECRET")
    if not (client_id and segredo):
        return {}

    if time.time() > _cache_token["expira"] - 30:  # renova um pouco antes de expirar
        resp = requests.post(URL_TOKEN, data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": segredo,
        }, timeout=30)
        resp.raise_for_status()
        corpo = resp.json()
        _cache_token["valor"] = corpo["access_token"]
        _cache_token["expira"] = time.time() + corpo.get("expires_in", 300)

    return {"Authorization": f"Bearer {_cache_token['valor']}"}


def chamar_api(caminho, params=None):
    """GET na OpenSky com mensagens de erro mais amigáveis. Devolve None em caso de 404."""
    resp = requests.get(f"{BASE}{caminho}", params=params,
                        headers=cabecalhos_auth(), timeout=40)
    if resp.status_code == 429:
        espera = resp.headers.get("X-Rate-Limit-Retry-After-Seconds", "?")
        sys.exit(f"Limite de requisições atingido. Tente de novo em {espera}s "
                 "(ou use credenciais para ter um limite maior).")
    if resp.status_code in (401, 403):
        sys.exit("Esse endpoint exige autenticação. Defina OPENSKY_CLIENT_ID e "
                 "OPENSKY_CLIENT_SECRET (veja o README).")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def buscar_aeronaves():
    """
    /states/all devolve cada aeronave como uma LISTA posicional (não um dicionário).
    Aqui convertemos para dict para o resto do código ficar legível.
    """
    dados = chamar_api("/states/all", {**BRASIL, "extended": 1})
    aeronaves = []
    for s in (dados or {}).get("states") or []:
        if s[5] is None or s[6] is None:
            continue  # sem posição, não dá pra plotar
        aeronaves.append({
            "icao24": s[0],
            "callsign": (s[1] or "").strip() or "sem indicativo",
            "pais": s[2],
            "lon": s[5],
            "lat": s[6],
            "altitude": s[13] if s[13] is not None else s[7],  # geométrica, senão barométrica
            "no_solo": s[8],
            "velocidade": s[9],      # m/s
            "rumo": s[10] or 0,      # graus, 0 = norte
            "razao_subida": s[11],   # m/s
            "categoria": s[17] if len(s) > 17 and s[17] is not None else 0,
        })
    return aeronaves, (dados or {}).get("time")


def grupo_da_categoria(categoria):
    for nome, (codigos, _, _) in GRUPOS.items():
        if categoria in codigos:
            return nome
    return "sem_info"  # categorias estranhas (veículos de solo, obstáculos) caem aqui


# --------------------------------------------------------------------------
# Comando: mapa de aeronaves
# --------------------------------------------------------------------------

def cmd_mapa(args):
    print("Consultando aeronaves sobre o Brasil...")
    aeronaves, instante = buscar_aeronaves()
    if not args.incluir_solo:
        aeronaves = [a for a in aeronaves if not a["no_solo"]]

    if args.categoria != "todas":
        aeronaves = [a for a in aeronaves if grupo_da_categoria(a["categoria"]) == args.categoria]

    if not aeronaves:
        print("Nenhuma aeronave retornada (a API pode estar limitando ou o filtro ficou restrito demais).")
        return

    mapa = folium.Map(location=[-14.2, -51.9], zoom_start=4, tiles="CartoDB positron")

    # Um FeatureGroup por categoria: o controle de camadas do canto superior direito
    # funciona como filtro interativo dentro do próprio mapa.
    grupos_mapa = {nome: folium.FeatureGroup(name=nome, show=True) for nome in GRUPOS}
    contagem = {nome: 0 for nome in GRUPOS}

    for a in aeronaves:
        nome_grupo = grupo_da_categoria(a["categoria"])
        _, cor, _ = GRUPOS[nome_grupo]
        contagem[nome_grupo] += 1

        vel_kmh = f"{a['velocidade'] * 3.6:.0f} km/h" if a["velocidade"] is not None else "?"
        alt = f"{a['altitude']:.0f} m" if a["altitude"] is not None else "?"
        popup = (f"<b>{a['callsign']}</b><br>ICAO24: {a['icao24']}<br>"
                 f"País de registro: {a['pais']}<br>Altitude: {alt}<br>"
                 f"Velocidade: {vel_kmh}<br>Rumo: {a['rumo']:.0f}°")

        # Triângulo girado conforme o rumo da aeronave (0° = norte)
        icone = folium.DivIcon(
            icon_size=(16, 16), icon_anchor=(8, 8),
            html=f'<div style="transform: rotate({a["rumo"]}deg); color:{cor}; '
                 f'font-size:15px; line-height:16px; text-align:center;">▲</div>',
        )
        folium.Marker([a["lat"], a["lon"]], icon=icone, tooltip=a["callsign"],
                      popup=folium.Popup(popup, max_width=250)).add_to(grupos_mapa[nome_grupo])

    for nome, grupo in grupos_mapa.items():
        grupo.layer_name = f"{GRUPOS[nome][2]} ({contagem[nome]})"
        grupo.add_to(mapa)
    folium.LayerControl(collapsed=False).add_to(mapa)

    hora = datetime.fromtimestamp(instante, timezone.utc).strftime("%d/%m/%Y %H:%M UTC") if instante else "?"
    caixa = (f'<div style="position:fixed; bottom:20px; left:20px; z-index:9999; background:white; '
             f'padding:8px 12px; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.3); '
             f'font-family:sans-serif; font-size:13px;">'
             f'<b>{len(aeronaves)} aeronaves</b> em voo<br>Dados de {hora}</div>')
    mapa.get_root().html.add_child(folium.Element(caixa))

    mapa.save(args.saida)
    print(f"{len(aeronaves)} aeronaves plotadas.")
    for nome, qtd in contagem.items():
        print(f"  {GRUPOS[nome][2]}: {qtd}")
    print(f"Mapa salvo em {args.saida} (abra no navegador)")


# --------------------------------------------------------------------------
# Comando: trajetória de uma aeronave
# --------------------------------------------------------------------------

def cmd_trajetoria(args):
    icao24 = args.icao24.lower().strip()
    print(f"Buscando trajetória de {icao24}...")
    # time=0 pede a trajetória do voo em andamento; com um timestamp qualquer
    # durante um voo passado, a API devolve aquele voo.
    dados = chamar_api("/tracks/all", {"icao24": icao24, "time": args.tempo})
    if not dados or not dados.get("path"):
        sys.exit("Nenhuma trajetória encontrada. A aeronave pode não estar em voo agora "
                 "(use --tempo com um timestamp Unix de um voo passado).")

    corte = time.time() - args.horas * 3600 if args.tempo == 0 else 0
    # cada ponto: [tempo, lat, lon, altitude_baro, rumo, no_solo]
    pontos = [p for p in dados["path"] if p[1] is not None and p[2] is not None and p[0] >= corte]
    if len(pontos) < 2:
        sys.exit(f"Poucos pontos nas últimas {args.horas}h. Tente aumentar --horas.")

    altitudes = [p[3] or 0 for p in pontos]
    escala = cm.LinearColormap(["#2b83ba", "#abdda4", "#ffffbf", "#fdae61", "#d7191c"],
                               vmin=min(altitudes), vmax=max(max(altitudes), min(altitudes) + 1),
                               caption="Altitude (m)")

    mapa = folium.Map(location=[pontos[0][1], pontos[0][2]], zoom_start=6, tiles="CartoDB positron")
    # Desenhamos segmento a segmento para colorir pela altitude
    for ant, atual in zip(pontos, pontos[1:]):
        folium.PolyLine([(ant[1], ant[2]), (atual[1], atual[2])],
                        color=escala(atual[3] or 0), weight=4, opacity=0.9).add_to(mapa)
    escala.add_to(mapa)

    def hora(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m %H:%M UTC")

    folium.Marker((pontos[0][1], pontos[0][2]), tooltip=f"Início: {hora(pontos[0][0])}",
                  icon=folium.Icon(color="green", icon="play")).add_to(mapa)
    folium.Marker((pontos[-1][1], pontos[-1][2]), tooltip=f"Último ponto: {hora(pontos[-1][0])}",
                  icon=folium.Icon(color="red", icon="stop")).add_to(mapa)

    mapa.fit_bounds([(p[1], p[2]) for p in pontos])
    mapa.save(args.saida)
    callsign = (dados.get("callsign") or "").strip() or "sem indicativo"
    print(f"{len(pontos)} pontos de {callsign}. Mapa salvo em {args.saida}")


# --------------------------------------------------------------------------
# Comando: chegadas e partidas
# --------------------------------------------------------------------------

def cmd_aeroporto(args):
    # Esses dados são processados em lote, então o dia de ontem (UTC) é o mais confiável.
    ontem = datetime.now(timezone.utc).date() - timedelta(days=1)
    inicio = datetime.combine(ontem, datetime.min.time(), tzinfo=timezone.utc)
    fim = inicio + timedelta(hours=args.horas)
    params = {"airport": args.icao.upper(), "begin": int(inicio.timestamp()), "end": int(fim.timestamp())}

    for rotulo, caminho, campo in (("Chegadas", "/flights/arrival", "estDepartureAirport"),
                                   ("Partidas", "/flights/departure", "estArrivalAirport")):
        voos = chamar_api(caminho, params) or []
        print(f"\n{rotulo} em {args.icao.upper()} ({inicio:%d/%m %H:%M} a {fim:%H:%M} UTC): {len(voos)} voos")
        print(f"{'Indicativo':<10} {'ICAO24':<8} {'Outro aeroporto':<16} {'Horário (UTC)'}")
        for v in voos[:args.limite]:
            ts = v["lastSeen"] if rotulo == "Chegadas" else v["firstSeen"]
            print(f"{(v.get('callsign') or '').strip():<10} {v['icao24']:<8} "
                  f"{(v.get(campo) or '?'):<16} {datetime.fromtimestamp(ts, timezone.utc):%H:%M}")


# --------------------------------------------------------------------------
# Linha de comando
# --------------------------------------------------------------------------

def montar_parser():
    parser = argparse.ArgumentParser(description="Rastreador de voos (OpenSky Network)")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("mapa", help="mapa interativo das aeronaves sobre o Brasil")
    p.add_argument("--categoria", choices=["todas", *GRUPOS], default="todas",
                   help="mostra só um grupo de aeronaves")
    p.add_argument("--incluir-solo", action="store_true", help="inclui aeronaves em solo")
    p.add_argument("--saida", default="mapa_voos_brasil.html")
    p.set_defaults(func=cmd_mapa)

    p = sub.add_parser("trajetoria", help="trajetória de uma aeronave pelo ICAO24")
    p.add_argument("--icao24", required=True, help="código hexadecimal, ex.: e48c3a")
    p.add_argument("--horas", type=float, default=6, help="janela de tempo (só para voos em andamento)")
    p.add_argument("--tempo", type=int, default=0, help="timestamp Unix de um voo passado (0 = voo atual)")
    p.add_argument("--saida", default="trajetoria.html")
    p.set_defaults(func=cmd_trajetoria)

    p = sub.add_parser("aeroporto", help="chegadas e partidas de um aeroporto (exige login)")
    p.add_argument("--icao", required=True, help="código ICAO do aeroporto, ex.: SBGR")
    p.add_argument("--horas", type=int, default=12, help="janela a partir de 00:00 UTC de ontem (máx. 48)")
    p.add_argument("--limite", type=int, default=20, help="quantos voos listar")
    p.set_defaults(func=cmd_aeroporto)

    return parser


if __name__ == "__main__":
    argumentos = montar_parser().parse_args()
    argumentos.func(argumentos)
