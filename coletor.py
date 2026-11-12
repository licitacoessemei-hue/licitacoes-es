#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coletor de licitações abertas no PNCP (Portal Nacional de Contratações Públicas).

O que faz, a cada execução:
  1. Busca TODAS as contratações com recebimento de propostas aberto no estado configurado
     (API pública de consultas do PNCP, sem cadastro nem senha).
  2. Classifica cada uma pelos ramos/palavras-chave de config.json.
  3. Gera docs/index.html (página para o celular, com os dados embutidos) e docs/dados.json.
  4. Opcional: avisa no celular (app ntfy) sobre licitações relevantes NOVAS.

Só usa a biblioteca padrão do Python (3.9 ou mais novo).

Uso:   python coletor.py
       python coletor.py --config config.json --saida docs --sem-notificar
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
BASE_URL = os.environ.get("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta").rstrip("/")
ENDPOINT = "/v1/contratacoes/proposta"
TAMANHOS_DE_PAGINA = (500, 100, 50, 10)   # a API recusa tamanhos acima do seu limite; testamos do maior ao menor
USER_AGENT = "Mozilla/5.0 (compatible; coletor-licitacoes/1.0)"
PESO_FORTE, PESO_FRACO = 3, 1
SITUACOES_DESCARTADAS = ("revogad", "anulad")


class ParametroInvalido(Exception):
    pass


class FalhaDeAcesso(Exception):
    pass


# ----------------------------------------------------------------------------- utilidades

