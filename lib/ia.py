"""Conversa com o Claude pela API (roda no Python da .venv da skill, que tem o SDK oficial `anthropic`).

Cada etapa da edição automática abre uma Conversa: manda o material, recebe JSON no formato pedido
(structured outputs) e, na conferência, continua a mesma conversa com o relatório — assim o Claude corrige
o que ele mesmo fez, com o contexto inteiro (e o prefixo fica em cache, mais barato).

  .venv/bin/python lib/ia.py testar      -> testa a chave configurada (não gasta tokens)

Duas formas de pagar (⚙ Configurações › "Como usar o Claude"):
  api         a chave da API da Anthropic (cobrada por token) — o SDK `anthropic` direto.
  assinatura  o seu plano do Claude (Pro/Max) pelo Claude Code deste computador: cada pedido vira uma chamada headless do
              Claude Code (claude_agent_sdk, o mesmo motor do chat), sem ferramentas, com o JSON no formato pedido
              (output_format). Sai do limite do seu plano, não da carteira. Login: o do `claude` no Terminal, ou o token
              longo do `claude setup-token` colado no ⚙ (CLAUDE_CODE_OAUTH_TOKEN) — o mais garantido para rodar sozinho."""
import os, re, sys, json, time, base64, tempfile
import anthropic

LIB = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, LIB)
import chaves
from custos import custo_claude
BETA_FALLBACK = "server-side-fallback-2026-07-01"

class Recusa(Exception): pass

class Uso:
    def __init__(self): self.ent = self.sai = self.cw = self.cr = 0; self.usd = 0.0; self.chamadas = 0
    def soma(self, modelo, u):
        ent, sai = u.input_tokens or 0, u.output_tokens or 0
        cw, cr = getattr(u, "cache_creation_input_tokens", 0) or 0, getattr(u, "cache_read_input_tokens", 0) or 0
        custo = custo_claude(modelo, ent, sai, cw, cr)
        self.ent += ent; self.sai += sai; self.cw += cw; self.cr += cr; self.usd += custo; self.chamadas += 1
        return custo
    def dict(self): return dict(entrada=self.ent, saida=self.sai, cache_escrita=self.cw, cache_leitura=self.cr, usd=round(self.usd, 4), chamadas=self.chamadas)

def usa_assinatura(): return (chaves.ler().get("claude_auth") or "api") == "assinatura"

class Claude:
    def __init__(self, chave=None, modelo=None, log=print, ao_cobrar=None, assinatura=None):
        """ao_cobrar(dict): chamado a cada resposta cobrada (modelo, tokens, US$) — é daí que sai a aba Custo.
        assinatura=True força o plano do Claude (Claude Code), mesmo com a chave da API escolhida em ⚙ — é o modo
        "sem gastar API" do Ad Animado."""
        d = chaves.ler()
        self.modelo = modelo or d.get("modelo") or "claude-opus-5-5"
        self.uso = Uso(); self.log = log; self.fallback = True; self.ao_cobrar = ao_cobrar
        self.assinatura = bool(assinatura) or (chave is None and usa_assinatura())
        if self.assinatura: self.c = None; return
        chave = chave or d["anthropic"]
        if not chave: raise RuntimeError("a chave da API do Claude não está configurada (⚙ Configurações)")
        self.c = anthropic.Anthropic(api_key=chave, max_retries=4, timeout=1200.0)

    def conversa(self, sistema, esforco="high"):
        return ConversaAssinatura(self, sistema, esforco) if self.assinatura else Conversa(self, sistema, esforco)

    def _chamar(self, sistema, msgs, schema, max_tokens, esforco):
        """schema None = sem structured outputs (o JSON vem pedido no texto): para schemas grandes demais para a gramática."""
        cfg = {"effort": esforco}
        if schema is not None: cfg["format"] = {"type": "json_schema", "schema": schema}
        kw = dict(model=self.modelo, max_tokens=max_tokens, messages=msgs,
                  system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                  thinking={"type": "adaptive"}, cache_control={"type": "ephemeral"}, output_config=cfg)
        if self.fallback: kw.update(betas=[BETA_FALLBACK], fallbacks="default")
        with self.c.beta.messages.stream(**kw) as s:
            return s.get_final_message()

