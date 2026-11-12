#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coletor de licitações abertas no PNCP (Portal Nacional de Contratações Públicas).

O que faz, a cada execução:
  1. Busca TODAS as contratações com recebimento de propostas aberto no estado configurado
     (API pública de consultas do PNCP, sem cadastro nem senha).
  2. Classifica cada uma pelos ramos/palavras-chave de config.json.
  3. Para as licitações relevantes, guarda os arquivos do edital e a lista de itens (API pública do PNCP).
  4. Lê outras fontes públicas (SEST SENAT, FINDES/SENAI/SESI, SENAC ES) - ver fontes_extra.py.
  5. Gera docs/index.html (página para o celular, com os dados embutidos) e docs/dados.json.
  6. Opcional: avisa no celular (app ntfy) sobre licitações relevantes NOVAS.

Só usa a biblioteca padrão do Python (3.9 ou mais novo).

Uso:   python coletor.py
       python coletor.py --config config.json --saida docs --sem-notificar
"""
import argparse
import http.client
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

try:
    import fontes_extra
except Exception as _e:                      # sem o arquivo fontes_extra.py o app continua só com o PNCP
    fontes_extra = None

BRT = timezone(timedelta(hours=-3))
BASE_URL = os.environ.get("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta").rstrip("/")
ENDPOINT = "/v1/contratacoes/proposta"
API_PNCP = os.environ.get("PNCP_API_URL", "https://pncp.gov.br/api/pncp/v1").rstrip("/")   # arquivos e itens de cada contratação
RE_ID_PNCP = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")
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

ESPERA = float(os.environ.get("COLETOR_ESPERA", "3"))                 # segundos entre tentativas (dobra a cada vez)
PAUSA_REPETICAO = float(os.environ.get("COLETOR_PAUSA_REPETICAO", "20"))


def http_get_json(url, tentativas=5, espera=None):
    espera = ESPERA if espera is None else espera
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
            if e.code == 404:
                return None
            if e.code in (401, 403):
                raise FalhaDeAcesso(
                    "HTTP %d: o PNCP recusou o acesso. Se estiver rodando fora do Brasil/em nuvem, "
                    "tente rodar a partir de um computador no Brasil." % e.code)
            ultimo_erro = "HTTP %d" % e.code
        except (OSError, http.client.HTTPException, ValueError) as e:   # rede, leitura cortada, JSON inválido
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

    registros, faltaram = {}, []

    def guardar(resp):
        for r in (resp or {}).get("data") or []:
            chave = r.get("numeroControlePNCP")
            if chave:
                registros[chave] = r

    total_paginas = (resposta or {}).get("totalPaginas") or 0
    guardar(resposta)
    log("  página 1/%d (%d registros até agora)" % (max(total_paginas, 1), len(registros)))
    for pagina in range(2, min(total_paginas, 400) + 1):
        try:
            resp = pedir(pagina, tamanho)
        except FalhaDeAcesso as e:
            if "recusou o acesso" in str(e):
                raise
            resp = None
        if resp is None:
            log("  página %d/%d: o PNCP não respondeu (tento de novo no final)" % (pagina, total_paginas))
            faltaram.append(pagina)
        else:
            guardar(resp)
            log("  página %d/%d (%d registros até agora)" % (pagina, total_paginas, len(registros)))
        time.sleep(0.3)
    if faltaram:                                       # segunda chance, depois de uma pausa
        log("  repetindo %d página(s) que falharam..." % len(faltaram))
        time.sleep(PAUSA_REPETICAO)
        for pagina in list(faltaram):
            try:
                resp = pedir(pagina, tamanho)
            except FalhaDeAcesso:
                resp = None
            if resp is not None:
                guardar(resp)
                faltaram.remove(pagina)
                log("  página %d/%d recuperada (%d registros até agora)" % (pagina, total_paginas, len(registros)))
            time.sleep(0.5)
    buscar_abertas.faltaram = faltaram
    buscar_abertas.total_paginas = total_paginas
    return list(registros.values())


buscar_abertas.faltaram = []
buscar_abertas.total_paginas = 0


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


# ----------------------------------------------------------------------------- arquivos do edital e itens (PNCP)

def _num(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return int(v) if v == int(v) else round(v, 4)


def enriquecer_pncp(itens, previo, log=print, limite=250, orcamento_s=480):
    """Para as licitações relevantes, guarda os arquivos (edital, anexos) e os itens de cada contratação.
    Falhas aqui nunca derrubam a atualização: o cartão simplesmente fica sem esses detalhes."""
    inicio, ok_n, falhas_seguidas = time.time(), 0, 0
    agora = agora_brt()
    alvo = sorted((i for i in itens if i.get("ramos") and not i.get("fonte")), key=lambda i: -(i.get("score") or 0))[:limite]
    for it in alvo:
        m = RE_ID_PNCP.match(it.get("id") or "")
        if not m:
            continue
        ant = (previo or {}).get(it["id"]) or {}
        quando = parse_data(ant.get("enr"))
        if quando and "arqs" in ant and (agora - quando) < timedelta(hours=6):          # leitura recente: reaproveita
            for k in ("arqs", "itens", "nitens", "enr"):
                if k in ant:
                    it[k] = ant[k]
            continue
        if time.time() - inicio > orcamento_s or falhas_seguidas >= 6:
            log("  (detalhes: parei por tempo/falhas; o restante fica para a próxima atualização)")
            break
        base = "%s/orgaos/%s/compras/%s/%d" % (API_PNCP, m.group(1), m.group(3), int(m.group(2)))
        try:
            arq = http_get_json(base + "/arquivos", tentativas=2, espera=1) or []
            try:
                its = http_get_json(base + "/itens?pagina=1&tamanhoPagina=100", tentativas=2, espera=1)
            except ParametroInvalido:
                its = http_get_json(base + "/itens", tentativas=2, espera=1)
            if isinstance(its, dict):
                its = its.get("data") or its.get("itens") or []
            arq = [a for a in arq if isinstance(a, dict) and a.get("url") and a.get("statusAtivo", True)]
            arq.sort(key=lambda a: (a.get("tipoDocumentoId") != 2, a.get("sequencialDocumento") or 0))   # edital primeiro
            it["arqs"] = [[limpar(a.get("titulo"), 90) or "Documento", limpar(a.get("tipoDocumentoNome")), a["url"]] for a in arq[:12]]
            its = [x for x in (its or []) if isinstance(x, dict)]
            it["nitens"] = len(its)
            it["itens"] = [[x.get("numeroItem"), limpar(x.get("descricao"), 220), _num(x.get("quantidade")),
                            limpar(x.get("unidadeMedida")), _num(x.get("valorUnitarioEstimado"))] for x in its[:40]]
            it["enr"] = agora.isoformat(timespec="seconds")
            ok_n += 1
            falhas_seguidas = 0
        except Exception as e:                                                       # noqa: BLE001
            falhas_seguidas += 1
            for k in ("arqs", "itens", "nitens", "enr"):                              # mantém o que já tínhamos
                if k in ant:
                    it[k] = ant[k]
            log("  detalhes de %s: %s" % (it["id"], str(e)[:100]))
        time.sleep(0.15)
    log("Detalhes (edital e itens) atualizados em %d licitações relevantes." % ok_n)


# ----------------------------------------------------------------------------- outras fontes (SEST SENAT, FINDES, SENAC ES)

def carregar_status_previo(caminho):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f).get("fontes_status") or {}
    except (OSError, ValueError):
        return {}


def montar_itens_extra(brutos, cfg, previo, status_prev):
    ramos, excluir = compilar(cfg)
    minimo = cfg.get("pontuacao_minima", 2)
    agora = agora_brt()
    itens = []
    for it in brutos:
        achados = classificar(it.get("objeto") or "", ramos, excluir, minimo)
        it["ramos"] = sorted(achados, key=lambda n: -achados[n][0])
        it["termos"] = sorted({t for _, ts in achados.values() for t in ts})
        it["score"] = max([p for p, _ in achados.values()] or [0])
        primeira_leitura = previo is None or not (status_prev.get(it.get("fonte")) or {}).get("ok")
        if primeira_leitura:                          # primeira vez que a fonte é lida: nada vira "nova" nem notifica
            it["visto"] = it.get("pub") or agora.isoformat(timespec="seconds")
        else:
            it["visto"] = (previo.get(it["id"]) or {}).get("visto") or agora.isoformat(timespec="seconds")
        itens.append(it)
    return itens


def manter_itens_da_ultima_leitura(previo, status, ja_lidos):
    """Se um portal falhou agora, continua mostrando o que ele tinha na última leitura (enquanto o prazo não passar)."""
    ids = {i["id"] for i in ja_lidos}
    agora, mantidos = agora_brt(), []
    for nome, st in status.items():
        if st.get("ok"):
            continue
        for i in (previo or {}).values():
            fecha = parse_data(i.get("fecha"))
            if i.get("fonte") == nome and i["id"] not in ids and fecha and fecha >= agora:
                mantidos.append(i)
        if any(i.get("fonte") == nome for i in mantidos):
            st["aviso"] = (st.get("aviso") or "falhou") + " – mostrando a última leitura que deu certo"
            st["n"] = sum(1 for i in mantidos if i.get("fonte") == nome)
    return mantidos


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
                    "message": "\n".join("• %s – %s" % (i["ramos"][0], i["mun"] or i.get("fonte") or "") for i in novos[:12]),
                    "tags": ["bell"]})
        else:
            for i in novos:
                fecha = parse_data(i["fecha"])
                quando = fecha.strftime("%d/%m %H:%M") if fecha else "prazo não informado"
                enviar({"topic": topico, "title": "%s – %s" % (i["ramos"][0], i["mun"] or i.get("fonte") or ""),
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
    ap.add_argument("--sem-extras", action="store_true", help="não lê SEST SENAT, FINDES e SENAC ES")
    ap.add_argument("--sem-detalhes", action="store_true", help="não busca arquivos do edital e itens no PNCP")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    os.makedirs(args.saida, exist_ok=True)
    caminho_json = os.path.join(args.saida, "dados.json")
    previo = carregar_previo(caminho_json)

    try:
        sys.stdout.reconfigure(line_buffering=True)      # mensagens aparecem na ordem certa no log do GitHub
    except (AttributeError, ValueError):
        pass
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
    if not args.sem_detalhes:
        enriquecer_pncp(itens, previo)
    aviso_pncp = None
    faltaram = getattr(buscar_abertas, "faltaram", [])
    if faltaram:                                         # não perde o que estava só nas páginas que falharam
        ids = {i["id"] for i in itens}
        agora_ = agora_brt()
        mantidos = [i for i in (previo or {}).values()
                    if not i.get("fonte") and i["id"] not in ids and parse_data(i.get("fecha")) and parse_data(i.get("fecha")) >= agora_]
        itens += mantidos
        aviso_pncp = ("o PNCP não respondeu em %d de %d página(s); %d licitações vieram da leitura anterior"
                      % (len(faltaram), getattr(buscar_abertas, "total_paginas", 0), len(mantidos)))
        print("AVISO: " + aviso_pncp)
    n_pncp = len(itens)

    fontes_status = {}
    if fontes_extra and not args.sem_extras:
        brutos, fontes_status = fontes_extra.coletar_todas(cfg)
        extras = montar_itens_extra(brutos, cfg, previo, carregar_status_previo(caminho_json))
        extras += manter_itens_da_ultima_leitura(previo, fontes_status, extras)
        itens += extras
    fontes_status["PNCP"] = {"nome": "PNCP", "ok": True, "n": n_pncp, "em": agora_brt().isoformat(timespec="seconds")}
    if aviso_pncp:
        fontes_status["PNCP"]["aviso"] = aviso_pncp

    relevantes = [i for i in itens if i["ramos"]]
    dados = {
        "gerado_em": agora_brt().isoformat(timespec="seconds"),
        "uf": cfg["uf"],
        "ramos": list(cfg["ramos"].keys()),
        "total_abertas": len(itens),
        "total_relevantes": len(relevantes),
        "fontes_status": fontes_status,
        "itens": itens,
    }
    with open(caminho_json, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.saida, "index.html"), "w", encoding="utf-8") as f:
        f.write(gerar_html(args.modelo, dados))

    print("Concluído: %d abertas (%d descartadas por revogação/prazo), %d relevantes para os seus ramos."
          % (len(itens), descartadas, len(relevantes)))
    for nome, st in fontes_status.items():
        print("  fonte %-24s %s" % (nome, ("%d abertas" % st.get("n", 0)) if st.get("ok") else "FALHOU: " + str(st.get("aviso"))))
    for ramo in dados["ramos"]:
        print("  %-34s %d" % (ramo, sum(1 for i in relevantes if ramo in i["ramos"])))

    topico = os.environ.get("NTFY_TOPIC", "").strip()
    if topico and previo is not None and not args.sem_notificar:
        novos = [i for i in relevantes if i["id"] not in previo and i["visto"] != i["pub"]]
        if novos:
            notificar_ntfy(novos, topico, os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
