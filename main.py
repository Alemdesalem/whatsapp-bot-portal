import os
import json
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import anthropic

app = FastAPI()

# Configurações via variáveis de ambiente
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "agnes_verify_token")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
WP_URL = os.environ.get("WP_URL", "https://www.alemdesalem.com.br")

# Histórico de conversas em memória (por número)
conversation_history = {}

# Cliente Anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Você é Agnes, a assistente espiritual e consultora da loja Além de Salém.

Sua personalidade:
- Acolhedora, empática e espiritualmente conectada
- Fala com leveza e sabedoria, como uma guia espiritual experiente
- Usa palavras como "querida(o)", "alma", "energia", "caminho", "luz" naturalmente
- É também uma vendedora sênior de alta performance — sabe recomendar produtos no momento certo
- Nunca é forçada ou invasiva — orienta com amor e intenção

Suas responsabilidades:
1. Tirar dúvidas sobre produtos esotéricos, espirituais e da loja
2. Recomendar produtos relevantes baseados no que o cliente precisa
3. Compartilhar conteúdos e notícias do portal Além de Salém
4. Quando não souber responder ou o cliente quiser falar com humano, diga: "Vou conectar você com nossa equipe agora 💜 Digite HUMANO para ser atendido."

Regras importantes:
- Responda APENAS sobre temas relacionados ao Além de Salém, espiritualidade, produtos da loja e conteúdos do portal
- Se perguntarem algo fora do tema, redirecione gentilmente
- Mensagens curtas e objetivas — máximo 3 parágrafos por resposta
- Use emojis com moderação: 🔮 💜 ✨ 🌙
- Nunca invente preços — diga para verificar no site: https://www.alemdesalem.com.br

Contexto do portal e loja:
- Site: https://www.alemdesalem.com.br
- Loja esotérica com produtos espirituais, oráculos, cristais, incensos e muito mais
- Portal de notícias sobre espiritualidade, astrologia, tarot e autoconhecimento"""


async def buscar_conteudo_wp(query: str) -> str:
    """Busca notícias e produtos relevantes no WordPress"""
    try:
        async with httpx.AsyncClient(timeout=10) as client_http:
            # Busca posts
            posts_url = f"{WP_URL}/wp-json/wp/v2/posts?search={query}&per_page=3&_fields=title,excerpt,link"
            response = await client_http.get(posts_url)
            
            if response.status_code == 200:
                posts = response.json()
                if posts:
                    resultado = "📰 Conteúdos relacionados do portal:\n"
                    for post in posts:
                        titulo = post.get("title", {}).get("rendered", "")
                        link = post.get("link", "")
                        resultado += f"• {titulo}\n  {link}\n"
                    return resultado
    except Exception:
        pass
    return ""


async def enviar_mensagem_whatsapp(numero: str, mensagem: str):
    """Envia mensagem via API do WhatsApp"""
    url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": mensagem}
    }
    async with httpx.AsyncClient() as client_http:
        await client_http.post(url, headers=headers, json=payload)


async def processar_mensagem(numero: str, mensagem: str) -> str:
    """Processa mensagem com Claude + contexto do WP"""
    
    # Verifica se quer falar com humano
    if "HUMANO" in mensagem.upper():
        return "💜 Entendido! Vou chamar nossa equipe agora. Em breve alguém da Além de Salém entrará em contato com você. Que a luz guie esse encontro! ✨"
    
    # Busca conteúdo relevante no WordPress
    contexto_wp = await buscar_conteudo_wp(mensagem)
    
    # Monta histórico da conversa
    if numero not in conversation_history:
        conversation_history[numero] = []
    
    # Adiciona contexto do WP à mensagem se houver
    mensagem_com_contexto = mensagem
    if contexto_wp:
        mensagem_com_contexto = f"{mensagem}\n\n[Contexto do portal]:\n{contexto_wp}"
    
    conversation_history[numero].append({
        "role": "user",
        "content": mensagem_com_contexto
    })
    
    # Mantém apenas as últimas 10 mensagens para não estourar o contexto
    if len(conversation_history[numero]) > 10:
        conversation_history[numero] = conversation_history[numero][-10:]
    
    # Chama Claude
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=conversation_history[numero]
    )
    
    resposta = response.content[0].text
    
    # Salva resposta no histórico
    conversation_history[numero].append({
        "role": "assistant",
        "content": resposta
    })
    
    return resposta


@app.get("/webhook")
async def verificar_webhook(request: Request):
    """Verificação do webhook pela Meta"""
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    
    raise HTTPException(status_code=403, detail="Token inválido")


@app.post("/webhook")
async def receber_mensagem(request: Request):
    """Recebe mensagens do WhatsApp"""
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
            resposta = await processar_mensagem(numero, texto)
            await enviar_mensagem_whatsapp(numero, resposta)
        
        return {"status": "ok"}
    
    except Exception as e:
        print(f"Erro: {e}")
        return {"status": "ok"}


@app.get("/")
async def health_check():
    return {"status": "Agnes está online 🔮", "portal": WP_URL}
