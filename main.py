import os
import json
import httpx
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import anthropic
from supabase import create_client, Client
from indexar import buscar_produtos_indexados, buscar_posts_indexados, indexar_produtos, indexar_posts
import openai

app = FastAPI()

# Configurações
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "agnes_verify_token")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WP_URL = os.environ.get("WP_URL", "https://www.alemdesalem.com.br")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Clientes
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
- Mensagens curtas e objetivas — máximo 3 parágrafos
- Use emojis com moderação: 🔮 💜 ✨ 🌙
- Nunca invente preços — use os dados reais dos produtos
- Se não encontrou produto específico, pergunte mais detalhes antes de dizer que não tem
- Se o cliente já perguntou sobre algo antes, mencione isso naturalmente

Contexto:
- Site: https://www.alemdesalem.com.br
- Loja esotérica especializada em magia, bruxaria e ocultismo
- Portal de notícias sobre espiritualidade, astrologia, tarot e magia"""


# ============================================================
# MEMÓRIA DE CLIENTES
# ============================================================

async def buscar_memoria_cliente(numero: str) -> dict:
    try:
        result = supabase.table("memoria_clientes").select("*").eq("numero_whatsapp", numero).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"Erro ao buscar memória: {e}")
    return {}


async def salvar_conversa(numero: str, mensagem: str, resposta: str, keywords: str):
    try:
        supabase.table("conversas").insert({
            "numero_whatsapp": numero,
            "mensagem_cliente": mensagem,
            "resposta_agnes": resposta,
            "keywords": keywords
        }).execute()
    except Exception as e:
        print(f"Erro ao salvar conversa: {e}")


async def atualizar_memoria_cliente(numero: str, mensagem: str, resposta: str, keywords: str):
    try:
        memoria = await buscar_memoria_cliente(numero)
        historico_atual = memoria.get("historico_resumido", "")
        produtos_interesse = memoria.get("produtos_interesse", [])

        resumo_response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"""Analise essa conversa e retorne um JSON com:
- "resumo": 1 frase resumindo o interesse do cliente (máx 100 chars)
- "produto": produto ou tema que o cliente perguntou (ou null)
- "nome": nome do cliente se mencionou (ou null)

Histórico anterior: {historico_atual}
Cliente disse: {mensagem}
Agnes respondeu: {resposta}

Retorne APENAS o JSON, sem explicação."""
            }]
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
            "updated_at": "NOW()"
        }

        if novo_nome:
            update_data["nome"] = novo_nome

        if memoria:
            update_data["total_conversas"] = memoria.get("total_conversas", 0) + 1
            supabase.table("memoria_clientes").update(update_data).eq("numero_whatsapp", numero).execute()
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
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": f"""Extraia apenas as palavras-chave de produto ou tema espiritual desta mensagem para buscar em uma loja esotérica.
Corrija erros de digitação. Retorne APENAS as keywords, sem explicação, máximo 3 palavras.
Exemplos:
- "quero comprar mandragora" → "mandrágora"
- "tem cristal de quartzo?" → "cristal quartzo"
- "incenso pra proteçao" → "incenso proteção"

