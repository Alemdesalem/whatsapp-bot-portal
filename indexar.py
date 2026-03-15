"""
Script de indexação do WordPress + WooCommerce para o Supabase.
Roda automaticamente via endpoint /indexar ou manualmente.
"""
import os
import httpx
import re
from supabase import create_client, Client

WP_URL = os.environ.get("WP_URL", "https://www.alemdesalem.com.br")
WC_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_SECRET = os.environ.get("WC_CONSUMER_SECRET")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def limpar_html(texto: str) -> str:
    """Remove tags HTML do texto"""
    if not texto:
        return ""
    texto = re.sub(r'<[^>]+>', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto[:2000]  # Limita tamanho


async def indexar_produtos():
    """Importa todos os produtos do WooCommerce para o Supabase"""
    total = 0
    page = 1

    print("🛍️ Iniciando indexação de produtos...")

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            url = f"{WP_URL}/wp-json/wc/v3/products?per_page=100&page={page}&status=publish"
            r = await client.get(url, auth=(WC_KEY, WC_SECRET))

            if r.status_code != 200 or not r.json():
                break

            produtos = r.json()
            if not produtos:
                break

            for p in produtos:
                try:
                    categorias = ", ".join([c["name"] for c in p.get("categories", [])])
                    tags = ", ".join([t["name"] for t in p.get("tags", [])])
                    descricao = limpar_html(p.get("description", "") or p.get("short_description", ""))
                    stock_status = p.get("stock_status", "instock")
                    estoque = "Em estoque" if stock_status == "instock" else "Sob consulta"

                    supabase.table("produtos_indexados").upsert({
                        "produto_id": p["id"],
                        "nome": p.get("name", ""),
                        "descricao": descricao,
                        "preco": p.get("price", ""),
                        "estoque": estoque,
                        "categorias": categorias,
                        "tags": tags,
                        "link": p.get("permalink", ""),
                        "atualizado_em": "NOW()"
                    }, on_conflict="produto_id").execute()

                    total += 1
                except Exception as e:
                    print(f"Erro produto {p.get('id')}: {e}")

            print(f"  Página {page}: {len(produtos)} produtos indexados")
            page += 1

            if len(produtos) < 100:
                break

    print(f"✅ Total de produtos indexados: {total}")
    return total


async def indexar_posts():
    """Importa todos os posts do WordPress para o Supabase"""
    total = 0
    page = 1

    print("📰 Iniciando indexação de posts...")

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            url = f"{WP_URL}/wp-json/wp/v2/posts?per_page=100&page={page}&_fields=id,title,content,link,status"
            r = await client.get(url)

            if r.status_code != 200:
                break

            posts = r.json()
            if not posts:
                break

            for post in posts:
                try:
                    titulo = limpar_html(post.get("title", {}).get("rendered", ""))
                    conteudo = limpar_html(post.get("content", {}).get("rendered", ""))

                    supabase.table("posts_indexados").upsert({
                        "post_id": post["id"],
                        "titulo": titulo,
                        "conteudo": conteudo,
                        "link": post.get("link", ""),
                        "atualizado_em": "NOW()"
                    }, on_conflict="post_id").execute()

                    total += 1
                except Exception as e:
                    print(f"Erro post {post.get('id')}: {e}")

            print(f"  Página {page}: {len(posts)} posts indexados")
            page += 1

            if len(posts) < 100:
                break

    print(f"✅ Total de posts indexados: {total}")
    return total


async def buscar_produtos_indexados(query: str, limite: int = 5) -> list:
    """Busca produtos indexados no Supabase por texto"""
    try:
        # Busca por nome
        r1 = supabase.table("produtos_indexados").select(
            "nome, preco, estoque, link, categorias, tags, descricao"
        ).ilike("nome", f"%{query}%").limit(limite).execute()

        # Busca por tags se não achou por nome
        if not r1.data:
            r1 = supabase.table("produtos_indexados").select(
                "nome, preco, estoque, link, categorias, tags, descricao"
            ).ilike("tags", f"%{query}%").limit(limite).execute()

        # Busca por categoria
        if not r1.data:
            r1 = supabase.table("produtos_indexados").select(
                "nome, preco, estoque, link, categorias, tags, descricao"
            ).ilike("categorias", f"%{query}%").limit(limite).execute()

        # Busca por descrição
        if not r1.data:
            r1 = supabase.table("produtos_indexados").select(
                "nome, preco, estoque, link, categorias, tags, descricao"
            ).ilike("descricao", f"%{query}%").limit(limite).execute()

        return r1.data or []
    except Exception as e:
        print(f"Erro ao buscar produtos indexados: {e}")
        return []


async def buscar_posts_indexados(query: str, limite: int = 3) -> list:
    """Busca posts indexados no Supabase por texto"""
    try:
        r1 = supabase.table("posts_indexados").select(
            "titulo, link, conteudo"
        ).ilike("titulo", f"%{query}%").limit(limite).execute()

        if not r1.data:
            r1 = supabase.table("posts_indexados").select(
                "titulo, link, conteudo"
            ).ilike("conteudo", f"%{query}%").limit(limite).execute()

        return r1.data or []
    except Exception as e:
        print(f"Erro ao buscar posts indexados: {e}")
        return []