def norm(texto):
    """minúsculas, sem acento, espaços simples."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower()).strip()


def limpar(texto, limite=None):
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if limite and len(texto) > limite:
        texto = texto[: limite - 1].rstrip() + "…"
    return texto


def agora_brt():
    return datetime.now(BRT)


def parse_data(texto):
    """A API devolve horário de Brasília sem fuso ('2026-11-11T23:59:00')."""
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto[:19]).replace(tzinfo=BRT)
    except ValueError:
        return None


# ----------------------------------------------------------------------------- acesso à API

def http_get_json(url, tentativas=4, espera=3):
    ultimo_erro = None
    for i in range(tentativas):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                corpo = resp.read()
            if not corpo.strip():          # 204 sem conteúdo = nenhum resultado
                return None
            return json.loads(corpo.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400:
                raise ParametroInvalido(url)
            if e.code in (401, 403):
                raise FalhaDeAcesso(
                    "HTTP %d: o PNCP recusou o acesso. Se estiver rodando fora do Brasil/em nuvem, "
                    "tente rodar a partir de um computador no Brasil." % e.code)
            ultimo_erro = "HTTP %d" % e.code
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            ultimo_erro = repr(e)
        if i < tentativas - 1:
            time.sleep(espera * (2 ** i))
    raise FalhaDeAcesso("Falha ao consultar o PNCP (%s)" % ultimo_erro)


def buscar_abertas(uf, dias_a_frente, log=print):
    """Todas as contratações do estado com propostas abertas (encerramento até hoje + dias_a_frente)."""
    data_final = (agora_brt() + timedelta(days=dias_a_frente)).strftime("%Y%m%d")

    def pedir(pagina, tamanho):
        q = urllib.parse.urlencode({"dataFinal": data_final, "uf": uf, "pagina": pagina, "tamanhoPagina": tamanho})
        return http_get_json("%s%s?%s" % (BASE_URL, ENDPOINT, q))

    tamanho, resposta = None, None
    for t in TAMANHOS_DE_PAGINA:
        try:
            resposta = pedir(1, t)
            tamanho = t
            break
        except ParametroInvalido:
            continue
    if tamanho is None:
        raise FalhaDeAcesso("A API recusou todos os tamanhos de página testados.")

    registros, pagina = {}, 1
    while True:
        for r in (resposta or {}).get("data") or []:
            chave = r.get("numeroControlePNCP")
            if chave:
                registros[chave] = r
        total_paginas = (resposta or {}).get("totalPaginas") or 0
        log("  página %d/%d (%d registros até agora)" % (pagina, max(total_paginas, 1), len(registros)))
        if pagina >= total_paginas or pagina >= 400:
            break
        pagina += 1
        resposta = pedir(pagina, tamanho)
        if resposta is None:
            raise FalhaDeAcesso("Resposta vazia inesperada na página %d; abortando para não gravar dados incompletos." % pagina)
        time.sleep(0.3)
    return list(registros.values())


# ----------------------------------------------------------------------------- classificação

def _padrao(termo):
    t = norm(termo)
    fim = r"(?![a-z0-9])" if len(t) <= 4 else ""      # termos curtos: só palavra inteira
    return re.compile(r"(?<![a-z0-9])" + re.escape(t) + fim)


def compilar(cfg):
    ramos = []
    for nome, d in cfg["ramos"].items():
        termos = [(kw, PESO_FORTE, _padrao(kw)) for kw in d.get("fortes", [])]
        termos += [(kw, PESO_FRACO, _padrao(kw)) for kw in d.get("fracas", [])]
        ramos.append((nome, termos))
    excluir = [_padrao(x) for x in cfg.get("excluir", [])]
    return ramos, excluir


def classificar(texto, ramos, excluir, minimo):
    """Devolve {ramo: (pontos, [termos encontrados])} para os ramos que atingiram a pontuação mínima."""
    t = norm(texto)
    achados, fortes = {}, set()
    for nome, termos in ramos:
        pontos, usados, forte = 0, [], False
        for kw, peso, pad in termos:
            if pad.search(t):
                pontos += peso
                usados.append(kw)
                forte = forte or peso == PESO_FORTE
        if pontos >= minimo:
            achados[nome] = (pontos, usados)
            if forte:
                fortes.add(nome)
    if fortes:                       # havendo termo forte em algum ramo, ramos só com termos fracos são ruído
        achados = {n: v for n, v in achados.items() if n in fortes}
    elif achados and any(p.search(t) for p in excluir):
        return {}
    return achados


# ----------------------------------------------------------------------------- montagem dos dados

def simplificar(r):
    org, uni = r.get("orgaoEntidade") or {}, r.get("unidadeOrgao") or {}
    cnpj, ano, seq = org.get("cnpj"), r.get("anoCompra"), r.get("sequencialCompra")
    url = "https://pncp.gov.br/app/editais/%s/%s/%s" % (cnpj, ano, seq) if cnpj and ano and seq else ""
    numero = limpar(str(r.get("numeroCompra") or ""))
    return {
        "id": r.get("numeroControlePNCP"),
        "objeto": limpar(r.get("objetoCompra"), 1500),
        "orgao": limpar(org.get("razaoSocial")),
        "unidade": limpar(uni.get("nomeUnidade")),
        "mun": limpar(uni.get("municipioNome")),
        "esfera": org.get("esferaId") or "",
        "mod": limpar(r.get("modalidadeNome")),
        "sit": limpar(r.get("situacaoCompraNome")),
        "valor": r.get("valorTotalEstimado"),
        "srp": bool(r.get("srp")),
        "num": ("%s/%s" % (numero, ano)) if numero and ano else numero,
        "abre": r.get("dataAberturaProposta") or "",
        "fecha": r.get("dataEncerramentoProposta") or "",
        "pub": r.get("dataPublicacaoPncp") or "",
        "origem": limpar(r.get("usuarioNome")),
        "link": limpar(r.get("linkSistemaOrigem")),
        "url": url,
    }


def carregar_previo(caminho):
    try:
        with open(caminho, encoding="utf-8") as f:
            d = json.load(f)
        return {i["id"]: i for i in d.get("itens", []) if i.get("id")}
    except (OSError, ValueError):
        return None


def montar_itens(registros, cfg, previo):
    ramos, excluir = compilar(cfg)
    minimo = cfg.get("pontuacao_minima", 2)
    agora = agora_brt()
    itens, descartadas = [], 0
    for r in registros:
        sit = norm(r.get("situacaoCompraNome"))
        fecha = parse_data(r.get("dataEncerramentoProposta"))
        if any(s in sit for s in SITUACOES_DESCARTADAS) or (fecha and fecha < agora):
            descartadas += 1
            continue
        it = simplificar(r)
        achados = classificar("%s %s" % (r.get("objetoCompra") or "", r.get("informacaoComplementar") or ""),
                              ramos, excluir, minimo)
        it["ramos"] = sorted(achados, key=lambda n: -achados[n][0])
        it["termos"] = sorted({t for _, ts in achados.values() for t in ts})
        it["score"] = max([p for p, _ in achados.values()] or [0])
        if previo is None:                       # 1ª execução: usa a data de publicação (evita tudo aparecer como "nova")
            it["visto"] = it["pub"] or agora.isoformat(timespec="seconds")
        else:
            it["visto"] = (previo.get(it["id"]) or {}).get("visto") or agora.isoformat(timespec="seconds")
        itens.append(it)
    return itens, descartadas


# ----------------------------------------------------------------------------- notificação (opcional)

def _brl(v):
    if v is None or v <= 1:
        return "valor não informado"
    return ("R$ %s" % format(v, ",.2f")).replace(",", "X").replace(".", ",").replace("X", ".")


def notificar_ntfy(novos, topico, servidor, log=print):
    def enviar(payload):
        req = urllib.request.Request(servidor, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=30).read()

    try:
        if len(novos) > 5:
            enviar({"topic": topico, "title": "%d novas licitações no ES" % len(novos),
                    "message": "\n".join("• %s – %s" % (i["ramos"][0], i["mun"]) for i in novos[:12]),
                    "tags": ["bell"]})
        else:
            for i in novos:
                fecha = parse_data(i["fecha"])
                quando = fecha.strftime("%d/%m %H:%M") if fecha else "prazo não informado"
                enviar({"topic": topico, "title": "%s – %s" % (i["ramos"][0], i["mun"]),
                        "message": "%s\nEncerra: %s · %s" % (limpar(i["objeto"], 180), quando, _brl(i["valor"])),
                        "click": i["url"] or None, "tags": ["bell"]})
        log("Notificação enviada (%d nova(s))." % len(novos))
    except Exception as e:                          # aviso falhar não pode derrubar a atualização
        log("Aviso: não consegui enviar a notificação (%r)." % (e,))


# ----------------------------------------------------------------------------- saída

def gerar_html(caminho_modelo, dados):
    with open(caminho_modelo, encoding="utf-8") as f:
        modelo = f.read()
    blob = (json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
            .replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return modelo.replace("__DADOS_JSON__", blob)


def main():
    aqui = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Coleta licitações abertas no PNCP e gera a página do celular.")
    ap.add_argument("--config", default=os.path.join(aqui, "config.json"))
    ap.add_argument("--saida", default=os.path.join(aqui, "docs"))
    ap.add_argument("--modelo", default=os.path.join(aqui, "modelo.html"))
    ap.add_argument("--sem-notificar", action="store_true")
    ap.add_argument("--aceitar-vazio", action="store_true", help="grava mesmo que a API não devolva nenhuma licitação")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    os.makedirs(args.saida, exist_ok=True)
    caminho_json = os.path.join(args.saida, "dados.json")
    previo = carregar_previo(caminho_json)

    print("Buscando licitações abertas (%s) no PNCP..." % cfg["uf"])
    try:
        registros = buscar_abertas(cfg["uf"], cfg.get("dias_a_frente", 365))
    except FalhaDeAcesso as e:
        print("ERRO: %s" % e, file=sys.stderr)
        return 1
    if not registros and not args.aceitar_vazio:
        print("ERRO: a API não devolveu nenhuma licitação; mantendo os dados anteriores.", file=sys.stderr)
        return 2

    itens, descartadas = montar_itens(registros, cfg, previo)
    relevantes = [i for i in itens if i["ramos"]]
    dados = {
        "gerado_em": agora_brt().isoformat(timespec="seconds"),
        "uf": cfg["uf"],
        "ramos": list(cfg["ramos"].keys()),
        "total_abertas": len(itens),
        "total_relevantes": len(relevantes),
        "itens": itens,
    }
    with open(caminho_json, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.saida, "index.html"), "w", encoding="utf-8") as f:
        f.write(gerar_html(args.modelo, dados))

    print("Concluído: %d abertas (%d descartadas por revogação/prazo), %d relevantes para os seus ramos."
          % (len(itens), descartadas, len(relevantes)))
    for ramo in dados["ramos"]:
        print("  %-34s %d" % (ramo, sum(1 for i in relevantes if ramo in i["ramos"])))

    topico = os.environ.get("NTFY_TOPIC", "").strip()
    if topico and previo is not None and not args.sem_notificar:
        novos = [i for i in relevantes if i["id"] not in previo]
        if novos:
            notificar_ntfy(novos, topico, os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
