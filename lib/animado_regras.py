"""O que o Claude sabe ao escrever os prompts do Ad Animado: as regras do anúncio (hook, corpo, produto, CTA) e como cada
IA de imagem e de vídeo responde melhor. Tudo em texto, para entrar direto nas instruções de animado.py.

Fontes (pesquisa de 2026-09): guia oficial do Nano Banana Pro (Google), guia oficial do Veo 3.1 (Google Cloud), guias de
Kling 3.0 (fal), Seedance 2.0 (guia oficial BytePlus, resumido), Grok Imagine Video 1.5 (Scenario), posts de criadores no X
(Kling premia prompt curto; Seedance recusa start frame com rosto realista reconhecível), e estudos de hook/hold rate de
anúncios (Adligator, Pixis, hook stacking). Revise quando os modelos mudarem."""

REGRAS_AD = """REGRAS DO ANÚNCIO (valem para todo ad; são mais importantes que qualquer regra de estilo):

HOOK (os beats que caem nos primeiros ~3 s da fala — normalmente o 1º e o 2º):
- Tem que parar o dedo. O primeiro quadro já é o momento mais forte, nunca uma cena de apresentação ("plano geral calmo
  do personagem na sala" é proibido no hook).
- Mostre a DOR de forma VISUAL e concreta, de preferência como METÁFORA física exagerada que qualquer um entende sem som:
  intestino preso = mangueira toda torcida e estufada dentro de uma barriga translúcida; cansaço = o personagem derretendo na
  cadeira como cera; inchaço = botão da calça estourando em câmera lenta; ansiedade = o personagem sendo espremido por
  paredes que se fecham. A imagem traduz a fala, não ilustra a palavra ao pé da letra.
- Abra CURIOSIDADE: mostre a consequência antes da causa, algo estranho/fora do lugar, uma escala impossível (um objeto do
  dia a dia gigante, o personagem minúsculo), um close extremo que ainda não revela o todo, um "o que é isso?" visual. A
  pessoa precisa querer ver o próximo corte para entender.
- Quebra de padrão: ângulo incomum (de cima, de dentro do corpo, POV), contraste forte de cor, movimento já no quadro 1.
- Nunca mostre o produto no hook. Nunca comece com logo, texto ou pessoa falando parada.

CORPO:
- Cada beat traz uma informação visual NOVA — nada de repetir o mesmo enquadramento em beats seguidos.
- Alterne os trilhos (AVATAR / B-ROLL / PRODUTO) e as escalas (close, médio, detalhe, macro, dentro do corpo).
- Escale a tensão até a virada e deixe a virada clara (pares espelhados antes/depois com o mesmo cenário e enquadramento).
- A cada ~5 s um novo "gancho" visual (uma revelação, um zoom num detalhe, uma transformação) para segurar quem ficou.
- O MECANISMO vira imagem concreta e específica (a molécula, o órgão, a camada, o antes/depois celular), nunca "ciência genérica".

PRODUTO (físico ou digital) — o mais específico e detalhista possível:
- Físico: formato exato da embalagem (pouch/pote/frasco/caixa), material (fosco, brilhante, vidro âmbar), cor exata, onde
  ficam marca e nome, tampa, textura do conteúdo (cápsula, pó, gota), mão real interagindo, o ritual de uso (abrir, pingar,
  misturar) e o momento do dia. Só marca e nome nítidos; o resto da arte pequeno/desfocado.
- Digital (app, curso, protocolo, e-book, método): NUNCA "um celular mostrando um app" genérico. Concretize: a tela
  específica (o módulo com nome, a checklist do dia 1 com o primeiro item marcado, o gráfico subindo), o entregável como
  objeto tátil (guia impresso de 21 dias com abas coloridas, cardápio pregado na geladeira, cronograma na mesa), o bônus
  como objetos separados e nomeados, e o RESULTADO de usar (a pessoa cumprindo o passo, a balança, o prato montado).
- Detalhe o que a fala promete: se diz "7 minutos por dia", mostre um timer de 7 min; se diz "3 ingredientes", os 3.

CTA: ação física e específica (o polegar tocando no botão, a caixa chegando na porta, o acesso liberado na tela), nunca
uma pose genérica."""

