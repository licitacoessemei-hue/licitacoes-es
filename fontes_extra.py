#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fontes além do PNCP, lidas das páginas públicas (sem login e sem CAPTCHA):

  * SEST SENAT  (compras.sestsenat.org.br)  - portal "Paradigma", precisa de navegador (JavaScript)
  * FINDES / SENAI / SESI (portaldecompras.findes.org.br) - mesmo portal "Paradigma"
  * SENAC ES    (es.senac.br/licitacoes)    - página HTML simples
  * Portal de Compras Públicas (portaldecompraspublicas.com.br) - lista de processos do ES, precisa de navegador

Cada leitor devolve (itens, status). Se um portal mudar ou ficar fora do ar, só ele falha:
o resto do aplicativo continua funcionando e o motivo aparece na aba "Fontes".
Os itens usam o mesmo formato do coletor.py, com o campo extra "fonte".
"""
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

BRT = timezone(timedelta(hours=-3))
USER_AGENT = "Mozilla/5.0 (compatible; coletor-licitacoes/2.0; leitura publica, 1 acesso por execucao)"
# Os portais Paradigma desenham tabelas diferentes conforme o navegador que os abre; o navegador
# automático se apresenta como um Chrome comum de computador para receber a mesma tabela que a pessoa vê.
USER_AGENT_NAVEGADOR = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36")
UFS = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())

FONTES = {
    "sestsenat": {"nome": "SEST SENAT", "tipo": "paradigma",
                  "url": "https://compras.sestsenat.org.br/portal/Mural.aspx", "filtrar_uf": True},
    "findes": {"nome": "FINDES / SENAI / SESI", "tipo": "paradigma",
               "url": "https://portaldecompras.findes.org.br/portal/Mural.aspx", "filtrar_uf": False},
    "senac_es": {"nome": "SENAC ES", "tipo": "senac",
                 "url": "https://es.senac.br/licitacoes"},
    # 100132 é o número que o próprio portal usa para o Espírito Santo no filtro "UF"
    "pcp": {"nome": "Portal de Compras Públicas", "tipo": "pcp",
            "url": "https://www.portaldecompraspublicas.com.br/processos?pagina=1&uf=100132"},
}


for _k, _f in FONTES.items():                  # (usado só nos testes) COLETOR_URL_SESTSENAT=... troca o endereço
    _f["url"] = os.environ.get("COLETOR_URL_" + _k.upper(), _f["url"])


def _limpar(t, limite=None):
    t = re.sub(r"\s+", " ", t or "").strip()
    if limite and len(t) > limite:
        t = t[: limite - 1].rstrip() + "…"
    return t


def _norm(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "")
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)).lower()).strip()


# ----------------------------------------------------------------------------- datas

_RE_DATA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:[\s,T]+(?:às\s+)?(\d{1,2})\s*(?:[:h]\s*(\d{2})?)(?::\d{2})?\s*(AM|PM)?)?", re.I)


def parse_data_hora(texto, hora_padrao=(23, 59)):
    """Aceita '11/13/2026 8:01 PM' (formato americano, com AM/PM) e '13/11/2026 20:01' / '13/11/2026, 9h'.
    Devolve 'AAAA-MM-DDTHH:MM:SS' (horário de Brasília) ou ''."""
    m = _RE_DATA.search(texto or "")
    if not m:
        return ""
    a, b, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
    h = int(m.group(4)) if m.group(4) is not None else None
    mi = int(m.group(5)) if m.group(5) else 0
    ampm = (m.group(6) or "").upper()
    if ampm:
        mes, dia = a, b                      # formato americano
    elif a > 12:
        dia, mes = a, b
    elif b > 12:
        mes, dia = a, b
    else:
        dia, mes = a, b                      # dúvida: padrão brasileiro
    if h is None:
        h, mi = hora_padrao
    elif ampm:
        h = (h % 12) + (12 if ampm == "PM" else 0)
    try:
        return datetime(ano, mes, dia, h, mi).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ""


def _futuro(iso):
    try:
        return datetime.fromisoformat(iso).replace(tzinfo=BRT) >= datetime.now(BRT)
    except ValueError:
        return False


def _ufs_no_texto(texto):
    achados = re.findall(r"(?:/|\s-\s|,\s)\s*([A-Z]{2})\b", texto or "")
    return {u for u in achados if u in UFS}


# ----------------------------------------------------------------------------- navegador (portais Paradigma)

def garantir_playwright(log=print):
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        pass
    if os.environ.get("GITHUB_ACTIONS") != "true" and not os.environ.get("COLETOR_INSTALAR_NAVEGADOR"):
        log("  (biblioteca 'playwright' não instalada; portais que exigem JavaScript foram pulados)")
        return False
    log("  instalando a biblioteca do navegador (só a primeira vez em cada execução)...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "playwright"], capture_output=True, text=True)
    if r.returncode != 0:
        log("  não consegui instalar o playwright: " + (r.stderr or r.stdout)[-300:])
        return False
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _abrir_navegador(p, log=print):
    tentativas = [{"channel": "chrome"}, {"executable_path": "/usr/bin/google-chrome"},
                  {"executable_path": "/usr/bin/chromium"}, {}]
    for rodada in (1, 2):
        erros = []
        for kw in tentativas:
            try:
                return p.chromium.launch(headless=True, args=["--no-sandbox"], **kw)
            except Exception as e:                                   # noqa: BLE001
                erros.append(str(e).splitlines()[0][:120])
        if rodada == 1 and os.environ.get("GITHUB_ACTIONS") == "true":
            log("  baixando o Chromium do Playwright...")
            subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                           capture_output=True, text=True)
    raise RuntimeError("nenhum navegador disponível (%s)" % "; ".join(erros))


_JS_TABELAS = """() => [...document.querySelectorAll('table')].map(t => ({
    cab: [...t.querySelectorAll('th')].map(x => (x.innerText || '').trim()),
    linhas: [...t.querySelectorAll('tbody tr')].map(tr =>
        [...tr.querySelectorAll('td')].map(td => (td.innerText || '').trim()))
}))"""

_JS_PRONTO = """() => [...document.querySelectorAll('table tbody tr')].some(tr => tr.querySelectorAll('td').length >= 6)"""

_SEL_PROXIMA = ["a.paginate_button.next:not(.disabled)", "li.next:not(.disabled) a", "a[aria-label='Next']:not(.disabled)",
                "a:has-text('Próxima')", "a:has-text('Próximo')", "button:has-text('Próxima')"]


def _papel_colunas(cab, ncol):
    """Descobre qual coluna é qual pelos títulos (português ou inglês); sem títulos, usa a ordem do portal."""
    papeis = {"codigo": 0, "processo": 1, "unidade": 2, "objeto": 3, "modalidade": 4, "inicio": 5, "fim": 6}
    achados = {}
    regras = [("codigo", r"^(c[oó]d|code)"), ("processo", r"process"), ("unidade", r"unid|unit"),
              ("objeto", r"objet|object"), ("modalidade", r"modal|mode"),
              ("fim", r"\b(fim|final|t[eé]rmino|end|encerr|limite|prazo|fechamento)"), ("inicio", r"in[ií]cio|start|abertura")]
    for i, t in enumerate([_norm(c) for c in cab][:ncol]):
        for papel, rx in regras:
            if papel not in achados and re.search(rx, t):
                achados[papel] = i
                break
    return achados if len(achados) >= 5 else papeis


def _todas_tabelas(page):
    """Tabelas da página e de quadros (iframes) dentro dela."""
    tabelas = []
    for fr in page.frames:
        try:
            tabelas += fr.evaluate(_JS_TABELAS)
        except Exception:                                              # noqa: BLE001
            pass
    return tabelas


def _esperar_tabela(page, limite_s=45):
    fim = time.time() + limite_s
    while time.time() < fim:
        for fr in page.frames:
            try:
                if fr.evaluate(_JS_PRONTO):
                    return True
            except Exception:                                          # noqa: BLE001
                pass
        page.wait_for_timeout(1000)
    return False


def _colunas_de_data(linhas, ncol):
    """Colunas cujas células começam com uma data (independe do idioma e do nome da coluna)."""
    cols = []
    for i in range(ncol):
        vals = [l[i] for l in linhas if i < len(l) and l[i].strip()]
        if vals and sum(1 for v in vals if re.match(r"\s*\d{1,2}/\d{1,2}/\d{4}", v)) >= 0.6 * len(vals):
            cols.append(i)
    return cols


_RX_DATA = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}")
_RX_UNIDADE = re.compile(r"\|\s|^\s*(sesi|senai|sest|senat|iel|cindes|findes)\b|^\s*[a-z]\s?\d{2,4}\s*-\s*(sest|senat)", re.I)
_RX_MODALIDADE = re.compile(r"^\s*(selection|auction|bidding|competition|open |closed |preg[aã]o|concorr|dispensa|convite|"
                            r"chamamento|leil[aã]o|processo seletivo|tomada|cota[cç][aã]o|credenc|disputa)", re.I)
_RX_MODALIDADE_EN = re.compile(r"^\s*(selection process|auction|bidding|competition)", re.I)
_PAPEIS = ("codigo", "processo", "unidade", "objeto", "modalidade", "inicio", "fim")
_TRADUCAO_MODALIDADE = {"selection process with competition": "Processo seletivo com concorrência",
                        "selection process without competition": "Processo seletivo sem concorrência"}


def _por_titulos(l, col):
    return {p: (l[col[p]].strip() if p in col and col[p] < len(l) else "") for p in _PAPEIS}


def _faz_sentido(d):
    """A leitura por títulos só vale se o 'objeto' parece um texto de objeto (não uma data, nem a modalidade)."""
    o = d["objeto"]
    return bool(o) and not _RX_DATA.match(o) and not _RX_DATA.match(d["unidade"]) \
        and not (len(o) <= 45 and _RX_MODALIDADE_EN.match(o)) and o != d["unidade"] and o != d["modalidade"]


def _por_conteudo(l):
    """Deduz o papel de cada célula pelo que ela contém, sem depender da ordem das colunas."""
    cels = [c.strip() for c in l]
    idx_datas = [i for i, c in enumerate(cels) if _RX_DATA.match(c)]
    resto = [c for i, c in enumerate(cels) if c and i not in idx_datas]
    unidades = [c for c in resto if _RX_UNIDADE.search(c) and len(c) <= 160]
    unidade = min(unidades, key=len) if unidades else ""
    modal = next((c for c in resto if c != unidade and len(c) <= 45 and _RX_MODALIDADE.match(c)), "")
    outros = [c for c in resto if c not in (unidade, modal)]
    objeto = max(outros, key=len) if outros else ""
    if re.fullmatch(r"[A-Za-z]{2,6}\s?\d[\d./-]{4,}|\d{1,8}", objeto):       # só sobrou um número de processo: não há objeto
        objeto = ""
    sobra = [c for c in outros if c != objeto]
    codigo = next((c for c in sobra if re.fullmatch(r"\d{1,8}", c)), "")
    processo = next((c for c in sobra if c != codigo), "")
    if not codigo:                                                   # "2819 CDE 2026002466" numa célula só
        for c in sobra:
            m2 = re.fullmatch(r"(\d{1,8})\s+([A-Za-z]{0,6}\s?\d[\d./-]{4,})", c)
            if m2:
                codigo, processo = m2.group(1), m2.group(2)
                break
    m = re.match(r"^(\d{1,8})\s+([A-Za-z]{0,6}\s?\d[\d./-]{4,})\s+(.+)$", objeto, re.S)     # "2819 CDE 2026002466 texto..."
    if m and not codigo and not processo:
        codigo, processo, objeto = m.group(1), m.group(2), m.group(3)
    return {"codigo": codigo, "processo": processo, "unidade": unidade, "objeto": objeto, "modalidade": modal,
            "inicio": cels[idx_datas[0]] if idx_datas else "", "fim": cels[idx_datas[-1]] if idx_datas else ""}


def _extrair_linhas(page):
    """Devolve (linhas, diagnostico). O diagnóstico vai para o dados.json para eu conferir o que o portal entregou."""
    tabelas = _todas_tabelas(page)
    melhor = max(tabelas, key=lambda t: sum(1 for l in t["linhas"] if len(l) >= 6), default=None)
    if not melhor:
        return [], {}
    cheias = [l for l in melhor["linhas"] if len(l) >= 6]
    ncol = max((len(l) for l in melhor["linhas"]), default=0)
    col = _papel_colunas(melhor["cab"], ncol)
    datas = _colunas_de_data(cheias, ncol)
    if len(datas) >= 2:                      # a primeira data é o início e a última é o encerramento
        col["inicio"], col["fim"] = datas[0], datas[-1]
    elif len(datas) == 1:
        col["fim"] = datas[0]
        col.setdefault("inicio", datas[0])
    saida, por_titulo, por_conteudo = [], 0, 0
    for l in cheias:
        d = _por_titulos(l, col)
        if _faz_sentido(d):
            por_titulo += 1
        else:
            d = _por_conteudo(l)
            por_conteudo += 1
        saida.append(d)
    diag = {"titulos": [_limpar(c, 30) for c in melhor["cab"][:10]], "colunas": ncol,
            "por_titulo": por_titulo, "por_conteudo": por_conteudo,
            "amostra": [[_limpar(c, 40) for c in l] for l in cheias[:2]]}
    return saida, diag


def ler_paradigma(chave, log=print):
    cfg = FONTES[chave]
    status = {"nome": cfg["nome"], "url": cfg["url"]}
    if not garantir_playwright(log):
        status.update(ok=False, aviso="navegador automático indisponível nesta execução")
        return [], status
    from playwright.sync_api import sync_playwright
    linhas, vistos = [], set()
    with sync_playwright() as p:
        navegador = _abrir_navegador(p, log)
        try:
            ctx = navegador.new_context(locale="en-US", timezone_id="America/Sao_Paulo", user_agent=USER_AGENT_NAVEGADOR,
                                        viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
            if not _esperar_tabela(page):
                corpo = _norm(page.inner_text("body"))
                if re.search(r"no records|nenhum registro|sem registros", corpo):
                    status.update(ok=True, n=0, aviso="o portal não mostra processos abertos agora")
                    return [], status
                raise RuntimeError("a tabela de processos não carregou")
            for _ in range(25):                                        # páginas, se houver paginação
                novos = 0
                achadas, diag_pagina = _extrair_linhas(page)
                if diag_pagina and "diag" not in status:
                    status["diag"] = diag_pagina                      # só a 1ª página
                for l in achadas:
                    k = (l["codigo"], l["processo"], l["unidade"], l["objeto"][:60], l["inicio"], l["fim"])
                    if k not in vistos:
                        vistos.add(k); linhas.append(l); novos += 1
                clicou = False
                if novos:
                    for sel in _SEL_PROXIMA:
                        alvo = page.locator(sel).first
                        if alvo.count() and alvo.is_visible():
                            alvo.click(); page.wait_for_timeout(1500); clicou = True
                            break
                if not clicou:
                    break
        finally:
            navegador.close()

    itens = []
    descartados_uf = sem_data = encerradas = sem_objeto = 0
    for l in linhas:
        fecha = parse_data_hora(l["fim"])
        if not fecha:
            sem_data += 1
            continue
        if not _futuro(fecha):
            encerradas += 1
            continue
        if not l["objeto"] or _RX_DATA.match(l["objeto"]):
            sem_objeto += 1                                           # nunca mostrar uma data como se fosse o objeto
            continue
        ufs = _ufs_no_texto(l["unidade"] + " " + l["objeto"])
        uf = "?"
        if cfg.get("filtrar_uf"):
            if "ES" in ufs or "espirito santo" in _norm(l["objeto"] + " " + l["unidade"]):
                uf = "ES"
            elif ufs:
                descartados_uf += 1
                continue
        else:
            uf = "ES"
        proc = l["processo"] or l["codigo"]
        cidade = re.search(r"([A-ZÀ-Ú][A-ZÀ-Ú' ]+)/([A-Z]{2})\s*$", l["unidade"])
        ident = "|".join([l["codigo"], l["processo"], l["unidade"], l["objeto"][:80], l["inicio"], l["fim"]])
        itens.append({
            "id": "%s:%s:%s" % (chave, _limpar(proc)[:40] or "s-n", hashlib.md5(ident.encode("utf-8")).hexdigest()[:8]),
            "objeto": _limpar(l["objeto"], 1500),
            "orgao": _limpar(l["unidade"], 140) or cfg["nome"], "unidade": "",
            "mun": (cidade.group(1).title() + "/" + cidade.group(2)) if cidade else "",
            "esfera": "S", "mod": _TRADUCAO_MODALIDADE.get(_norm(l["modalidade"]), _limpar(l["modalidade"])),
            "sit": "", "valor": None, "srp": False,
            "num": _limpar(proc), "abre": parse_data_hora(l["inicio"], (0, 0)), "fecha": fecha,
            "pub": parse_data_hora(l["inicio"], (0, 0)) or fecha, "origem": cfg["nome"], "link": "",
            "url": cfg["url"], "fonte": cfg["nome"], "uf": uf,
        })
    status.update(ok=True, n=len(itens), lidas=len(linhas), fora_do_es=descartados_uf, encerradas=encerradas)
    if sem_data:
        status["sem_data"] = sem_data
    if linhas and sem_data >= max(1, len(linhas) // 2):
        status["ok"] = False
        status["aviso"] = "não consegui entender as datas do painel (%d de %d linhas)" % (sem_data, len(linhas))
    elif not linhas:
        status["aviso"] = "tabela vazia"
    if sem_objeto:
        status["sem_objeto"] = sem_objeto
        if not itens:
            status["ok"] = False
            status["aviso"] = "li as linhas do painel mas não consegui identificar o objeto (%d linha(s))" % sem_objeto
    return itens, status


# ----------------------------------------------------------------------------- SENAC ES (HTML simples)

SECOES_SENAC = {"chamamento publico": "Chamamento Público", "leilao": "Leilão", "pregoes presenciais": "Pregão Presencial",
                "pregoes eletronicos": "Pregão Eletrônico", "concorrencias": "Concorrência", "convites": "Convite",
                "regulamento proprio": "Regulamento"}
_ENCERRADO = re.compile(r"encerrad|homologad|deserto|fracassad|vencedor|revogad|cancelad|anulad|adjudicad|finalizad", re.I)
_TITULO = re.compile(r"\b\d{1,4}\s*/\s*\d{4}\b")


class _FluxoSenac(HTMLParser):
    """Lê a página como uma sequência de textos e links (não depende do desenho do HTML).
    Um edital = textos (título, objeto, data...) seguidos de um ou mais links de PDF; textos que
    aparecem depois dos links e não parecem um título novo são anotações do mesmo edital."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.itens, self.secao = base, [], ""
        self.textos, self.links, self.notas = [], [], []
        self._ignorar, self._a = 0, None

    def _fechar_bloco(self):
        if self.textos and self.links:
            self.itens.append({"secao": self.secao, "textos": self.textos, "links": self.links, "notas": self.notas})
        self.textos, self.links, self.notas = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._ignorar += 1
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if re.search(r"documentolicitatorio|\.pdf(\?|$)", href, re.I):
                self._a = {"href": urllib.parse.urljoin(self.base, href), "rot": []}

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self._ignorar = max(self._ignorar - 1, 0)
        elif tag == "a" and self._a is not None:
            self.links.append((self._a["href"], _limpar(" ".join(self._a["rot"])) or "Documento"))
            self._a = None

    def handle_data(self, data):
        if self._ignorar:
            return
        t = _limpar(data)
        if not t:
            return
        if self._a is not None:
            self._a["rot"].append(t)
            return
        if _norm(t) in SECOES_SENAC:
            self._fechar_bloco()
            self.secao = SECOES_SENAC[_norm(t)]
            return
        if self.links:
            if _TITULO.search(t) and len(t) < 140:
                self._fechar_bloco()
                self.textos.append(t)
            else:
                self.notas.append(t)
            return
        self.textos.append(t)

    def fim(self):
        self._fechar_bloco()
        return self.itens