class Conversa:
    def __init__(self, cl, sistema, esforco):
        self.cl, self.sistema, self.esforco, self.msgs = cl, sistema, esforco, []

    def pedir(self, conteudo, schema, max_tokens=64000, rotulo="Claude", formato=True):
        """conteudo: texto ou lista de blocos (use texto()/imagem()). Devolve o JSON já lido.
        formato=False: não usa structured outputs (schema grande demais: "The compiled grammar is too large") — o schema vai
        no texto do pedido e o JSON é lido da resposta. Esse erro também troca para formato=False sozinho."""
        if not formato: conteudo = _com_schema(conteudo, schema)
        self.msgs.append({"role": "user", "content": conteudo})
        t0 = time.time(); esperas = 0; tentativa = -1
        while tentativa < 2:
            tentativa += 1
            try:
                final = self.cl._chamar(self.sistema, self.msgs, schema if formato else None, max_tokens, self.esforco)
            except anthropic.RateLimitError as e:          # conta nova tem limite de tokens por minuto: espera e tenta de novo
                esperas += 1
                if esperas > 6: self.msgs.pop(); raise RuntimeError("a API recusou por limite de uso várias vezes seguidas (veja os limites da sua conta no console da Anthropic)")
                try: seg = int(e.response.headers.get("retry-after", "60"))
                except (ValueError, AttributeError): seg = 60
                self.cl.log(f"{rotulo}: limite de uso por minuto da API; esperando {seg}s…"); time.sleep(min(max(seg, 10), 120)); tentativa -= 1; continue
            except anthropic.BadRequestError as e:
                if formato and "grammar" in str(e).lower():
                    self.msgs.pop(); self.cl.log(f"{rotulo}: formato grande demais para o modo estrito; pedindo o JSON no texto…")
                    return self.pedir(conteudo, schema, max_tokens, rotulo, formato=False)
                if self.cl.fallback and ("fallback" in str(e).lower() or "beta" in str(e).lower()):
                    self.cl.fallback = False; self.cl.log("(o modelo reserva não está disponível nesta conta; seguindo sem ele)"); continue
                self.msgs.pop(); raise
            except Exception:
                self.msgs.pop(); raise
            custo = self.cl.uso.soma(final.model, final.usage)
            if self.cl.ao_cobrar:
                u = final.usage
                self.cl.ao_cobrar(dict(rotulo=rotulo, modelo=final.model, entrada=u.input_tokens or 0, saida=u.output_tokens or 0,
                                       cache_escrita=getattr(u, "cache_creation_input_tokens", 0) or 0,
                                       cache_leitura=getattr(u, "cache_read_input_tokens", 0) or 0, usd=custo))
            if final.stop_reason == "refusal":
                self.msgs.pop(); det = getattr(final, "stop_details", None)
                raise Recusa(f"o Claude recusou este pedido ({getattr(det, 'category', None) or 'sem categoria'})")
            if final.stop_reason == "max_tokens":
                if tentativa < 2 and max_tokens < 128000:
                    max_tokens = min(128000, max_tokens * 2); self.cl.log(f"{rotulo}: resposta longa, pedindo de novo com mais espaço…"); continue
                self.msgs.pop(); raise RuntimeError("a resposta do Claude passou do limite de tamanho")
            texto = "".join(b.text for b in final.content if b.type == "text")
            try:
                dados = json.loads(texto) if formato else _json_do_texto(texto)
            except ValueError:
                if tentativa < 2: self.cl.log(f"{rotulo}: resposta ilegível, pedindo de novo…"); continue
                self.msgs.pop(); raise RuntimeError("o Claude devolveu um JSON inválido")
            self.msgs.append({"role": "assistant", "content": final.content})
            u = final.usage
            self.cl.log(f"{rotulo}: respondeu em {time.time() - t0:.0f}s · {u.output_tokens} tokens escritos · US$ {custo:.3f}")
            return dados
        self.msgs.pop(); raise RuntimeError("o Claude não conseguiu responder")

