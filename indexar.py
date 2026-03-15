"""
Indexação do WordPress + WooCommerce para o Supabase.
Roda automaticamente via endpoint /indexar ou no startup.
"""

import os
import re
import httpx
from supabase import create_client, Client

WP_URL = os.environ.get("WP_URL", "https://www.alemdesalem.com.br")
WC_KEY = os.environ.get("WC_CONSUMER_KEY")
WC_SECRET = os.environ.get("WC_CONSUMER_SECRET")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Regex pré-compilado para limpeza de HTML
RE_HTML_TAGS = re.compile(r"<[^>]+>")
RE_MULTI_SPACES = re.compile(r"\s+")


def limpar_html(texto: str) -> str:
    """Remove tags HTML e normaliza espaços"""
    if not texto:
        return ""
    texto = RE_HTML_TAGS.sub(" ", texto)
    texto = RE_MULTI_SPACES.sub(" ", texto).strip()
    return texto[:2000]


async def indexar_produtos() -> int:
    """Importa todos os produtos do WooCommerce para o Supabase"""
    total = 0
    page = 1

    print("Iniciando indexação de produtos...")

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            url = f"{WP_URL}/wp-json/wc/v3/products"
            params = {"per_page": 100, "page": page, "status": "publish"}
            r = await client.get(url, params=params, auth=(WC_KEY, WC_SECRET))

            if r.status_code != 200:
                break

            produtos = r.json()
            if not produtos:
                break

            # Prepara batch de upserts
            for p in produtos:
                try:
                    categorias = ", ".join(c["name"] for c in p.get("categories", []))
                    tags = ", ".join(t["name"] for t in p.get("tags", []))
                    descricao = limpar_html(
                        p.get("description", "") or p.get("short_description", "")
                    )
                    estoque = (
                        "Em estoque"
                        if p.get("stock_status") == "instock"
                        else "Sob consulta"
                    )

                    supabase.table("produtos_indexados").upsert(
                        {
                            "produto_id": p["id"],
                            "nome": p.get("name", ""),
                            "descricao": descricao,
                            "preco": p.get("price", ""),
                            "estoque": estoque,
                            "categorias": categorias,
                            "tags": tags,
                            "link": p.get("permalink", ""),
                        },
                        on_conflict="produto_id",
                    ).execute()

                    total += 1
                except Exception as e:
                    print(f"Erro produto {p.get('id')}: {e}")

            print(f"  Página {page}: {len(produtos)} produtos indexados")
            page += 1

            if len(produtos) < 100:
                break

    print(f"Total de produtos indexados: {total}")
    return total


async def indexar_posts() -> int:
    """Importa todos os posts do WordPress para o Supabase"""
    total = 0
    page = 1

    print("Iniciando indexação de posts...")

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            url = f"{WP_URL}/wp-json/wp/v2/posts"
            params = {
                "per_page": 100,
                "page": page,
                "_fields": "id,title,content,link,status",
            }
            r = await client.get(url, params=params)

            if r.status_code != 200:
                break

            posts = r.json()
            if not posts:
                break

            for post in posts:
                try:
                    titulo = limpar_html(post.get("title", {}).get("rendered", ""))
                    conteudo = limpar_html(post.get("content", {}).get("rendered", ""))

                    supabase.table("posts_indexados").upsert(
                        {
                            "post_id": post["id"],
                            "titulo": titulo,
                            "conteudo": conteudo,
                            "link": post.get("link", ""),
                        },
                        on_conflict="post_id",
                    ).execute()

                    total += 1
                except Exception as e:
                    print(f"Erro post {post.get('id')}: {e}")

            print(f"  Página {page}: {len(posts)} posts indexados")
            page += 1

            if len(posts) < 100:
                break

    print(f"Total de posts indexados: {total}")
    return total


async def buscar_produtos_indexados(query: str, limite: int = 5) -> list:
    """Busca produtos indexados no Supabase por texto (nome > tags > categoria > descrição)"""
    campos = "nome, preco, estoque, link, categorias, tags, descricao"
    colunas_busca = ["nome", "tags", "categorias", "descricao"]

    try:
        for coluna in colunas_busca:
            result = (
                supabase.table("produtos_indexados")
                .select(campos)
                .ilike(coluna, f"%{query}%")
                .limit(limite)
                .execute()
            )
            if result.data:
                return result.data
        return []
    except Exception as e:
        print(f"Erro ao buscar produtos indexados: {e}")
        return []


async def buscar_posts_indexados(query: str, limite: int = 3) -> list:
    """Busca posts indexados no Supabase por texto (título > conteúdo)"""
    campos = "titulo, link, conteudo"

    try:
        for coluna in ["titulo", "conteudo"]:
            result = (
                supabase.table("posts_indexados")
                .select(campos)
                .ilike(coluna, f"%{query}%")
                .limit(limite)
                .execute()
            )
            if result.data:
                return result.data
        return []
    except Exception as e:
        print(f"Erro ao buscar posts indexados: {e}")
        return []
