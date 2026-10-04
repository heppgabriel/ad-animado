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

## Instalar

### Mac

Abra o **Terminal** (Cmd + Espaço, digite "Terminal"), cole e aperte Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/heppgabriel/ad-animado/main/instalar.sh | bash
```

Se faltar o Homebrew, instale antes pelo site [brew.sh](https://brew.sh) e rode o comando de novo.

### Windows

Abra o **PowerShell** (tecla Windows, digite "PowerShell"), cole e aperte Enter:

```powershell
irm https://raw.githubusercontent.com/heppgabriel/ad-animado/main/instalar.ps1 | iex
```

Ele instala o que faltar (Git, Python 3.12, ffmpeg) pelo winget. Se pedir para fechar e abrir o PowerShell de novo, faça isso e rode o mesmo comando outra vez.

### Depois de instalar

Na primeira vez demora alguns minutos. No fim, o app abre sozinho no navegador (http://localhost:4124) e aparece o atalho **Ad Animado** na área de trabalho. Da próxima vez, é só clicar nele.

Para atualizar, rode o mesmo comando de instalação de novo. Os ads e as chaves nunca são apagados.

## UGC ultra-realista (página /ugc)

Avatar falando para a câmera, com cara de vídeo de celular de verdade, seguindo as skills **omni-ugc-director** e **troca-rosto-pinterest**:

1. Cole o roteiro (fica travado, palavra por palavra) e envie uma ou mais **imagens iniciais** (frames reais, de preferência do Pinterest).
2. Opcional: envie a **foto da avatar** e marque em quais cenas montar ela (troca de rosto no Nano Banana Pro), com pedidos como "segurando o produto". Sem foto, dá para descrever um rosto novo.
3. O Claude lê cada imagem e o vídeo de referência, divide a fala em clipes de 4 a 10 s pela velocidade da fala (auditoria de integridade: roteiro = soma das falas) e escreve os prompts do **Gemini Omni Flash** com todas as travas.
4. Os vídeos saem pela **KIE** ou pelo **Google AI Studio** (você escolhe), todos ao mesmo tempo. Opcional: **B-roll** no Kling cobrindo partes da fala.
5. Assista e aprove um por um; troque a cena de qualquer clipe ou a imagem de qualquer cena (os prompts se reescrevem sozinhos); refaça o que não ficou bom.
6. Baixe a **pasta com os clipes numerados** (.zip) ou **todos juntos** num vídeo só.

Os UGCs ficam em `Ads Animados/UGC/<nome>`.

## Primeira vez: ⚙ Configurações

| O quê | Para quê | Como |
|---|---|---|
| **Claude** | storyboard, prompts, conferência | Pela **assinatura** (Pro/Max): precisa do Claude Code instalado e logado no computador (`claude` no Terminal/PowerShell). Se o teste falhar, rode `claude setup-token` e cole o token. Ou use uma chave da API. |
| **Grok** | imagens e vídeos | Pela **assinatura**: precisa do Grok CLI instalado e logado (rode `grok` uma vez e entre com a sua conta; usa a cota semanal, sem cobrança por uso). Sem ele, use uma chave da API da xAI ou a KIE. |
| **KIE** | Nano Banana, Seedream, Kling, Seedance, Veo, Gemini Omni… | Chave em kie.ai › API Key (cobra créditos). |
| **Gemini** | opcional: assistir o vídeo de referência; Omni pelo Google no UGC | Chave do Google AI Studio (tem plano gratuito para o estudo; o Omni não tem). Sem ela, o Claude estuda a referência pelos quadros. |

Cada pessoa da equipe usa as **próprias** contas: as chaves ficam só no computador de quem configurou.

## Onde ficam as coisas

- Ads: pasta `Ads Animados` dentro da pasta pessoal (`~/Ads Animados` no Mac, `C:\Users\<você>\Ads Animados` no Windows) (imagens, vídeos, `final.mp4`, SRT, custos)
- Chaves: `.config/ad-animado/chaves.json` dentro da pasta pessoal (permissão 600, nunca vai para o GitHub)
- Registro do servidor: `~/.ad-animado.log`

A porta (4124) e a pasta dos ads podem ser trocadas em `config.json`.

## Testes

```bash
.venv/bin/python -m unittest discover -s tests        # Windows: .venv\Scripts\python -m unittest discover -s tests
```