# ---------------------------------------------------------------- pela assinatura (Claude Code headless)
def ambiente_assinatura():
    """Tira a chave da API do ambiente (senão o Claude Code cobraria nela) e põe o token do ⚙, se houver."""
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDECODE"):
        os.environ.pop(k, None)
    env = {"CLAUDE_AGENT_SDK_CLIENT_APP": "atlas-editor/1.0", "DISABLE_AUTOUPDATER": "1"}
    tok = chaves.ler().get("claude_token") or ""
    if tok: env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    if os.environ.get("ESTUDIO_CHAT_API"): env["ANTHROPIC_BASE_URL"] = os.environ["ESTUDIO_CHAT_API"]      # testes
    return env

def erro_assinatura(texto, status=None):
    t = (texto or "").lower()
    if status == 429 or "rate limit" in t or "usage limit" in t or "limit reached" in t or "limite" in t:
        return RuntimeError("o limite do seu plano do Claude chegou por agora — espere ele renovar e retome (ou troque para a chave da API em ⚙)")
    if status in (401, 403) or "login" in t or "auth" in t or "credential" in t or "oauth" in t:
        return RuntimeError("o Claude Code deste computador não está logado: rode `claude` no Terminal e entre com a sua conta, "
                            "ou rode `claude setup-token` e cole o token em ⚙ Configurações")
    return RuntimeError("o Claude (assinatura) falhou: " + (texto or "sem detalhe")[:300])

