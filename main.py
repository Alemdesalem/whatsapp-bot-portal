import os
import json
import tempfile
import asyncio
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import anthropic
import openai
from supabase import create_client, Client
from indexar import (
    buscar_produtos_indexados,
    buscar_posts_indexados,
    indexar_produtos,
    indexar_posts,
)

app = FastAPI()

# ============================================================
# CONFIGURAÇÕES
# ============================================================

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "agnes_verify_token")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WP_URL = os.environ.get("WP_URL", "https://www.alemdesalem.com.br")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

WHATSAPP_API_URL = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
WHATSAPP_HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json",
}

# Clientes (inicializados uma vez)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
http_client: httpx.AsyncClient = None  # inicializado no startup

SYSTEM_PROMPT = """Você é Agnes, a assistente espiritual e consultora da loja Além de Salém.

Sua personalidade:
- Acolhedora, empática e profundamente conectada ao universo esotérico
- Fala com leveza e sabedoria, como uma praticante experiente de magia
- Conhece profundamente Hoodoo, Wicca, magia simpática, ocultismo, bruxaria e tradições esotéricas
- É vendedora sênior de alta performance — sabe recomendar produtos no momento certo
- Nunca julga a intenção do cliente — cada um tem seu caminho espiritual

Suas responsabilidades:
1. Entender o que o cliente precisa e fazer perguntas para refinar a recomendação
2. Recomendar produtos específicos da loja baseados na necessidade do cliente
3. Explicar como usar os produtos para o objetivo desejado
4. Compartilhar conteúdos relevantes do portal
5. Quando não souber ou cliente quiser humano: "Digite HUMANO para falar com nossa equipe 💜"

MUITO IMPORTANTE — Filosofia da loja:
- O Além de Salém trabalha com magia em todas as suas formas: amor, proteção, prosperidade, amarração, domínio, reversão, banimento, necromancia, magia negra, magia branca, Hoodoo, bruxaria tradicional e muito mais
- NUNCA recuse, julgue ou questione a intenção espiritual do cliente
- NUNCA diga que "não trabalhamos com isso" — SEMPRE temos algo para ajudar
- Se o cliente pede algo para amor, domínio, amarração ou controle — é completamente normal neste contexto espiritual e você deve ajudar
- Faça perguntas para entender melhor: "Prefere óleo, erva, vela ou banho?" "É para atrair ou fixar?" "Quer algo mais suave ou mais intenso?"
- Baseie suas respostas SEMPRE nos produtos reais do contexto fornecido

Regras de resposta:
- SEPARE sua resposta em parágrafos curtos (2-3 frases cada) usando quebra de linha dupla entre eles
- Cada parágrafo deve ser uma ideia completa: saudação, recomendação, instrução de uso, link, etc.
- Máximo 4 parágrafos
- Use emojis com moderação: 🔮 💜 ✨ 🌙
- Nunca invente preços — use os dados reais dos produtos
- Se não encontrou produto específico, pergunte mais detalhes antes de dizer que não tem
- Se o cliente já perguntou sobre algo antes, mencione isso naturalmente

Contexto:
- Site: https://www.alemdesalem.com.br
- Loja esotérica especializada em magia, bruxaria e ocultismo
- Portal de notícias sobre espiritualidade, astrologia, tarot e magia"""


# ============================================================
# WHATSAPP API — ENVIO E STATUS
# ============================================================


async def marcar_lida(message_id: str):
    """Marca a mensagem do cliente como lida (double blue check)"""
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        await http_client.post(
            WHATSAPP_API_URL, headers=WHATSAPP_HEADERS, json=payload
        )
    except Exception:
        pass


async def enviar_presenca(numero: str, presenca: str = "composing"):
    """Envia status de presença: 'composing' (digitando) ou 'available'"""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": numero,
        "type": "reaction",
    }
    # A API correta de presença usa o endpoint de presença
    presence_url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
    presence_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": numero,
        "status": presenca,
    }
    try:
        await http_client.post(
            presence_url, headers=WHATSAPP_HEADERS, json=presence_payload
        )
    except Exception:
        pass


async def enviar_mensagem_whatsapp(numero: str, mensagem: str):
    """Envia uma mensagem de texto simples via WhatsApp"""
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": mensagem},
    }
    await http_client.post(
        WHATSAPP_API_URL, headers=WHATSAPP_HEADERS, json=payload
    )