def interpretar_senac(html, base="https://es.senac.br/licitacoes"):
    f = _FluxoSenac(base)
    f.feed(html)
    blocos = f.fim()
    itens = []
    for b in blocos:
        textos, notas = b["textos"], b["notas"]
        titulo = next((t for t in textos if _TITULO.search(t) and len(t) < 140), textos[0])
        todo = " | ".join(textos + notas)
        if _ENCERRADO.search(" ".join(notas + [t for t in textos if t != titulo and len(t) < 200])) and not re.search(r"reabert", todo, re.I):
            continue
        linha_data = next((t for t in textos if re.search(r"abertura|sess[aã]o|data", t, re.I) and _RE_DATA.search(t)), None) \
            or next((t for t in textos if _RE_DATA.search(t)), "")
        fecha = parse_data_hora(linha_data)
        if not fecha or not _futuro(fecha):
            continue
        candidatos = [t for t in textos if t != titulo and t != linha_data and not re.match(r"^(abertura|data)\b", _norm(t))]
        objeto = max(candidatos, key=len) if candidatos else titulo
        objeto = re.sub(r"^objeto\s*[:\-–]\s*", "", objeto, flags=re.I)
        sit = "Suspenso" if re.search(r"suspens", todo, re.I) else ""
        arqs = [[rot, "Edital" if i == 0 else "Documento", href] for i, (href, rot) in enumerate(b["links"])]
        itens.append({
            "id": "senac_es:%s:%s" % (_norm(b["secao"]), _norm(titulo)), "objeto": _limpar("%s – %s" % (titulo, objeto) if objeto != titulo else titulo, 1500),
            "orgao": "SENAC ES", "unidade": "", "mun": "", "esfera": "S", "mod": b["secao"], "sit": sit,
            "valor": None, "srp": False, "num": _limpar(titulo), "abre": fecha, "fecha": fecha, "pub": fecha,
            "origem": "SENAC ES", "link": "", "url": b["links"][0][0], "fonte": "SENAC ES", "uf": "ES", "arqs": arqs,
        })
    return itens, len(blocos)


