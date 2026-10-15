import csv
import io
import os
import re
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, abort, redirect, url_for,
    flash, send_file, session
)
from flask import render_template_string
from supabase import create_client, Client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAMPOS_COMERCIAIS = [
    "cnpj",
    "status_comercial",
    "ultimo_contato",
    "resultado_contato",
    "visitou",
    "ultima_compra",
    "valor_ultima_compra",
    "proximo_retorno",
    "observacoes",
]
CAMPOS_PESQUISA = [
    "cnpj", "razao_social", "nome_fantasia", "CONTATO 1", "CONTATO 2",
    "telefone_1", "telefone_2", "codigo_municipio", "uf", "email",
    "cnae_principal", "cnae_principal_codigo",
]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-no-render")


LOGIN_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Acesso | CAM AGROLOG CRM</title>
  <style>
    body{font-family:Arial,sans-serif;background:#f3f5f4;margin:0;min-height:100vh;display:grid;place-items:center}
    form{background:white;padding:28px;border-radius:12px;box-shadow:0 4px 20px #0001;width:min(360px,calc(100% - 56px))}
    h1{font-size:22px;margin-top:0} input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;font-size:16px}
    button{background:#176b3a;color:white;border:0;border-radius:6px;cursor:pointer}
    .erro{color:#b00020}
  </style>
</head>
<body>
  <form method="post">
    <h1>CAM AGROLOG CRM</h1>
    <p>Informe a senha de acesso.</p>
    {% if erro %}<p class="erro">{{ erro }}</p>{% endif %}
    <input type="password" name="senha" placeholder="Senha de acesso" required autofocus>
    <button type="submit">Entrar</button>
  </form>
</body>
</html>
"""


def normalizar_cnpj(valor):
    return re.sub(r"\D", "", str(valor or ""))


def cliente_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL", "").strip()
    chave = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not chave:
        raise RuntimeError(
            "Faltam as variáveis SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY "
            "nas configurações do serviço."
        )
    return create_client(url, chave)


def exigir_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("crm_autorizado"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    senha_configurada = os.environ.get("CRM_ACCESS_PASSWORD", "")
    erro = None
    if request.method == "POST":
        senha_informada = request.form.get("senha", "")
        if not senha_configurada:
            erro = "O administrador ainda não configurou CRM_ACCESS_PASSWORD no Render."