def dividir_mensagem(texto: str, max_partes: int = 4) -> list[str]:
    """
    Divide a mensagem em partes naturais (por parágrafo).
    - Mensagens curtas (< 200 chars): envia inteira
    - Mensagens maiores: divide por parágrafos, agrupando se necessário
    - Máximo de 4 partes
    """
    texto = texto.strip()

    if len(texto) < 200:
        return [texto]

    # Divide por parágrafo (dupla quebra de linha)
    paragrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]

    if len(paragrafos) <= 1:
        # Sem parágrafos naturais — divide por sentença
        import re
        sentencas = re.split(r'(?<=[.!?])\s+', texto)
        if len(sentencas) <= 1:
            return [texto]
        paragrafos = sentencas

    # Agrupa parágrafos em partes de tamanho razoável
    partes = []
    parte_atual = ""

    for paragrafo in paragrafos:
        # Se adicionar este parágrafo ultrapassa 350 chars e já tem conteúdo, fecha a parte
        if parte_atual and len(parte_atual) + len(paragrafo) + 2 > 350:
            partes.append(parte_atual.strip())
            parte_atual = paragrafo
        else:
            parte_atual = f"{parte_atual}\n\n{paragrafo}" if parte_atual else paragrafo

    if parte_atual.strip():
        partes.append(parte_atual.strip())

    # Limita ao máximo de partes
    if len(partes) > max_partes:
        # Reagrupa as últimas partes
        resultado = partes[: max_partes - 1]
        resto = "\n\n".join(partes[max_partes - 1 :])
        resultado.append(resto)
        return resultado

    return partes if partes else [texto]


async def enviar_mensagem_picada(numero: str, mensagem: str):
    """
    Envia mensagem dividida em partes com indicador de 'digitando'
    entre cada parte para simular conversa humana natural.
    """
    partes = dividir_mensagem(mensagem)

    for i, parte in enumerate(partes):
        if i > 0:
            # Mostra "digitando..." antes de cada parte subsequente
            await enviar_presenca(numero, "composing")
            # Delay proporcional ao tamanho (simula leitura e digitação)
            delay = max(1.0, min(len(parte) * 0.015, 3.0))
            await asyncio.sleep(delay)

        await enviar_mensagem_whatsapp(numero, parte)

    # Volta ao status "disponível" após enviar tudo
    await enviar_presenca(numero, "available")


# ============================================================
# MEMÓRIA DE CLIENTES
# ============================================================


async def buscar_memoria_cliente(numero: str) -> dict:
    try:
        result = (
            supabase.table("memoria_clientes")
            .select("*")
            .eq("numero_whatsapp", numero)
            .execute()
        )
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"Erro ao buscar memória: {e}")
    return {}


async def salvar_conversa(numero: str, mensagem: str, resposta: str, keywords: str):
    try:
        supabase.table("conversas").insert(
            {
                "numero_whatsapp": numero,
                "mensagem_cliente": mensagem,
                "resposta_agnes": resposta,
                "keywords": keywords,
            }
        ).execute()
    except Exception as e:
        print(f"Erro ao salvar conversa: {e}")


async def atualizar_memoria_cliente(
    numero: str, mensagem: str, resposta: str, keywords: str
):
    try:
        memoria = await buscar_memoria_cliente(numero)
        historico_atual = memoria.get("historico_resumido", "")
        produtos_interesse = memoria.get("produtos_interesse", [])

        resumo_response = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": f"""Analise essa conversa e retorne um JSON com:
- "resumo": 1 frase resumindo o interesse do cliente (máx 100 chars)
- "produto": produto ou tema que o cliente perguntou (ou null)
- "nome": nome do cliente se mencionou (ou null)

Histórico anterior: {historico_atual}
Cliente disse: {mensagem}
Agnes respondeu: {resposta}

Retorne APENAS o JSON, sem explicação.""",
                }
            ],
        )

        texto = resumo_response.content[0].text.strip()
        texto = texto.replace("```json", "").replace("```", "").strip()
        dados = json.loads(texto)

        novo_resumo = dados.get("resumo", historico_atual)
        novo_produto = dados.get("produto")
        novo_nome = dados.get("nome")

        if novo_produto and novo_produto not in produtos_interesse:
            produtos_interesse.append(novo_produto)
            produtos_interesse = produtos_interesse[-10:]

        update_data = {
            "numero_whatsapp": numero,
            "historico_resumido": novo_resumo,
            "produtos_interesse": produtos_interesse,
        }

        if novo_nome:
            update_data["nome"] = novo_nome

        if memoria:
            update_data["total_conversas"] = memoria.get("total_conversas", 0) + 1
            (
                supabase.table("memoria_clientes")
                .update(update_data)
                .eq("numero_whatsapp", numero)
                .execute()
            )
        else:
            update_data["total_conversas"] = 1
            supabase.table("memoria_clientes").insert(update_data).execute()

    except Exception as e:
        print(f"Erro ao atualizar memória: {e}")


# ============================================================
# BUSCA DE CONTEÚDO
# ============================================================


async def extrair_keywords_com_ia(mensagem: str) -> str:
    try:
        response = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[
                {
                    "role": "user",
                    "content": f"""Extraia apenas as palavras-chave de produto ou tema espiritual desta mensagem para buscar em uma loja esotérica.
