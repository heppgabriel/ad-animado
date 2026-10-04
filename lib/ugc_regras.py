"""Regras do UGC ultra-realista (skills omni-ugc-director e troca-rosto-pinterest, do Gabriel).
Os blocos travados (LOCKS) entram no prompt LITERALMENTE pelo código: o Claude só escreve o que muda de clipe
para clipe (cena, beats, negativas específicas). Assim a estrutura, a voz e as travas nunca variam."""

TAXAS = {"calmo": 2.35, "rapido": 2.9}                  # palavras por segundo em pt-BR (acima de 2,6 embola)
DURACOES = (4, 6, 8, 10)                                # o Omni Flash gera 4, 6, 8 ou 10 s

LANGUAGE_LOCK = ("LANGUAGE LOCK: All spoken dialogue MUST be in Brazilian Portuguese (pt-BR). Do NOT speak English. "
                 "Do NOT translate the dialogue.")

def completeness_lock(pron):
    return (f"COMPLETENESS LOCK — CRITICAL: {pron['S']} must speak the ENTIRE dialogue line below, word for word, from the very first "
            "word to the very last word. Do NOT skip, shorten, summarize, paraphrase, merge or cut any word or sentence. Do NOT drop "
            "the opening words. Do NOT drop the final words. Do NOT trail off, fade out or stop early. Do NOT improvise or add words "
            "that are not written. If the line is long, speak at a slightly faster natural pace but deliver 100% of it, and fully "
            "finish the last word before the clip ends.")

def no_speech_lock(pron):
    return (f"NO SPEECH LOCK — CRITICAL: {pron['S']} does not speak a single word in this clip. {pron['P']} lips stay closed and still "
            "the entire time. No talking, no mouthing, no lip movement, no whispering. There is no dialogue and no voice in this "
            "clip at all. " + f"{pron['S']} is only listening.")

def identity_lock(nome, pron, tracos):
    return (f"IDENTITY LOCK — HIGHEST PRIORITY: This is {nome}. {pron['S']} is the exact same {pron['pessoa']} as in @image1 and nobody "
            f"else. Copy {pron['O']} from @image1 feature by feature: {tracos}. {pron['S']} must look recognisably identical in the "
            "first frame and in the last frame.")

IMAGE_QUALITY_LOCK = ("Image quality lock: same lighting, texture and amateur camera quality as @image1. Do not sharpen. Do not smooth "
                      "skin. No beauty filter, no HDR, no color grade. Raw, organic, slightly soft amateur home-video texture with mild "
                      "digital noise and flat colors.")

def camera_fixa(pron, posicao, enquadramento):
    return (f"Camera: Absolutely fixed shot {posicao}, vertical 9:16, {enquadramento}, at {pron['P'].lower()} eye level. The frame is "
            "completely locked — zero camera movement, no drift, no float, no shake, no panning, no tilting, no reframing, no "
            "push-in. Every edge of the frame stays in exactly the same place from the first frame to the last. One continuous "
            "take, no cuts, no zooms.")

def camera_selfie(pron, enquadramento):
    return (f"Camera: This is a selfie — the camera is held in {pron['P'].lower()} raised hand, held steady at chest height. Vertical "
            f"9:16, {enquadramento}. Movement is only the tiny natural sway of a hand holding the camera still — a few millimetres "
            "of drift, barely perceptible. No walking, no reframing, no panning, no tilting, no push-in. One continuous take, no "
            "cuts, no zooms.")

def audio_fala(pron, voz, ambiencia, fala):
    return (f"Audio: {voz}. Close, slightly compressed voice recording, {ambiencia}. Natural speech rhythm with small pauses. "
            f"{pron['S']} speaks in Brazilian Portuguese (pt-BR) only, never English, and says every word below exactly as written, "
            f"finishing the final word before the clip ends:\n\"{fala}\"")

def audio_mudo(ambiencia):
    return f"Audio: No voice, no dialogue, no speech of any kind. Only quiet room tone — {ambiencia}, one faint breath."

def do_not(pron, extra):
    base = (f"Do not: No English speech. No text overlays, captions, subtitles or logos. {pron['P']} face and body must stay identical "
            "to @image1 in every frame — no face morphing, no body morphing, no change in " + pron['P'].lower() + " build, shoulders or "
            f"proportions, no identity drift, no swapping to a different {pron['pessoa']}, no beautifying. No warped hands or extra "
            f"fingers. {pron['S']} stays in the same position: no standing, no leaning in or out, no turning, no repositioning. Do not "
            f"change {pron['P'].lower()} wardrobe or the setting from the reference. The camera is completely fixed: no drift, panning, "
            "tilting, swinging or reframing. No skipped words. No truncated or unfinished sentences. No paraphrasing. No summarizing. "
            "No dropped opening or closing words. The final word must be fully pronounced.")
    return base + ((" " + extra.strip()) if extra and extra.strip() else "")

