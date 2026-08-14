#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tibia_xp.py — leitor de XP do Tracker Tibia 157 -> 300

Lê a XP total do personagem no highscores oficial do Tibia.com (filtrando por
mundo e vocação, que é onde chars de level baixo aparecem) e guarda uma leitura
por dia. A diferença entre duas leituras é a XP ganha no dia que começou no
Server Save da primeira delas.

Comandos:
  python tibia_xp.py setup "Nome do Char"   configura e testa o personagem
  python tibia_xp.py once                   faz a leitura (espera até 12 min se o site
                                            estiver prestes a atualizar)
  python tibia_xp.py serve                  abre o tracker em http://localhost:8777
  python tibia_xp.py agendar                agenda a leitura de hora em hora
  python tibia_xp.py agendar 1440           agenda 1x por dia (ou outro intervalo em minutos)
  python tibia_xp.py status                 mostra o que já foi lido
  python tibia_xp.py publicar               envia as leituras para o GitHub Pages

Sem dependências externas — só a biblioteca padrão do Python.
"""
import sys, os, re, json, gzip, time, html, datetime, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_F = os.path.join(BASE, "tibia_config.json")
LEI_F = os.path.join(BASE, "tibia_leituras.json")
JS_F = os.path.join(BASE, "leituras.js")
HTML_F = os.path.join(BASE, "tracker_tibia.html")
PORTA = 8777
ULTIMA_IDADE = None   # "Last Update: X minutes ago", informado pelo próprio Tibia.com

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
# códigos de vocação usados pelo highscores do Tibia.com
PROF = {"none": 1, "knight": 2, "elite knight": 2, "paladin": 3, "royal paladin": 3,
        "sorcerer": 4, "master sorcerer": 4, "druid": 5, "elder druid": 5}


# ---------------------------------------------------------------- utilidades
def hoje():
    return datetime.date.today().isoformat()


def _ultimo_domingo(ano, mes):
    d = datetime.date(ano, mes, 31)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def horas_ate_o_ss(dt_utc):
    """Quantas horas recuar para achar o Server Save.

    O SS é 10:00 no horário da Alemanha, que muda com o horário de verão europeu:
    CEST (UTC+2) entre o último domingo de março e o de outubro -> 08:00 UTC;
    CET (UTC+1) no resto do ano -> 09:00 UTC.
    """
    ano = dt_utc.year
    ini = datetime.datetime.combine(_ultimo_domingo(ano, 3), datetime.time(1, 0))
    fim = datetime.datetime.combine(_ultimo_domingo(ano, 10), datetime.time(1, 0))
    return 8 if ini <= dt_utc < fim else 9


def ancora_de(dt_utc):
    """Data do Server Save a que uma leitura pertence."""
    return (dt_utc - datetime.timedelta(hours=horas_ate_o_ss(dt_utc))).date().isoformat()


def ss_passou():
    return datetime.datetime.utcnow().hour >= 8


def carregar(path, padrao):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return padrao


def gravar(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def get(url, tentativas=3):
    erro = None
    for n in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip"})
            r = urllib.request.urlopen(req, timeout=30)
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except Exception as e:
            erro = e
            time.sleep(1.5 * (n + 1))
    raise RuntimeError("falha ao acessar %s (%s)" % (url.split("?")[0], erro))


def exp_do_level(L):
    """XP total acumulada ao atingir o level L — fórmula oficial do Tibia."""
    return int((50 * (L - 1) ** 3 - 150 * (L - 1) ** 2 + 400 * (L - 1)) / 3)


def level_da_exp(e):
    L = 1
    while exp_do_level(L + 1) <= e:
        L += 1
    return L


# ------------------------------------------------------------------- fontes
def buscar_personagem(nome):
    """Mundo, level e vocação, lidos da própria página do personagem no Tibia.com."""
    h = get("https://www.tibia.com/community/?subtopic=characters&name=" + urllib.parse.quote(nome))
    if "does not exist" in h:
        raise RuntimeError('personagem "%s" não existe' % nome)
    campos = {}
    for a, b in re.findall(r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", h, re.S):
        k = html.unescape(re.sub("<[^>]+>", "", a)).replace(" ", " ").strip().rstrip(":")
        v = html.unescape(re.sub("<[^>]+>", "", b)).replace(" ", " ").strip()
        if k and k not in campos:
            campos[k] = v
    faltando = [k for k in ("Name", "World", "Level", "Vocation") if k not in campos]
    if faltando:
        raise RuntimeError("não consegui ler %s na página de %s" % (", ".join(faltando), nome))
    return {"nome": campos["Name"], "mundo": campos["World"],
            "level": int(re.sub(r"[^\d]", "", campos["Level"])), "vocacao": campos["Vocation"]}


def idade_do_highscores(pagina_html):
    """O Tibia.com informa há quanto tempo o highscores foi atualizado (de hora em hora)."""
    m = re.search(r"Last Update:\s*([^<]{1,40})", pagina_html)
    return m.group(1).strip() if m else None


def minutos_de_idade(txt):
    """'56 minutes ago' -> 56. O highscores atualiza de hora em hora."""
    if not txt:
        return None
    t = txt.lower()
    if "less than" in t or "just now" in t:
        return 0
    m = re.search(r"(\d+)\s*hour", t)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*min", t)
    if m:
        return int(m.group(1))
    return None


MARGEM = 4   # minutos de folga depois da atualização estimada


def sincronizar_agenda(cfg, verboso=True):
    """Ajusta o minuto da tarefa para logo depois da hora em que o site atualiza.

    Sem isso, uma tarefa em minuto fixo pode cair logo ANTES da atualização e ficar
    quase uma hora carregando dado velho. Como a página informa a idade do dado,
    dá para calcular quando vem a próxima e chegar poucos minutos depois dela.
    """
    idade = minutos_de_idade(ULTIMA_IDADE)
    if idade is None:
        return
    agora = datetime.datetime.now()
    proxima = agora - datetime.timedelta(minutes=idade) + datetime.timedelta(minutes=60)
    alvo = (proxima + datetime.timedelta(minutes=MARGEM)).replace(second=0, microsecond=0)
    minuto = alvo.minute

    atual = cfg.get("minuto_leitura")
    if atual is not None and abs(((minuto - atual + 30) % 60) - 30) <= 1:
        return                                    # já está sincronizado
    import subprocess
    bat = os.path.join(BASE, "atualizar_xp.bat")
    if not os.path.exists(bat):
        return
    r = subprocess.run(["schtasks", "/Create", "/TN", "TrackerTibiaXP", "/SC", "HOURLY", "/MO", "1",
                        "/ST", "%02d:%02d" % (alvo.hour, minuto), "/TR", bat, "/F"],
                       capture_output=True, text=True, encoding="latin-1")
    if r.returncode == 0:
        cfg["minuto_leitura"] = minuto
        gravar(CFG_F, cfg)
        if verboso:
            print("  agenda sincronizada: leituras no minuto :%02d (o site atualizou há %d min)"
                  % (minuto, idade))
    elif verboso:
        print("  não consegui reajustar a agenda: %s" % ((r.stdout or "") + (r.stderr or "")).strip()[:80])


def _pagina(mundo, prof, n):
    url = ("https://www.tibia.com/community/?subtopic=highscores&world=%s&category=6&profession=%d&currentpage=%d"
           % (urllib.parse.quote(mundo), prof, n))
    h = get(url)
    global ULTIMA_IDADE
    ULTIMA_IDADE = idade_do_highscores(h) or ULTIMA_IDADE
    linhas = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        tds = [html.unescape(re.sub("<[^>]+>", "", c)).strip()
               for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) >= 6 and tds[0].isdigit():
            try:
                linhas.append({"rank": int(tds[0]), "nome": tds[1], "vocacao": tds[2],
                               "mundo": tds[3], "level": int(tds[4]),
                               "exp": int(re.sub(r"[^\d]", "", tds[5]))})
            except ValueError:
                pass
    return linhas


def buscar_exp(mundo, nome, level, vocacao, verboso=True):
    """Busca binária nas 20 páginas do highscores da vocação. Devolve (exp, rank) ou (None, None)."""
    prof = PROF.get(vocacao.lower())
    if not prof:
        raise RuntimeError("vocação desconhecida: " + vocacao)
    alvo = nome.lower()
    cache = {}

    def pag(n):
        if n not in cache:
            if verboso:
                print("  lendo página %d..." % n)
            cache[n] = _pagina(mundo, prof, n)
            time.sleep(0.4)
        return cache[n]

    lo, hi = 1, 20
    while lo <= hi:
        m = (lo + hi) // 2
        l = pag(m)
        if not l:
            hi = m - 1
            continue
        for e in l:
            if e["nome"].lower() == alvo:
                return e["exp"], e["rank"]
        if level > l[0]["level"]:
            hi = m - 1
        elif level < l[-1]["level"]:
            lo = m + 1
        else:
            # level cai dentro da faixa da página mas o nome não estava lá:
            # empates de level podem jogar o char para uma página vizinha
            for n in (m - 1, m + 1, m - 2, m + 2):
                if 1 <= n <= 20:
                    for e in pag(n):
                        if e["nome"].lower() == alvo:
                            return e["exp"], e["rank"]
            break
    return None, None


# ------------------------------------------------------------------- leitura
def ler(verboso=True, esperar=0):
    cfg = carregar(CFG_F, {})
    if not cfg.get("char"):
        raise RuntimeError('nenhum personagem configurado — rode: python tibia_xp.py setup "Nome do Char"')

    ch = buscar_personagem(cfg["char"])
    cfg.update(char=ch["nome"], mundo=ch["mundo"], vocacao=ch["vocacao"])
    gravar(CFG_F, cfg)
    if verboso:
        print("%s — %s, %s, level %d" % (ch["nome"], ch["mundo"], ch["vocacao"], ch["level"]))

    exp, rank = buscar_exp(ch["mundo"], ch["nome"], ch["level"], ch["vocacao"], verboso)
    fonte = "highscores"
    if exp is None:
        exp, rank = exp_do_level(ch["level"]), None
        fonte = "level"
        if verboso:
            print("  fora do top 1000 da vocação — usando a XP mínima do level %d" % ch["level"])

    dados = carregar(LEI_F, {})
    if "leituras" not in dados:                      # migra o formato antigo (um registro por dia)
        antigas = [dict(v, ts=v.get("ts", d + "T12:00:00"), ancora=d) for d, v in sorted(dados.items())]
        dados = {"leituras": antigas}
    leituras = dados["leituras"]

    agora = datetime.datetime.utcnow()
    anc = ancora_de(agora)
    anterior = None
    for l in leituras:
        if l.get("ancora", "") < anc:
            anterior = l
    ja_tem = [l for l in leituras if l.get("ancora") == anc]

    leituras.append({"ts": agora.isoformat(timespec="seconds") + "Z", "ancora": anc,
                     "exp": exp, "level": ch["level"], "rank": rank, "fonte": fonte,
                     "idade": ULTIMA_IDADE})
    dados["leituras"] = leituras[-400:]
    gravar(LEI_F, dados)
    escrever_js(dados, cfg)

    sincronizar_agenda(cfg, verboso)

    # Se a próxima atualização do site está logo aí, não faz sentido voltar só daqui
    # a uma hora: espera os poucos minutos que faltam e lê de novo, já com dado fresco.
    idade = minutos_de_idade(ULTIMA_IDADE)
    if esperar and idade is not None:
        falta = 60 - idade
        if 0 < falta <= esperar:
            if verboso:
                print("  faltam ~%d min para o site atualizar — esperando para pegar o dado novo" % falta)
            time.sleep((falta + MARGEM) * 60)
            return ler(verboso, esperar=0)          # uma tentativa extra, sem repetir a espera

    publicar_no_git(verboso)

    ganho = (exp - anterior["exp"]) if anterior else None
    # o que rendeu entre dois Server Saves pertence ao dia que começou no primeiro deles
    linha = anterior["ancora"] if anterior else anc
    if verboso:
        print("  XP total: {:,}".format(exp).replace(",", ".") + (" (rank %d)" % rank if rank else ""))
        print("  ciclo do Server Save de %s%s" % (anc, ("  ·  highscores: " + ULTIMA_IDADE) if ULTIMA_IDADE else ""))
        if ja_tem and ja_tem[-1]["exp"] == exp:
            print("  mesmo valor da leitura anterior — o highscores ainda não atualizou")
        elif ganho is None:
            print("  primeira leitura — serve de marco; o ganho aparece na leitura do próximo SS")
        else:
            print("  ganho do dia {}: {:+,}".format(linha, ganho).replace(",", "."))
    return {"ok": True, "data": linha, "ciclo": anc, "exp": exp, "level": ch["level"], "rank": rank,
            "fonte": fonte, "ganho": ganho, "char": ch["nome"], "mundo": ch["mundo"],
            "vocacao": ch["vocacao"], "idade": ULTIMA_IDADE,
            "repetida": bool(ja_tem and ja_tem[-1]["exp"] == exp)}


def escrever_js(dados, cfg):
    """Publica as leituras para a página.

    O .js é o que funciona com o arquivo aberto direto do disco (file://); o .json
    é o que a página hospedada busca de tempos em tempos. Havendo uma pasta docs/
    (layout do GitHub Pages), escreve nos dois lugares.
    """
    leituras = dados.get("leituras", [])
    destinos = [BASE]
    docs = os.path.join(BASE, "docs")
    if os.path.isdir(docs):
        destinos.append(docs)
    for d in destinos:
        with open(os.path.join(d, "leituras.js"), "w", encoding="utf-8") as f:
            f.write("window.LEITURAS=" + json.dumps(leituras, ensure_ascii=False) + ";\n")
            f.write("window.LEITURAS_CFG=" + json.dumps(cfg, ensure_ascii=False) + ";\n")
        with open(os.path.join(d, "leituras.json"), "w", encoding="utf-8") as f:
            json.dump({"leituras": leituras, "cfg": cfg,
                       "atualizado": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"},
                      f, ensure_ascii=False)


def publicar_no_git(verboso=True):
    """Envia as leituras para o GitHub, que serve a página pública.

    O Tibia.com recusa conexões dos servidores do GitHub (403), então a leitura tem
    de sair deste PC. Aqui lê e publica; a nuvem só hospeda.
    """
    if not os.path.isdir(os.path.join(BASE, ".git")):
        return
    import subprocess

    def git(*a):
        return subprocess.run(["git"] + list(a), cwd=BASE, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    alvos = [f for f in ("tibia_leituras.json", "tibia_config.json",
                         "docs/leituras.js", "docs/leituras.json")
             if os.path.exists(os.path.join(BASE, f))]
    if not alvos:
        return
    git("add", *alvos)
    if git("diff", "--staged", "--quiet").returncode == 0:
        return                                           # nada mudou desde a última
    quando = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    git("-c", "commit.gpgsign=false", "commit", "-m", "leitura " + quando)
    r = git("push", "--quiet")
    if r.returncode != 0:
        # alguém mexeu no repositório pela web: traz o que veio de lá e tenta de novo
        git("-c", "rebase.autoStash=true", "pull", "--rebase", "--quiet")
        r = git("push", "--quiet")
    if verboso:
        print("  publicado em https://caiomgama.github.io/tibia-tracker/" if r.returncode == 0
              else "  não consegui publicar: %s" % (r.stderr or r.stdout).strip()[:120])



# ------------------------------------------------------------------ comandos
def cmd_setup(nome):
    ch = buscar_personagem(nome)
    print("Encontrado: %s — %s, %s, level %d" % (ch["nome"], ch["mundo"], ch["vocacao"], ch["level"]))
    print("Procurando no highscores de %s (%s)..." % (ch["mundo"], ch["vocacao"]))
    exp, rank = buscar_exp(ch["mundo"], ch["nome"], ch["level"], ch["vocacao"])
    cfg = carregar(CFG_F, {})
    cfg.update(char=ch["nome"], mundo=ch["mundo"], vocacao=ch["vocacao"])
    gravar(CFG_F, cfg)
    if exp is None:
        print("\nAVISO: %s não está no top 1000 de %s em %s." % (ch["nome"], ch["vocacao"], ch["mundo"]))
        print("A leitura automática só vai detectar quando você subir de level.")
        print("Use o Hunt Analyser no tracker para a XP exata do dia.")
    else:
        print("\nOK — rank %d da vocação, XP total {:,}".format(exp).replace(",", ".") % rank)
        print("Leitura automática 100% funcional. Rode: python tibia_xp.py agendar")
    return exp is not None


def cmd_status():
    cfg = carregar(CFG_F, {})
    dados = carregar(LEI_F, {})
    leituras = dados.get("leituras", [])
    print("Personagem: %s" % (cfg.get("char") or "(não configurado)"))
    if cfg.get("mundo"):
        print("Mundo/vocação: %s / %s" % (cfg["mundo"], cfg.get("vocacao", "?")))
    print("Leituras guardadas: %d" % len(leituras))
    marcos = {}
    for l in leituras:
        marcos.setdefault(l.get("ancora"), l)     # a primeira de cada ciclo é o marco
    ant = None
    for d in sorted(marcos)[-12:]:
        r = marcos[d]
        g = ("  ganho {:+,}".format(r["exp"] - ant).replace(",", ".")) if ant else "  (marco inicial)"
        print("  SS %s  level %-4d  XP {:>16,}".format(r["exp"]).replace(",", ".") % (d, r["level"]) + g)
        ant = r["exp"]


def cmd_agendar(minutos=None):
    """Cria a tarefa. Sem argumento, roda de hora em hora — que é o ritmo em que o
    próprio highscores se atualiza, então o dia vai rendendo no tracker sozinho."""
    import subprocess
    py = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(py):
        py = sys.executable
    bat = os.path.join(BASE, "atualizar_xp.bat")
    with open(bat, "w", encoding="utf-8") as f:
        linhas = ["@echo off",
                  'cd /d "%~dp0"',
                  '"%s" "%s" once >> tibia_xp.log 2>&1' % (py, os.path.join(BASE, "tibia_xp.py"))]
        f.write("\r\n".join(linhas) + "\r\n")

    cfg = carregar(CFG_F, {})
    if minutos:
        arg = ["/SC", "MINUTE", "/MO", str(minutos)]
        quando = "a cada %d minutos" % minutos
    else:
        # respeita o minuto já sincronizado com a atualização do site
        m = cfg.get("minuto_leitura")
        hora = "%02d:%02d" % (datetime.datetime.now().hour, m) if m is not None else "05:15"
        arg = ["/SC", "HOURLY", "/MO", "1", "/ST", hora]
        quando = "de hora em hora, no minuto :%s" % hora.split(":")[1]
    print("Criando a tarefa para rodar %s..." % quando)
    r = subprocess.run(["schtasks", "/Create", "/TN", "TrackerTibiaXP"] + arg + ["/TR", bat, "/F"],
                       capture_output=True, text=True, encoding="latin-1")
    saida = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0:
        # se o PC estiver desligado na hora marcada, roda assim que ligar
        subprocess.run(["powershell", "-NonInteractive", "-Command",
                        "$t = Get-ScheduledTask -TaskName TrackerTibiaXP; "
                        "$t.Settings.StartWhenAvailable = $true; "
                        "Set-ScheduledTask -InputObject $t | Out-Null"],
                       capture_output=True, text=True)
        print("Pronto — com o PC ligado, a XP entra sozinha.")
        print("Se o PC estiver desligado na hora, a leitura roda assim que ele ligar.")
        print("  conferir: schtasks /Query /TN TrackerTibiaXP")
        print("  rodar já: schtasks /Run /TN TrackerTibiaXP")
        print("  remover:  schtasks /Delete /TN TrackerTibiaXP /F")
        print("  só 1x/dia depois do SS:  python tibia_xp.py agendar 1440")
        print("  log:      %s" % os.path.join(BASE, "tibia_xp.log"))
        print("O minuto da leitura se ajusta sozinho ao horário em que o site atualiza.")
    else:
        print("Não consegui criar a tarefa:")
        print(saida.strip())
        print("Se falar de permissão, abra o Prompt como administrador e rode de novo.")


def cmd_serve():
    import http.server, socketserver, webbrowser, threading
    base = BASE

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=base, **kw)

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = urllib.parse.urlparse(self.path).path
            if p == "/":
                self.path = "/tracker_tibia.html"
                return super().do_GET()
            if p == "/api/status":
                cfg = carregar(CFG_F, {})
                lei = carregar(LEI_F, {}).get("leituras", [])
                return self._json({"ok": True, "servidor": True, "cfg": cfg, "leituras": lei})
            if p == "/api/puxar":
                try:
                    return self._json(ler(verboso=True, esperar=0))  # resposta imediata
                except Exception as e:
                    return self._json({"ok": False, "erro": str(e)}, 200)
            if p == "/api/leituras":
                return self._json(carregar(LEI_F, {}).get("leituras", []))
            return super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORTA), H) as srv:
        url = "http://localhost:%d/" % PORTA
        print("Tracker no ar em %s" % url)
        print("Na mesma rede (celular), troque localhost pelo IP deste PC.")
        print("Ctrl+C para parar.")
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nparado.")


def main():
    args = sys.argv[1:]
    cmd = args[0].lower() if args else "serve"
    try:
        if cmd == "setup":
            if len(args) < 2:
                print('uso: python tibia_xp.py setup "Nome do Char"')
                return 1
            cmd_setup(" ".join(args[1:]))
        elif cmd == "once":
            ler(verboso=True, esperar=12)
        elif cmd == "publicar":
            publicar_no_git()
        elif cmd == "status":
            cmd_status()
        elif cmd == "agendar":
            cmd_agendar(int(args[1]) if len(args) > 1 and args[1].isdigit() else None)
        elif cmd == "serve":
            cmd_serve()
        else:
            print(__doc__)
            return 1
    except Exception as e:
        print("ERRO: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