Corrija erros de digitação. Retorne APENAS as keywords, sem explicação, máximo 3 palavras.
Exemplos:
- "quero comprar mandragora" → "mandrágora"
- "tem cristal de quartzo?" → "cristal quartzo"
- "incenso pra proteçao" → "incenso proteção"

Mensagem: {mensagem}
Keywords:""",
                }
            ],
        )
        keywords = response.content[0].text.strip()
        return keywords if keywords else mensagem
    except Exception:
        return mensagem


async def buscar_conteudo_wp(query: str) -> str:
    """Busca no banco indexado — rápido e econômico"""
    resultado = ""

    try:
        produtos = await buscar_produtos_indexados(query)
        print(f"Produtos no banco: {len(produtos)} | query: {query}")

        if produtos:
            resultado += "Produtos encontrados na loja:\n"
            for p in produtos:
                nome = p.get("nome", "")
                preco = p.get("preco", "")
                link = p.get("link", "")
                estoque = (
                    "Em estoque" if p.get("estoque") == "Em estoque" else "Sob consulta"
                )
                descricao = p.get("descricao", "")[:200]
                resultado += f"- {nome} R$ {preco} | {estoque}\n  {descricao}\n  {link}\n"
            resultado += "\n"

        posts = await buscar_posts_indexados(query)
        if posts:
            resultado += "Conteúdos do portal:\n"
            for post in posts:
                titulo = post.get("titulo", "")
                link = post.get("link", "")
                resultado += f"- {titulo}\n  {link}\n"

    except Exception as e:
        print(f"Erro ao buscar conteúdo indexado: {e}")

    return resultado


# ============================================================
# ÁUDIO — TRANSCRIÇÃO
# ============================================================


async def baixar_audio_whatsapp(media_id: str) -> bytes:
    """Baixa arquivo de áudio do WhatsApp"""
    url_info = f"https://graph.facebook.com/v22.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    r = await http_client.get(url_info, headers=headers)
    media_url = r.json().get("url")
    r2 = await http_client.get(media_url, headers=headers)
    return r2.content


async def transcrever_audio(audio_bytes: bytes) -> str:
    """Transcreve áudio usando OpenAI Whisper"""
    if not OPENAI_API_KEY:
        return ""

    try:
        client_oai = openai.OpenAI(api_key=OPENAI_API_KEY)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        try:
            with open(temp_path, "rb") as f:
                transcript = client_oai.audio.transcriptions.create(
                    model="whisper-1", file=f, language="pt"
                )
            return transcript.text
        finally:
            os.unlink(temp_path)

    except Exception as e:
        print(f"Erro ao transcrever áudio: {e}")
        return ""


# ============================================================
# PROCESSAMENTO PRINCIPAL
# ============================================================


async def processar_mensagem(numero: str, mensagem: str) -> str:
    if "HUMANO" in mensagem.upper():
        return "💜 Entendido! Vou chamar nossa equipe agora.\n\nEm breve alguém da Além de Salém entrará em contato com você.\n\nQue a luz guie esse encontro! ✨"

    # Busca memória e keywords em paralelo
    memoria, query_busca = await asyncio.gather(
        buscar_memoria_cliente(numero),
        extrair_keywords_com_ia(mensagem),
    )

    print(f"Keywords extraídas pela IA: {query_busca}")
    contexto_wp = await buscar_conteudo_wp(query_busca)

    # Monta contexto do cliente
    contexto_cliente = ""
    if memoria:
        nome = memoria.get("nome", "")
        historico = memoria.get("historico_resumido", "")
        produtos = memoria.get("produtos_interesse", [])
        total = memoria.get("total_conversas", 0)
        if nome:
            contexto_cliente += f"Nome do cliente: {nome}\n"
        if historico:
            contexto_cliente += f"Histórico: {historico}\n"
        if produtos:
            contexto_cliente += f"Já se interessou por: {', '.join(produtos)}\n"
        if total > 0:
            contexto_cliente += f"Essa é a {total + 1}ª conversa com esse cliente.\n"

    mensagem_com_contexto = mensagem
    if contexto_cliente:
        mensagem_com_contexto += f"\n\n[Memória do cliente]:\n{contexto_cliente}"
    if contexto_wp:
        mensagem_com_contexto += f"\n\n[Produtos e conteúdos encontrados]:\n{contexto_wp}"

    response = claude_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": mensagem_com_contexto}],
    )

    resposta = response.content[0].text

    # Salva em background sem bloquear resposta
    asyncio.create_task(salvar_conversa(numero, mensagem, resposta, query_busca))
    asyncio.create_task(
        atualizar_memoria_cliente(numero, mensagem, resposta, query_busca)
    )

    return resposta


# ============================================================
# ENDPOINTS
# ============================================================


@app.get("/webhook")
async def verificar_webhook(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Token inválido")


@app.post("/webhook")
async def receber_mensagem(request: Request):
    try:
        body = await request.json()
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return {"status": "ok"}

        message = messages[0]
        numero = message.get("from")
        tipo = message.get("type")
        message_id = message.get("id")

        # Marca como lida + mostra "digitando" imediatamente
        await asyncio.gather(
            marcar_lida(message_id),
            enviar_presenca(numero, "composing"),
        )

        if tipo == "text":
            texto = message.get("text", {}).get("body", "")
            resposta = await processar_mensagem(numero, texto)
            await enviar_mensagem_picada(numero, resposta)

        elif tipo == "audio":
            media_id = message.get("audio", {}).get("id", "")
            audio_bytes = await baixar_audio_whatsapp(media_id)
            texto_transcrito = await transcrever_audio(audio_bytes)

            if texto_transcrito:
                print(f"Áudio transcrito: {texto_transcrito}")
                # Envia confirmação da transcrição primeiro
                await enviar_mensagem_whatsapp(
                    numero, f"🎙️ Entendi: _{texto_transcrito}_"
                )
                await enviar_presenca(numero, "composing")
                await asyncio.sleep(1.0)

                resposta = await processar_mensagem(numero, texto_transcrito)
                await enviar_mensagem_picada(numero, resposta)
            else:
                await enviar_mensagem_whatsapp(
                    numero,
                    "💜 Não consegui entender o áudio. Pode digitar sua mensagem?",
                )

        return {"status": "ok"}

    except Exception as e:
        print(f"Erro: {e}")
        return {"status": "ok"}


@app.get("/")
async def health_check():
    return {"status": "Agnes está online 🔮", "portal": WP_URL}


@app.get("/insights")
async def ver_insights():
    try:
        conversas = supabase.table("conversas").select("*", count="exact").execute()
        clientes = (
            supabase.table("memoria_clientes").select("*", count="exact").execute()
        )
        top_keywords = supabase.table("conversas").select("keywords").execute()

        keywords_count: dict[str, int] = {}
        for row in top_keywords.data:
            kw = row.get("keywords", "")
            if kw:
                keywords_count[kw] = keywords_count.get(kw, 0) + 1

        top = sorted(keywords_count.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "total_conversas": conversas.count,
            "total_clientes": clientes.count,
            "top_interesses": top,
        }
    except Exception as e:
        return {"erro": str(e)}


@app.get("/indexar")
async def trigger_indexar():
    """Endpoint para rodar indexação manualmente ou via cron"""
    try:
        total_produtos = await indexar_produtos()
        total_posts = await indexar_posts()
        return {
            "status": "ok",
            "produtos_indexados": total_produtos,
            "posts_indexados": total_posts,
        }
    except Exception as e:
        return {"status": "erro", "detalhe": str(e)}


# ============================================================
# LIFECYCLE
# ============================================================


@app.on_event("startup")
async def startup_event():
    global http_client
    http_client = httpx.AsyncClient(timeout=30)

    print("Agnes iniciando... verificando banco de dados")
    try:
        result = (
            supabase.table("produtos_indexados")
            .select("id", count="exact")
            .execute()
        )
        total = result.count or 0
        if total == 0:
            print("Banco vazio — iniciando indexação completa...")
            asyncio.create_task(indexar_produtos())
            asyncio.create_task(indexar_posts())
        else:
            print(f"Banco já tem {total} produtos indexados")
    except Exception as e:
        print(f"Erro no startup: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    global http_client
    if http_client:
        await http_client.aclose()