def do_not_mudo(pron, extra):
    return ("Do not: No speech. No talking. No lip movement. No mouthing words. No dialogue or voice in the audio. No text overlays, "
            f"captions, subtitles or logos. {pron['P']} face and body must stay identical to @image1 in every frame — no face morphing, "
            "no identity drift, no beautifying. No warped hands or extra fingers. The camera is completely fixed."
            + ((" " + extra.strip()) if extra and extra.strip() else ""))

PRONOMES = {"f": dict(S="She", O="her", P="Her", pessoa="woman"), "m": dict(S="He", O="him", P="His", pessoa="man")}

PROIBIDAS = ("cinematic",)                               # nunca, nem em negativa

# ---------------------------------------------------------------- instruções para o Claude
REGRAS_OMNI = """Você é diretor de anúncios UGC gerados por IA (A-roll no Google Omni Flash, B-roll no Kling). Regras que NÃO se negociam:
- NÃO PODE PARECER IA. Vídeo de celular de verdade é levemente imperfeito; quanto mais polido, pior.
- A imagem inicial (@image1) decide 60–70% do realismo. Tudo que você escrever tem que estar ANCORADO no que existe na imagem:
  não invente cenário, roupa, objeto ou pose que não está nela (o modelo entra em conflito e troca a pessoa).
- Nunca escreva a palavra "cinematic" (nem em negativa).
- Negação atrai o objeto: escrever "no phone" faz aparecer celular. Remova o substantivo e descreva o positivo
  ("her hands are open and empty, resting in her lap").
- Congelar tudo deforma o corpo: em vez de "frozen", limite o que mexe: "Only her face moves: expression, mouth, eyes, eyebrows,
  plus the natural micro-settling of someone sitting still." E repita em cada beat "body still, only her face moves".
- Uma ação por clipe. Um arco só (nunca "push in then pan").
- Mãos soltas no ar deformam: ancore as mãos em objetos ou no colo.
- O roteiro é TRAVADO: nunca reescreva, resuma ou corrija a fala. Se um claim for forte demais (cura, garantia), apenas avise.
- Copie o MECANISMO da referência (hook, casting, ritmo, tom), não os visuais.
Todos os textos que vão dentro dos prompts são em INGLÊS; as observações para o usuário em português."""

INSTR_CENA = REGRAS_OMNI + """

TAREFA: descrever a imagem inicial de uma cena para travar a identidade e o cenário nos prompts do Omni. Olhe a imagem e escreva
(em inglês, concreto, sem adjetivos de beleza):
- tracos: a lista feature a feature para o IDENTITY LOCK, no formato "same face shape and jawline, same nose, same mouth, same eye
  shape and spacing, same eyebrows, same skin tone and texture, same age lines (<marcadores reais de idade que você vê>), same
  hairstyle and hair colour (<cor e penteado>), same body build and shoulder width, same <roupa exata>".
- cenario: uma frase do cenário e da postura ancorada na imagem (onde está, sentada/em pé, o que as mãos fazem e onde apoiam).
- pode_mexer: o que pode se mexer sem conflito (normalmente só o rosto) e o que fica parado.
- camera_tipo: "fixa" (celular apoiado) ou "selfie" (braço segurando a câmera visível/óbvio).
- posicao: posição da câmera para o bloco Camera ("from a phone propped on the kitchen counter in front of her").
- enquadramento: "framing her from the chest up" / "framing her face and shoulders" etc.
- ambiencia: ambiência sonora do lugar ("quiet kitchen room tone, faint fridge hum").
- genero: "f" ou "m".
- tem_produto: se um produto/embalagem aparece na imagem.
- avisos: problemas da imagem que vão estragar o vídeo (boca aberta, mão no ar, rosto cortado, texto/marca visível, cara de estúdio),
  em português. Lista vazia se estiver boa."""