Mensagem: {mensagem}
Keywords:"""
            }]
        )
        keywords = response.content[0].text.strip()
        return keywords if keywords else mensagem
    except Exception:
        return mensagem


async def buscar_conteudo_wp(query: str) -> str:
    """Busca no banco indexado — rapido e economico"""
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
                estoque = "Em estoque" if p.get("estoque") == "Em estoque" else "Sob consulta"
                descricao = p.get("descricao", "")[:200]
                resultado += f"- {nome} R$ {preco} | {estoque}\n  {descricao}\n  {link}\n"
            resultado += "\n"

        posts = await buscar_posts_indexados(query)
        if posts:
            resultado += "Conteudos do portal:\n"
            for post in posts:
                titulo = post.get("titulo", "")
                link = post.get("link", "")
                resultado += f"- {titulo}\n  {link}\n"

    except Exception as e:
        print(f"Erro ao buscar conteudo indexado: {e}")

    return resultado

async def enviar_status_digitando(numero: str):
    """Marca presença como online e digitando via WhatsApp API"""
    url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    # Marca como online
    payload_online = {
        "messaging_product": "whatsapp",
        "to": numero,
        "recipient_type": "individual",
        "type": "contacts",
        "status": "read"
    }
    try:
        async with httpx.AsyncClient() as c:
            await c.post(url, headers=headers, json=payload_online)
    except Exception:
        pass


async def enviar_mensagem_whatsapp(numero: str, mensagem: str):
    url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": numero, "type": "text", "text": {"body": mensagem}}
    async with httpx.AsyncClient() as client_http:
        await client_http.post(url, headers=headers, json=payload)


def dividir_mensagem(texto: str) -> list:
    """Divide mensagem longa em partes naturais para simular digitação humana"""
    # Se for curta, manda de uma vez
    if len(texto) < 300:
        return [texto]

    partes = []
    paragrafos = texto.split("

")
    parte_atual = ""

    for paragrafo in paragrafos:
        if not paragrafo.strip():
            continue
        # Se adicionar esse parágrafo passar de 400 chars, fecha a parte atual
        if len(parte_atual) + len(paragrafo) > 400 and parte_atual:
            partes.append(parte_atual.strip())
            parte_atual = paragrafo
        else:
            parte_atual += "

" + paragrafo if parte_atual else paragrafo

    if parte_atual.strip():
        partes.append(parte_atual.strip())

    # Limita a 4 partes
    return partes[:4] if partes else [texto]


async def enviar_mensagem_picada(numero: str, mensagem: str):
    """Envia mensagem dividida em partes com delay para simular digitação humana"""
    partes = dividir_mensagem(mensagem)

    for i, parte in enumerate(partes):
        # Delay proporcional ao tamanho da parte (simula digitação)
        delay = min(len(parte) * 0.02, 3.0)  # máximo 3 segundos
        if i > 0:
            await asyncio.sleep(delay)

        await enviar_mensagem_whatsapp(numero, parte)


# ============================================================
# PROCESSAMENTO PRINCIPAL
# ============================================================



async def baixar_audio_whatsapp(media_id: str) -> bytes:
    """Baixa arquivo de áudio do WhatsApp"""
    # Primeiro pega a URL do arquivo
    url_info = f"https://graph.facebook.com/v22.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    
    async with httpx.AsyncClient() as c:
        r = await c.get(url_info, headers=headers)
        media_url = r.json().get("url")
        
        # Baixa o arquivo
        r2 = await c.get(media_url, headers=headers)
        return r2.content


async def transcrever_audio(audio_bytes: bytes) -> str:
    """Transcreve áudio usando OpenAI Whisper"""
    try:
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            return ""
        
        client_oai = openai.OpenAI(api_key=openai_key)
        
        # Salva temporariamente
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name
        
        # Transcreve
        with open(temp_path, "rb") as f:
            transcript = client_oai.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="pt"
            )
        
        import os as os_module
        os_module.unlink(temp_path)
        
        return transcript.text
    except Exception as e:
        print(f"Erro ao transcrever audio: {e}")
        return ""

async def processar_mensagem(numero: str, mensagem: str) -> str:
    if "HUMANO" in mensagem.upper():
        return "💜 Entendido! Vou chamar nossa equipe agora.\n\nEm breve alguém da Além de Salém entrará em contato com você.\n\nQue a luz guie esse encontro! ✨"

    memoria = await buscar_memoria_cliente(numero)
    query_busca = await extrair_keywords_com_ia(mensagem)
    print(f"Keywords extraidas pela IA: {query_busca}")
    contexto_wp = await buscar_conteudo_wp(query_busca)

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

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": mensagem_com_contexto}]
    )

    resposta = response.content[0].text

    asyncio.create_task(salvar_conversa(numero, mensagem, resposta, query_busca))
    asyncio.create_task(atualizar_memoria_cliente(numero, mensagem, resposta, query_busca))

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

        if tipo == "text":
            texto = message.get("text", {}).get("body", "")
            await enviar_status_digitando(numero)
            resposta = await processar_mensagem(numero, texto)
            await enviar_mensagem_picada(numero, resposta)

        elif tipo == "audio":
            media_id = message.get("audio", {}).get("id", "")
            await enviar_status_digitando(numero)
            audio_bytes = await baixar_audio_whatsapp(media_id)
            texto_transcrito = await transcrever_audio(audio_bytes)
            if texto_transcrito:
                print(f"Audio transcrito: {texto_transcrito}")
                resposta = await processar_mensagem(numero, texto_transcrito)
                await enviar_mensagem_picada(numero, f"🎙️ Entendi: _{texto_transcrito}_\n\n{resposta}")
            else:
                await enviar_mensagem_picada(numero, "💜 Não consegui entender o áudio. Pode digitar sua mensagem?")

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
        clientes = supabase.table("memoria_clientes").select("*", count="exact").execute()
        top_keywords = supabase.table("conversas").select("keywords").execute()

        keywords_count = {}
        for row in top_keywords.data:
            kw = row.get("keywords", "")
            if kw:
                keywords_count[kw] = keywords_count.get(kw, 0) + 1

        top = sorted(keywords_count.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "total_conversas": conversas.count,
            "total_clientes": clientes.count,
            "top_interesses": top
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
            "posts_indexados": total_posts
        }
    except Exception as e:
        return {"status": "erro", "detalhe": str(e)}


@app.on_event("startup")
async def startup_event():
    """Roda indexação inicial ao subir o servidor"""
    import asyncio
    print("Agnes iniciando... verificando banco de dados")
    try:
        result = supabase.table("produtos_indexados").select("id", count="exact").execute()
        total = result.count or 0
        if total == 0:
            print("Banco vazio — iniciando indexacao completa...")
            asyncio.create_task(indexar_produtos())
            asyncio.create_task(indexar_posts())
        else:
            print(f"Banco ja tem {total} produtos indexados")
    except Exception as e:
        print(f"Erro no startup: {e}")
