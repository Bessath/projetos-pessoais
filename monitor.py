#!/usr/bin/env python3
"""
Monitor da pagina de inscricoes da EJURR (TJRR).

Observa os blocos "Inscricoes Abertas" e "Inscricoes em Breve" e avisa quando:
  - surge qualquer curso novo nesses blocos;
  - surge um curso que bate com as palavras-chave de interesse (prioridade alta).

Estado salvo em state.json para comparar entre execucoes.
"""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://ejurr.tjrr.jus.br/?page_id=343"
STATE_FILE = Path(__file__).parent / "state.json"

# Blocos que interessam, na ordem em que aparecem na pagina.
SECOES_MONITORADAS = ["inscricoes abertas", "inscricoes em breve"]
TODOS_OS_MARCADORES = [
    "inscricoes abertas",
    "inscricoes em breve",
    "vagas esgotadas",
    "eventos em andamento",
    "eventos finalizados",
]

# Se o titulo bater com algum destes, o alerta vira prioridade alta.
PALAVRAS_CHAVE = ["formacao de formadores", "formador", "fofo"]

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def normalizar(texto: str) -> str:
    """Minusculas, sem acento, espacos colapsados."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


def baixar_pagina() -> str:
    resposta = requests.get(URL, headers=HEADERS, timeout=45)
    resposta.raise_for_status()
    resposta.encoding = resposta.apparent_encoding or "utf-8"
    return resposta.text


def extrair_secoes(html: str) -> dict:
    """Devolve {nome_do_bloco: [titulos]} para os blocos monitorados."""
    sopa = BeautifulSoup(html, "html.parser")
    corpo = sopa.body or sopa

    linhas = [ln.strip() for ln in corpo.get_text("\n").split("\n")]
    linhas = [ln for ln in linhas if ln]

    # Localiza a linha de cada marcador (primeira ocorrencia exata).
    posicoes = {}
    for indice, linha in enumerate(linhas):
        chave = normalizar(linha)
        if chave in TODOS_OS_MARCADORES and chave not in posicoes:
            posicoes[chave] = indice

    if not any(m in posicoes for m in SECOES_MONITORADAS):
        raise RuntimeError(
            "Nao encontrei os titulos das secoes. O layout do site pode ter mudado."
        )

    ordenados = sorted(posicoes.items(), key=lambda item: item[1])
    resultado = {}

    for ordem, (marcador, inicio) in enumerate(ordenados):
        if marcador not in SECOES_MONITORADAS:
            continue
        fim = ordenados[ordem + 1][1] if ordem + 1 < len(ordenados) else len(linhas)
        titulos = []
        for linha in linhas[inicio + 1 : fim]:
            if len(linha) < 4 or normalizar(linha) in TODOS_OS_MARCADORES:
                continue
            if linha not in titulos:
                titulos.append(linha)
        resultado[marcador] = titulos

    return resultado


def carregar_estado() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def salvar_estado(estado: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def e_de_interesse(titulo: str) -> bool:
    alvo = normalizar(titulo)
    return any(chave in alvo for chave in PALAVRAS_CHAVE)


def notificar(mensagem: str) -> None:
    print(mensagem)
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[aviso] Telegram nao configurado, alerta so no log.")
        return
    endereco = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(
        endereco,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensagem,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    ).raise_for_status()


def main() -> int:
    try:
        html = baixar_pagina()
        secoes = extrair_secoes(html)
    except Exception as erro:
        notificar(f"⚠️ Monitor EJURR falhou: {type(erro).__name__}: {erro}\n{URL}")
        return 1

    anterior = carregar_estado()
    novidades = []

    for secao, titulos in secoes.items():
        conhecidos = set(anterior.get(secao, []))
        for titulo in titulos:
            if titulo not in conhecidos:
                novidades.append((secao, titulo))

    primeira_execucao = not anterior
    salvar_estado(secoes)

    if primeira_execucao:
        resumo = "\n".join(
            f"<b>{secao.title()}</b>\n"
            + ("\n".join(f"• {t}" for t in titulos) if titulos else "• (vazio)")
            for secao, titulos in secoes.items()
        )
        notificar(
            "✅ Monitor da EJURR ativado. Situação atual:\n\n"
            f"{resumo}\n\n{URL}"
        )
        return 0

    if not novidades:
        print("Sem mudanças.")
        return 0

    prioritarias = [item for item in novidades if e_de_interesse(item[1])]
    corpo = "\n".join(f"• [{secao.title()}] {titulo}" for secao, titulo in novidades)

    if prioritarias:
        cabecalho = "🔔🔔 CURSO DE INTERESSE NA EJURR"
    else:
        cabecalho = "📣 Novidade na página de inscrições da EJURR"

    notificar(f"<b>{cabecalho}</b>\n\n{corpo}\n\n{URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
