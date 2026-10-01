# Ad Animado

Transforma uma copy de anúncio (locução ou fala do personagem) + imagens de referência num ad animado pronto:

1. **Áudio e SRT**: o SRT sai do áudio sozinho (Whisper), quebrado no ritmo que você escolher (ex.: 2 a 3 s por clipe).
2. **Vídeo de referência** (opcional): estuda um anúncio de base e aplica no nível de semelhança de 1 a 5.
3. **Storyboard**: o Claude escreve um beat por bloco do SRT (hook, corpo, produto), com prompts detalhados.
4. **Imagens**: Grok (pela assinatura) ou KIE (Nano Banana, Seedream, GPT Image…), todas ao mesmo tempo.
5. **Conferência**: o Claude olha cada imagem contra o storyboard e marca o que refazer.
6. **Vídeos**: Grok, Kling, Seedance, Hailuo, Wan ou Veo, gerados em lote. Modo *cortes* ou *contínuo* (plano-sequência com quadros-chave e transições).
7. **Montagem**: timeline batendo com o SRT, com a locução por baixo, e `final.mp4` pronto para baixar.

É o mesmo módulo "Ad Animado" do Atlas Editor, como app independente, com chaves e ads próprios.

## Instalar (Mac)

Precisa do [Homebrew](https://brew.sh). No Terminal, dentro da pasta do app:

```bash
./instalar.sh
```

Ou direto do GitHub (se o repositório for privado, entre antes com `gh auth login`):

```bash
gh repo clone USUARIO/ad-animado ~/"Ad Animado" && cd ~/"Ad Animado" && ./instalar.sh
```

O instalador cria o ícone **Ad Animado** na área de trabalho. Depois é só clicar nele, ou:

```bash
cd ~/"Ad Animado" && ./iniciar.sh      # abre http://localhost:4124
cd ~/"Ad Animado" && ./parar.sh        # encerra (uma geração em andamento continua)
```

Para atualizar: `cd ~/"Ad Animado" && git pull && ./instalar.sh`.

## Primeira vez: ⚙ Configurações

| O quê | Para quê | Como |
|---|---|---|
| **Claude** | storyboard, prompts, conferência | Pela **assinatura** (Pro/Max): precisa do Claude Code instalado e logado no Mac (`claude` no Terminal). Se o teste falhar, rode `claude setup-token` e cole o token. Ou use uma chave da API. |
| **Grok** | imagens e vídeos | Pela **assinatura**: rode `grok` no Terminal uma vez e entre com a sua conta (usa a cota semanal, sem cobrança por uso). Ou use uma chave da API da xAI. |
| **KIE** | Nano Banana, Seedream, Kling, Seedance, Veo… | Chave em kie.ai › API Key (cobra créditos). |
| **Gemini** | opcional: assistir o vídeo de referência | Chave do Google AI Studio (tem plano gratuito). Sem ela, o Claude estuda a referência pelos quadros. |

Cada pessoa da equipe usa as **próprias** contas: as chaves ficam só no computador de quem configurou.

## Onde ficam as coisas

- Ads: `~/Ads Animados/<nome do ad>/` (imagens, vídeos, `final.mp4`, SRT, custos)
- Chaves: `~/.config/ad-animado/chaves.json` (permissão 600, nunca vai para o GitHub)
- Registro do servidor: `~/.ad-animado.log`

A porta (4124) e a pasta dos ads podem ser trocadas em `config.json`.

## Testes

```bash
.venv/bin/python -m unittest discover -s tests
```