def ler_senac(chave="senac_es", log=print):
    cfg = FONTES[chave]
    status = {"nome": cfg["nome"], "url": cfg["url"]}
    req = urllib.request.Request(cfg["url"], headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    itens, lidos = interpretar_senac(html, cfg["url"])
    status.update(ok=True, n=len(itens), lidas=lidos)
    if lidos == 0:
        status.update(ok=False, aviso="não encontrei editais na página (o site pode ter mudado)")
    return itens, status


# ----------------------------------------------------------------------------- Portal de Compras Públicas (PCP)

MUNICIPIOS_ES = [
    "Afonso Cláudio", "Água Doce do Norte", "Águia Branca", "Alegre", "Alfredo Chaves", "Alto Rio Novo", "Anchieta",
    "Apiacá", "Aracruz", "Atilio Vivacqua", "Baixo Guandu", "Barra de São Francisco", "Boa Esperança",
    "Bom Jesus do Norte", "Brejetuba", "Cachoeiro de Itapemirim", "Cariacica", "Castelo", "Colatina",
    "Conceição da Barra", "Conceição do Castelo", "Divino de São Lourenço", "Domingos Martins", "Dores do Rio Preto",
    "Ecoporanga", "Fundão", "Governador Lindenberg", "Guaçuí", "Guarapari", "Ibatiba", "Ibiraçu", "Ibitirama",
    "Iconha", "Irupi", "Itaguaçu", "Itapemirim", "Itarana", "Iúna", "Jaguaré", "Jerônimo Monteiro", "João Neiva",
    "Laranja da Terra", "Linhares", "Mantenópolis", "Marataízes", "Marechal Floriano", "Marilândia", "Mimoso do Sul",
    "Montanha", "Mucurici", "Muniz Freire", "Muqui", "Nova Venécia", "Pancas", "Pedro Canário", "Pinheiros", "Piúma",
    "Ponto Belo", "Presidente Kennedy", "Rio Bananal", "Rio Novo do Sul", "Santa Leopoldina", "Santa Maria de Jetibá",
    "Santa Teresa", "São Domingos do Norte", "São Gabriel da Palha", "São José do Calçado", "São Mateus",
    "São Roque do Canaã", "Serra", "Sooretama", "Vargem Alta", "Venda Nova do Imigrante", "Viana", "Vila Pavão",
    "Vila Valério", "Vila Velha", "Vitória",
]
_MUN_ES = sorted(((_norm(m), m) for m in MUNICIPIOS_ES), key=lambda x: -len(x[0]))
_RX_PCP_STATUS = re.compile(r"^(recebendo|aguardando|cancelad|suspens|encerrad|em (disputa|julgamento|analise|andamento|recurso|fase)"
                            r"|homologad|deserto|fracassad|revogad|anulad|finalizad|adjudicad|concluid|aberto|publicad)")
_RX_PCP_FORA = re.compile(r"cancel|revog|anul|deserto|fracass|encerrad|homolog|adjudic|finaliz|conclu")
_CHIPS_PCP = {"exclusivo me", "exclusivo me/epp", "acessar processo", "ampla participacao", "cota reservada"}
MAX_PAGINAS_PCP = 45                                   # 45 páginas x 12 = 540 processos (o ES tem ~250 abertos)
ESPERA_PAGINA_PCP = float(os.environ.get("COLETOR_ESPERA_PCP", "50"))   # segundos esperando os cartões de uma página

_JS_CARTOES = """() => [...document.querySelectorAll('article')].map(a => {
    const lk = a.closest('a[href]') || a.querySelector('a[href*="/processos/"]');
    return {href: lk ? (lk.getAttribute('href') || '') : '', texto: (a.innerText || '').trim()};
})"""


def _municipio_pcp(comprador):
    """O comprador termina com o município ('Prefeitura Municipal de Guarapari', 'Fundo Municipal de Saúde de Ibatiba',
    'Secretaria Municipal de Obras de Serra - SEOB'). Só aceito quando é um dos 78 municípios do ES."""
    base = _norm(re.split(r"\s+-\s+", comprador or "")[0])
    for nn, nome in _MUN_ES:
        if re.search(r"(?:^|\s)" + re.escape(nn) + r"$", base):
            return nome
    return ""


def _esfera_pcp(comprador):
    n = _norm(comprador)
    if re.search(r"\b(sesi|senai|senac|sest|senat|sebrae|sesc|findes)\b|sistema s", n):
        return "S"
    if re.search(r"federal|ministerio|exercito|marinha|aeronautica|ufes|ifes", n):
        return "F"
    if re.search(r"estadual|estado do espirito santo|governo do estado|secretaria de estado|detran|idaf|iema|cesan|bandes|"
                 r"policia militar|corpo de bombeiros|defensoria|tribunal de justica|assembleia legislativa", n):
        return "E"
    if re.search(r"prefeitura|municipal|municipio|camara|consorcio|samae|saae|semae|autarquia", n):
        return "M"
    return ""


def interpretar_cartao_pcp(texto, href=""):
    """Um cartão da lista do PCP, lido como texto puro (não depende do desenho do HTML):
       Nº do Processo: 059/2026 / <situação> / <objeto, cortado em ~250 letras> / Exclusivo ME /
       Abertura das Propostas: 03/12/2026 às 12:00 / Modalidade: ... / Comprador: ... / Acessar processo"""
    linhas = [l for l in (_limpar(x) for x in (texto or "").replace("\r", "").split("\n")) if l]
    d = {"numero": "", "status": "", "objeto": "", "abertura": "", "modalidade": "", "comprador": "", "href": href or ""}
    resto = []
    rotulos = (("numero", r"^N[º°o]\.?\s*do\s+Processo\s*:\s*(.*)$"), ("abertura", r"^Abertura\s+das\s+Propostas\s*:\s*(.*)$"),
               ("modalidade", r"^Modalidade\s*:\s*(.*)$"), ("comprador", r"^Comprador\s*:\s*(.*)$"))
    for l in linhas:
        for campo, rx in rotulos:
            m = re.match(rx, l, re.I)
            if m:
                d[campo] = m.group(1).strip()
                break
        else:
            resto.append(l)
    for l in resto:
        if len(l) < 80 and _RX_PCP_STATUS.match(_norm(l)):
            d["status"] = l
            break
    cand = [l for l in resto if l != d["status"] and _norm(l) not in _CHIPS_PCP]
    longos = [l for l in cand if len(l) >= 40]
    d["objeto"] = _limpar(" ".join(longos) if longos else (max(cand, key=len) if cand else ""))
    return d if (d["numero"] or d["objeto"]) else None


def _num_ano(s):
    m = re.search(r"(\d+)\s*/\s*(\d{4})", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _palavras(t):
    return set(re.findall(r"[a-z0-9]{4,}", _norm(t)))


def repetida_no_pncp(it, pncp):
    """True se o processo do PCP já está na lista como licitação do PNCP (o PCP envia boa parte ao PNCP).
    Só considera repetida quando o número/ano, o município (ou o órgão) E o texto do objeto batem: numeração igual em
    modalidades diferentes é comum, e esconder uma licitação de verdade seria pior do que mostrar uma repetida."""
    k = _num_ano(it.get("num"))
    pa = _palavras(it.get("objeto"))
    if not k or not pa:
        return False
    mun, org = _norm(it.get("mun")), _norm(it.get("orgao"))
    for o in pncp:
        if o.get("fonte") or _num_ano(o.get("num")) != k:
            continue
        alvo = _norm(" ".join([o.get("orgao") or "", o.get("unidade") or "", o.get("mun") or ""]))
        mesmo_lugar = bool(mun and re.search(r"(?:^|\s)" + re.escape(mun) + r"(?:$|\s)", alvo)) or bool(not mun and org and org in alvo)
        if mesmo_lugar and len(pa & _palavras(o.get("objeto"))) / len(pa) >= 0.6:
            return True
    return False


def _url_pcp(base, n):
    u = urllib.parse.urlsplit(base)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if k != "pagina"]
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode([("pagina", str(n))] + q), ""))