def _json_do_texto(t):
    t = (t or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m: t = m.group(1).strip()
    i = min([x for x in (t.find("{"), t.find("[")) if x >= 0], default=-1)
    return json.loads(t[i:] if i > 0 else t)

async def _consultar(sistema, conteudo, schema, modelo, esforco, sessao, log_arq=None):
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
    env = ambiente_assinatura()
    async def mensagens():
        yield {"type": "user", "message": {"role": "user", "content": conteudo}, "parent_tool_use_id": None, "session_id": sessao or "default"}
    o = ClaudeAgentOptions(system_prompt=sistema, model=modelo, tools=[], allowed_tools=[], setting_sources=[], max_turns=6,
                           permission_mode="dontAsk", effort=esforco if esforco in ("low", "medium", "high", "xhigh", "max") else "high",
                           output_format={"type": "json_schema", "schema": schema} if schema else None, resume=sessao or None,
                           env=env, cwd=tempfile.gettempdir(), max_buffer_size=256 << 20,
                           stderr=(lambda l: open(log_arq, "a").write(l.rstrip() + "\n")) if log_arq else None)
    final = None
    async for m in query(prompt=mensagens(), options=o):
        if isinstance(m, ResultMessage): final = m
    return final

class ConversaAssinatura:
    """Mesma interface da Conversa (pedir -> JSON), mas pelo Claude Code logado com o seu plano. A conversa continua na
    mesma sessão do Claude Code (resume), então a correção e a revisão enxergam o que veio antes, como na API."""
    def __init__(self, cl, sistema, esforco):
        self.cl, self.sistema, self.esforco, self.sessao = cl, sistema, esforco, None

    def pedir(self, conteudo, schema, max_tokens=64000, rotulo="Claude", formato=True):
        import asyncio
        blocos = _com_schema(conteudo, schema)
        t0 = time.time(); ultimo = None
        for tentativa in range(3):
            try:
                r = asyncio.run(_consultar(self.sistema, blocos, schema if formato else None, self.cl.modelo, self.esforco, self.sessao))
            except Exception as e:
                raise erro_assinatura(str(e))
            if r is None: raise erro_assinatura("o Claude Code não respondeu")
            if r.is_error and formato and "grammar" in " ".join(r.errors or [r.result or ""]).lower():
                formato = False; self.cl.log(f"{rotulo}: formato grande demais para o modo estrito; pedindo o JSON no texto…"); continue
            if r.is_error: raise erro_assinatura("; ".join(r.errors or []) or r.result or r.subtype, getattr(r, "api_error_status", None))
            self.sessao = r.session_id or self.sessao
            u = r.usage or {}
            ent, sai = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
            cw, cr = int(u.get("cache_creation_input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0)
            self.cl.uso.ent += ent; self.cl.uso.sai += sai; self.cl.uso.cw += cw; self.cl.uso.cr += cr; self.cl.uso.chamadas += 1
            if self.cl.ao_cobrar:
                self.cl.ao_cobrar(dict(rotulo=rotulo, modelo=self.cl.modelo + " · assinatura (limite do plano)", entrada=ent, saida=sai,
                                       cache_escrita=cw, cache_leitura=cr, usd=0.0))
            dados = r.structured_output
            if dados is None:
                try: dados = _json_do_texto(r.result)
                except ValueError: dados = None
            if isinstance(dados, (dict, list)):
                self.cl.log(f"{rotulo}: respondeu em {time.time() - t0:.0f}s · {sai} tokens escritos · pela assinatura")
                return dados
            ultimo = r.result
            self.cl.log(f"{rotulo}: resposta fora do formato, pedindo de novo…")
            blocos = [texto("Sua última resposta não veio no formato JSON pedido. Responda de novo, só com o JSON.")]
        raise RuntimeError("o Claude (assinatura) não devolveu o JSON pedido: " + str(ultimo)[:200])

def texto(t): return {"type": "text", "text": t}

def _com_schema(conteudo, schema):
    """O pedido + o formato da resposta por escrito (reforço do structured outputs, e o único formato quando ele sai)."""
    blocos = [texto(conteudo)] if isinstance(conteudo, str) else list(conteudo)
    return blocos + [texto("FORMATO DA RESPOSTA: responda só com um objeto JSON válido que siga este JSON Schema, com todos os "
                           "campos obrigatórios (sem texto antes ou depois):\n" + json.dumps(schema, ensure_ascii=False))]

def imagem(caminho, tipo="image/jpeg"):
    return {"type": "image", "source": {"type": "base64", "media_type": tipo, "data": base64.standard_b64encode(open(caminho, "rb").read()).decode()}}

def testar():
    d = chaves.ler()
    if usa_assinatura():
        import asyncio
        try:
            r = asyncio.run(_consultar("Responda só com o JSON pedido.", [texto("Diga ok.")],
                                       {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
                                       d.get("modelo") or "claude-opus-5-5", "low", None))
            if r is None or r.is_error:
                e = erro_assinatura("; ".join((r.errors or []) if r else []) or (r.result if r else ""), getattr(r, "api_error_status", None) if r else None)
                return dict(ok=False, msg=str(e))
            return dict(ok=True, msg="conectado pela sua assinatura do Claude (Claude Code deste computador) · usa o limite do plano")
        except Exception as e:
            return dict(ok=False, msg=str(erro_assinatura(str(e))))
    if not d["anthropic"]: return dict(ok=False, msg="nenhuma chave salva")
    try:
        c = anthropic.Anthropic(api_key=d["anthropic"], max_retries=1, timeout=20.0)
        m = c.models.retrieve(d.get("modelo") or "claude-opus-5-5")
        return dict(ok=True, msg=f"chave válida · modelo {m.display_name} disponível")
    except anthropic.AuthenticationError:
        return dict(ok=False, msg="chave inválida")
    except anthropic.PermissionDeniedError:
        return dict(ok=False, msg="a chave não tem permissão para este modelo")
    except anthropic.NotFoundError:
        return dict(ok=False, msg="o modelo escolhido não está disponível nesta conta")
    except anthropic.APIConnectionError:
        return dict(ok=False, msg="sem conexão com a API")
    except anthropic.APIStatusError as e:
        return dict(ok=False, msg=f"a API respondeu {e.status_code}")

if __name__ == "__main__":
    if sys.argv[1:] == ["testar"]: print(json.dumps(testar(), ensure_ascii=False))