GUIA_IMAGEM = """COMO ESCREVER O PROMPT DE IMAGEM (o que funciona nos modelos atuais — Nano Banana, Grok Imagine, Seedream, GPT Image):
- Escreva como um briefing para um artista, em frases completas em inglês — nada de "tag soup" (dog, park, 4k, realistic).
- Ordem: enquadramento e lente/ângulo -> sujeito com os traços que identificam -> ação/emoção NO PICO -> cenário com 2 ou 3
  objetos concretos -> luz (qualidade, direção, temperatura de cor) -> paleta/clima -> composição 9:16.
- Com referência: diga explicitamente "keep the character's face, hairstyle and outfit exactly the same as the reference
  image"; varie pose/expressão/ângulo, nunca a identidade. Pack: "same package shape, colors and label layout as the reference".
- Estilo 3D animado: ancore em materiais e luz ("soft subsurface skin, clay-like finish, soft warm key light, gentle rim light")
  em vez de só "Pixar style"; expressões exageradas e legíveis (olhos grandes, sobrancelhas marcadas), proporções que leem
  bem no celular; cenário mais simples quando a identidade é prioridade.
- A imagem é o PRIMEIRO QUADRO de um vídeo: congele o instante logo antes ou no meio da ação (mão a caminho, objeto no ar,
  expressão mudando), com espaço para a câmera se mover; nunca pose parada de foto de catálogo.
- Composição vertical: sujeito principal no terço de cima/meio; o terço de baixo mais limpo (a legenda entra ali depois).
- O que não quer, diga de forma positiva ("clean empty background" em vez de "no clutter"); não peça texto/legenda na
  imagem (exceção: marca/nome do pack e telas de produto digital descritas com poucas palavras grandes).
- Específico > genérico: "a crumpled beige linen shirt" e não "a shirt"; "a white matte jar with a green lid" e não "a jar"."""

# Como cada família de modelo de vídeo lê o prompt de animação.
GUIAS_VIDEO = {
    "grok": """Grok Imagine Video: descreva SÓ o que se move; frase curta; UM movimento de câmera por clipe (push in, pull back,
pan, tilt, orbit ou track); verbos fortes e específicos (lifts, bursts, shatters, drifts, slumps). Não reescreva a composição.
Fala entre aspas para sincronizar a boca; efeitos e ambiente depois da fala; "no music" quando não quiser trilha.""",
    "kling": """Kling: prêmio para a brevidade — prompt curto, poucas palavras, ação clara. Trate a imagem como âncora e descreva
como a cena EVOLUI a partir dela ("as", "then", "slowly"); um movimento de câmera explícito (tracking, slow push-in, pan,
slow reveal); não repita o que já está na imagem nem textos/placas dela.""",
    "seedance": """Seedance: fórmula [sujeito] + [ação] + [câmera] + [estilo] + avoid [restrições]; UMA instrução de câmera principal
(push-in, pull-out, pan, tracking, orbit, handheld, fixed); luz descrita com precisão; menos de 100 palavras; só movimento
(a aparência já está no start frame) e "preserve composition and colors"; termine com "avoid jitter and bent limbs".
Não misture movimento de câmera e do sujeito na mesma frase; nada de "fast" sem dizer o quê.""",
    "veo": """Veo 3.1: fórmula [cinematografia] + [sujeito] + [ação] + [contexto] + [estilo e ambiente]; termos de câmera (dolly,
tracking, crane, slow pan, POV, close-up, shallow depth of field). Fala no formato: The character says, "…". Efeitos como
"SFX: …" e ambiente como "Ambient noise: …". O que não quer, descreva positivamente (não "no X").""",
    "hailuo": """Hailuo: frase curta e direta de movimento + um movimento de câmera; ação física clara desde o início.""",
    "wan": """Wan: descreva a ação principal e a câmera em uma ou duas frases; evite muitos elementos simultâneos.""",
}

def familia(mid):
    """grok | kling | seedance | veo | hailuo | wan a partir do id do modelo de vídeo (grok, kie:kling-2.6/…, …)."""
    m = (mid or "grok").lower()
    for chave in ("seedance", "kling", "veo", "hailuo", "wan"):
        if chave in m: return chave
    return "grok"

def guia_video(mid): return GUIAS_VIDEO[familia(mid)]

def guia_imagem_modelo(mid):
    m = (mid or "grok").lower()
    if "nano-banana" in m: return "Modelo de imagem: Nano Banana (Google) — entende briefing longo em linguagem natural e muitas referências; cite as referências pelo papel (the character reference, the product reference)."
    if "seedream" in m: return "Modelo de imagem: Seedream — prompt claro e direto, até ~3000 caracteres; descreva o que mudar em relação à referência."
    if "gpt-image" in m: return "Modelo de imagem: GPT Image — obedece a instruções detalhadas; seja explícito sobre composição e o que manter da referência."
    return "Modelo de imagem: Grok Imagine — prompt enxuto e visual, 3 a 6 frases; o essencial primeiro."

MOVIMENTO_POR_PAPEL = """MOVIMENTO POR PAPEL DO BEAT:
- HOOK: a ação mais forte do anúncio começa no PRIMEIRO QUADRO (quebra de padrão: crash zoom / snap push-in, algo estoura,
  cai, se transforma, o personagem reage exagerado). Nada de começo lento.
- CORPO: movimento contínuo e legível (gesto do personagem, câmera lenta de push-in ou pan) — nunca clipe parado.
- MECANISMO (B-ROLL): o processo acontecendo (a coisa se desentupindo, a célula se reparando, a molécula encaixando) com
  câmera lenta orbit/push-in.
- PRODUTO: slow orbit ou push-in no pack com mão real interagindo; a trava de embalagem sempre.
- CTA: o gesto específico (dedo tocando no botão, caixa sendo aberta) logo no início."""