def _sem_imagens(route):
    try:
        if route.request.resource_type in ("image", "font", "media"):
            route.abort()
        else:
            route.continue_()
    except Exception:                                                  # noqa: BLE001
        pass


def _ler_pagina_pcp(page, url, limite):
    """Abre uma página da lista e devolve os cartões. A lista chega depois da página (aparece 'carregando' e
    '0 Registros' por alguns segundos), então espero os cartões aparecerem; 2 tentativas."""
    for _ in range(2):
        espera = int(min(ESPERA_PAGINA_PCP, limite - time.time() - 5) * 1000)
        if espera < 3000:
            return []
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=min(60000, espera))
            page.wait_for_selector("article", timeout=espera)
            page.wait_for_timeout(400)
            achados = page.evaluate(_JS_CARTOES)
            if achados:
                return achados
        except Exception:                                              # noqa: BLE001
            pass
    return []


def ler_pcp(chave="pcp", log=print, prazo_final=None):
    cfg = FONTES[chave]
    status = {"nome": cfg["nome"], "url": cfg["url"]}
    if not garantir_playwright(log):
        status.update(ok=False, aviso="navegador automático indisponível nesta execução")
        return [], status
    t0 = time.time()
    limite = t0 + float(os.environ.get("COLETOR_ORCAMENTO_PCP", "420"))
    if prazo_final:
        limite = min(limite, prazo_final)
    if limite - t0 < 60:
        status.update(ok=False, aviso="sem tempo nesta execução (as outras fontes já usaram o limite)")
        return [], status
    from playwright.sync_api import sync_playwright
    cartoes, vistos, paginas, falhas, parou = [], set(), 0, [], ""
    with sync_playwright() as p:
        navegador = _abrir_navegador(p, log)
        try:
            ctx = navegador.new_context(locale="pt-BR", timezone_id="America/Sao_Paulo", user_agent=USER_AGENT_NAVEGADOR,
                                        viewport={"width": 1280, "height": 900})
            ctx.route("**/*", _sem_imagens)
            page = ctx.new_page()
            for n in range(1, MAX_PAGINAS_PCP + 1):
                if time.time() > limite - 20:
                    parou = "tempo"
                    break
                achados = _ler_pagina_pcp(page, _url_pcp(cfg["url"], n), limite)
                if not achados:
                    falhas.append(n)
                    if len(falhas) >= 2 and (not paginas or falhas[-2] == n - 1):
                        parou = "falha"                                # duas páginas seguidas sem resposta: desisto
                        break
                    continue
                paginas += 1
                datas = []
                for c in achados:
                    d = interpretar_cartao_pcp(c.get("texto"), c.get("href"))
                    if not d:
                        continue
                    k = d["href"] or (d["numero"], d["comprador"], d["objeto"][:60])
                    if k in vistos:
                        continue
                    vistos.add(k)
                    cartoes.append(d)
                    iso = parse_data_hora(d["abertura"], (0, 0))
                    if iso:
                        datas.append(iso)
                if datas and not _futuro(datas[-1]):
                    parou = "passado"                                  # a lista vai da data mais distante para a mais antiga:
                    break                                              # se o último cartão da página já passou, o resto também
        finally:
            navegador.close()

    itens = []
    fora_es = sem_data = encerradas = fora_status = sem_objeto = 0
    for d in cartoes:
        m = re.match(r"^/processos/([a-z]{2})/([^/]+)/([^/?#]+)", d["href"] or "")
        if (m.group(1).upper() if m else "ES") != "ES":
            fora_es += 1
            continue
        if _RX_PCP_FORA.search(_norm(d["status"])):
            fora_status += 1                                           # cancelado, revogado, deserto, encerrado...
            continue
        fecha = parse_data_hora(d["abertura"])
        if not fecha:
            sem_data += 1
            continue
        if not _futuro(fecha):
            encerradas += 1
            continue
        if not d["objeto"]:
            sem_objeto += 1
            continue
        pid = re.search(r"-(\d{4,})$", m.group(3)) if m else None
        ident = "|".join([d["numero"], d["comprador"], d["objeto"][:80], d["abertura"]])
        itens.append({
            "id": "pcp:" + (pid.group(1) if pid else hashlib.md5(ident.encode("utf-8")).hexdigest()[:12]),
            "objeto": _limpar(re.sub(r"\.\.\.$", "…", d["objeto"]), 1500),
            "orgao": _limpar(d["comprador"], 140) or cfg["nome"], "unidade": "",
            "mun": _municipio_pcp(d["comprador"]), "esfera": _esfera_pcp(d["comprador"]),
            "mod": _limpar(d["modalidade"]), "sit": "Suspenso" if "suspens" in _norm(d["status"]) else "",
            "valor": None, "srp": "registro de preco" in _norm(d["modalidade"]),
            "num": _limpar(d["numero"]), "abre": fecha, "fecha": fecha, "pub": fecha, "origem": cfg["nome"], "link": "",
            "url": urllib.parse.urljoin(cfg["url"], d["href"]) if d["href"] else cfg["url"],
            "fonte": cfg["nome"], "uf": "ES",
        })
    status.update(ok=True, n=len(itens), lidas=len(cartoes), paginas=paginas, segundos=int(time.time() - t0))
    if fora_es:
        status["fora_do_es"] = fora_es
    status["encerradas"] = encerradas + fora_status
    if sem_data:
        status["sem_data"] = sem_data
    if falhas:
        status["paginas_sem_resposta"] = falhas
    if not cartoes:
        status.update(ok=False, aviso="a lista de processos não carregou (o site pode ter mudado ou estava lento)")
    elif cartoes and sem_data >= max(1, len(cartoes) // 2):
        status.update(ok=False, aviso="não consegui entender as datas da lista (%d de %d cartões)" % (sem_data, len(cartoes)))
        if cartoes:
            status["diag"] = (cartoes[0]["numero"] + " | " + cartoes[0]["abertura"] + " | " + cartoes[0]["status"])[:200]
    elif parou == "tempo":
        status.update(parcial=True, aviso="li %d página(s) e parei pelo limite de tempo; o resto vem da leitura anterior" % paginas)
    elif parou == "falha":
        status.update(parcial=True, aviso="o portal parou de responder na página %d; o resto vem da leitura anterior" % (falhas[-1]))
    elif parou != "passado" and paginas >= MAX_PAGINAS_PCP:
        status["aviso"] = "limite de %d páginas atingido" % MAX_PAGINAS_PCP
    if sem_objeto:
        status["sem_objeto"] = sem_objeto
    return itens, status


# ----------------------------------------------------------------------------- orquestração

def coletar_todas(cfg, log=print, prazo_final=None):
    """Devolve (itens, {nome: status}). Nunca levanta exceção: cada fonte falha isoladamente.
    prazo_final: instante (time.time()) em que o trabalho todo precisa acabar; o PCP, o último, usa o que sobrar."""
    ligadas = cfg.get("fontes_extra") or {}
    itens, status = [], {}
    for chave, f in FONTES.items():
        if ligadas.get(chave, True) is False:
            continue
        if f["tipo"] == "pcp" and (cfg.get("uf") or "ES").upper() != "ES":
            continue                                                   # o número da UF no PCP só está configurado para o ES
        log("Lendo %s..." % f["nome"])
        try:
            if f["tipo"] == "paradigma":
                lido, st = ler_paradigma(chave, log)
            elif f["tipo"] == "pcp":
                lido, st = ler_pcp(chave, log, prazo_final)
            else:
                lido, st = ler_senac(chave, log)
        except Exception as e:                                         # noqa: BLE001
            lido, st = [], {"nome": f["nome"], "url": f["url"], "ok": False, "aviso": "falhou: %s" % str(e).splitlines()[0][:160]}
        st["em"] = datetime.now(BRT).isoformat(timespec="seconds")
        status[f["nome"]] = st
        itens.extend(lido)
        log("  %s: %s" % (f["nome"], ("%d abertas" % st.get("n", 0)) if st.get("ok") else st.get("aviso")))
    return itens, status
