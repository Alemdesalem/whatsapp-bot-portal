# Agnes – Assistente WhatsApp Além de Salém 🔮

Bot de atendimento inteligente para o portal e loja Além de Salém.

## Variáveis de ambiente necessárias

Configure no Railway:

| Variável | Descrição |
|---|---|
| `WHATSAPP_TOKEN` | Token de acesso da Meta Developer |
| `PHONE_NUMBER_ID` | ID do número de telefone na Meta |
| `ANTHROPIC_API_KEY` | Chave da API da Anthropic (Claude) |
| `VERIFY_TOKEN` | Token de verificação do webhook (você escolhe) |
| `WP_URL` | URL do portal WordPress |

## Deploy no Railway

1. Suba este repositório no GitHub
2. Conecte ao Railway
3. Configure as variáveis de ambiente
4. Railway gera a URL automaticamente
5. Configure o webhook na Meta Developer com a URL gerada

## Webhook URL

```
https://sua-url.railway.app/webhook
```