INSTR_PLANO = REGRAS_OMNI + """

TAREFA: dirigir os clipes de um UGC. Os clipes JÁ VÊM empacotados pelo código (fala de cada um travada, duração calculada pela taxa de
fala). Você NÃO mexe nas falas nem na ordem. Para cada clipe você decide:
- cena: qual imagem inicial usar (o id da cena). Use as cenas de forma coerente com o roteiro (hook, dor, prova, produto, CTA) e com o
  que cada imagem mostra; a mesma cena pode servir vários clipes seguidos.
- beats: 2 beats (clipes até 6 s) ou 3 beats (8–10 s), cada um "[0:00-0:03] <expressão/ação do rosto>, body still, only her face moves".
  A expressão acompanha o sentido da fala (dúvida, indignação, alívio, cumplicidade). Nada de gesto grande.
- nao_extra: negativas específicas deste clipe (em inglês, sem usar o substantivo proibido; vazio se não precisar).
Também:
- voz: UMA descrição de voz para o personagem, usada igual em todos os clipes (textura + entrega), ex.: "Warm calm knowing mature
  Brazilian Portuguese female voice, reassuring best-friend tone with light humour".
- analise: em português, 3 a 6 linhas: mecanismo da referência (por que venceu) e como o ad copia o mecanismo.
- avisos: em português, claims fortes demais no roteiro, falas que vão embolar, cena faltando. Lista vazia se nada.
Se o B-ROLL estiver ligado, proponha também broll: clipes de B-roll (produto, mãos, lifestyle) que COBREM o visual de um clipe de
A-roll (a fala continua por baixo). Para cada um:
- cobre: n do clipe de A-roll coberto; dur: 3 a 8 s (não maior que o clipe coberto).
- imagem_prompt: prompt (inglês) para gerar a imagem inicial do B-roll num gerador de imagem a partir das imagens anexadas (cena e
  produto): foto amadora de celular, mãos ancoradas em objetos, rótulo nítido, "a random frame paused from a video".
- prompt: prompt do Kling no formato:
  "<UM movimento de câmera + duração>.\n\n<o que o sujeito/objeto FAZ em sequência, com física, usando 'then'>.\n\n<se tem produto:
  label text \\"MARCA\\" remains stable and readable throughout.>\n\nHandheld phone feel, natural light, visible texture.\n\nAvoid: <3 a 5
  falhas específicas>." Use no máximo 1 B-roll a cada 3 clipes."""

INSTR_TROCA = """Você escreve prompts de TROCA DE ROSTO (skill troca-rosto-pinterest): um frame real (Pinterest) vira molde de cenário,
luz, pose, roupa e textura de celular, e a avatar entra dentro dele. Referência real -> imagem real -> vídeo real.
Regras:
- A pessoa original não pode continuar reconhecível; a cena é o molde e a identidade é 100% nova.
- Nunca escreva "cinematic".
- Liste de forma CONCRETA o que fica (pose, mãos, roupa, acessórios, móveis, fundo, enquadramento, direção da luz). "Keep everything
  else" sozinho não segura.
- Não descreva nada que não está na imagem — EXCETO o pedido extra do usuário (ex.: segurar o produto): aí descreva exatamente como a
  mão segura o produto anexado, rótulo virado para a câmera, nítido e legível, tamanho real, mão ancorada.
- Negação atrai o objeto: texto identificável (crachá, carimbo, tela) vai em POSITIVO ("the badge is plain and blank").
- Idade por marcadores (linhas nos olhos e boca, poros, fios brancos misturados), nunca só o número.
- Nitidez do rosto = nitidez da foto. Boca fechada ou sorriso fechado (a imagem vira start frame de A-roll com lip-sync), olhando para a lente.
- Personagem com jaleco/estetoscópio em anúncio de saúde: avise (em português) que apresentar pessoa gerada por IA como médico real
  pode derrubar o anúncio e violar regras do CFM/ANVISA.
Formato do prompt (Nano Banana Pro, inglês), com image 1 = cena, image 2 = avatar, image 3 = produto (se houver):
"Edit image 1. Replace the <man/woman>'s face and hair in image 1 with the face and hair of the <man/woman> in image 2. Keep everything
else from image 1 exactly as it is: <lista concreta>, same framing and crop, same lighting direction and colour, same camera angle.

Identity: <he/she> must be the exact person from image 2, feature by feature — same face shape and jawline, same nose, same mouth,
same eye shape and spacing, same eyebrows, same skin tone and texture, same age lines, same hairstyle and hair colour. The original
person from image 1 must not be recognisable.

Blend: lit exactly like the scene in image 1 — <luz real da cena>, same shadow side, same colour temperature. Face, neck and hands
have the same skin tone. Head in natural proportion to the body. Seamless hairline and neck transition.

Expression: relaxed neutral expression or gentle closed-mouth smile, lips together, eyes looking straight into the lens.

Texture: keep the amateur phone-camera quality of image 1 — same grain, same slight softness, same natural colours. Visible skin
texture, pores, fine lines. The face is exactly as sharp as the rest of the photo. Do not sharpen. Do not smooth skin. No beauty
filter, no HDR, no colour grade.

<Details: pedido extra (produto na mão) e textos em branco, em positivo>

Do not: change the clothing, pose, framing or background. Do not make <him/her> look younger or more attractive. No plastic or
airbrushed skin. No text, logos or watermarks."
Sem avatar (rosto novo descrito pelo usuário): troque a 1ª linha por "Replace the <man/woman>'s face and hair with a completely
different <...>" e o bloco Identity por "New face:" com traços feature a feature e marcadores reais de idade.
Modo "pessoa inteira" (biotipo muito diferente): "Place the person from image 2 into the scene of image 1, in exactly the same pose,
position, framing and crop..." + "same body build and shoulder width"."""
