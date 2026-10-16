import csv
import io
import os
import re
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, render_template_string, request,
    redirect, url_for, flash, session, send_file
)
from supabase import create_client, Client

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-no-render")

CAMPOS_COMERCIAIS = [
    "status_comercial",
    "ultimo_contato",
    "resultado_contato",
    "visitou",
    "ultima_compra",
    "valor_ultima_compra",
    "proximo_retorno",
    "observacoes",
]


def normalizar_cnpj(valor):
    return re.sub(r"\D", "", str(valor or ""))


def cliente_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL", "").strip()
    chave = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not url or not chave:
        raise RuntimeError(
            "Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no Render."
        )

    return create_client(url, chave)


def exigir_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("crm_autorizado"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapper


LOGIN_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login | CAM AGROLOG CRM</title>
<style>
body{font-family:Arial;background:#f2f4f2;margin:0;min-height:100vh;
display:grid;place-items:center}
form{background:white;padding:28px;border-radius:10px;width:300px}
input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px}
button{background:#176b3a;color:white;border:0;border-radius:5px;cursor:pointer}
.erro{color:#b00020}
</style>
</head>
<body>
<form method="post">
<h2>CAM AGROLOG CRM</h2>
<p>Informe sua senha de acesso.</p>
{% if erro %}<p class="erro">{{ erro }}</p>{% endif %}
<input type="password" name="senha" placeholder="Senha de acesso" required autofocus>
<button type="submit">Entrar</button>
</form>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    senha_configurada = os.environ.get("CRM_ACCESS_PASSWORD", "")
    erro = None

    if request.method == "POST":
        senha_informada = request.form.get("senha", "")

        if not senha_configurada:
            erro = "Configure CRM_ACCESS_PASSWORD no Render."
        if senha_informada == senha_configurada:
            session["crm_autorizado"] = True
            return redirect(url_for("acessos"))
        else:
            erro = "Senha incorreta."

    return render_template_string(LOGIN_HTML, erro=erro)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

AREAS_CAM = {
    "agrolog": {
        "nome": "CAM AGROLOG",
        "descricao": "Compra e venda de grãos."
    },
    "distribuicao": {
        "nome": "CAM DISTRIBUIÇÃO",
        "descricao": "Clientes de ovos e outras mercadorias."
    },
    "agrologistica": {
        "nome": "CAM AGROLOGÍSTICA",
        "descricao": "Fretes, caminhoneiros e transportadoras."
    }
}


@app.route("/acessos")
@exigir_login
def acessos():
    return render_template("acessos.html")



def ler_csv_upload(arquivo):
    conteudo = arquivo.read().decode("utf-8-sig", errors="replace")

    amostra = conteudo[:10000]
    try:
        dialeto = csv.Sniffer().sniff(
            amostra, delimiters=";,\t|,"
        )
        leitor = csv.DictReader(
            io.StringIO(conteudo), dialect=dialeto
        )
    except csv.Error:
        leitor = csv.DictReader(
            io.StringIO(conteudo), delimiter=";"
        )

    if not leitor.fieldnames:
        raise ValueError("O CSV precisa ter uma linha de cabeçalho.")

    cabecalhos = [
        str(campo).strip() if campo else ""
        for campo in leitor.fieldnames
    ]

    if not any(cabecalhos):
        raise ValueError("O CSV não possui colunas válidas.")

    leitor.fieldnames = cabecalhos
    linhas = []

    for linha in leitor:
        dados = {
            str(chave).strip(): str(valor or "").strip()
            for chave, valor in linha.items()
            if chave and str(chave).strip()
        }

        if any(dados.values()):
            linhas.append(dados)

    if not linhas:
        raise ValueError("O CSV não contém registros.")

    return linhas


def identificar_registro(dados):
    for chave, valor in dados.items():
        nome = re.sub(r"[^a-z0-9]", "", chave.lower())

        if nome in ("cnpj", "cpfcnpj", "telefone", "telefone1",
                    "contato1", "celular", "identificador"):
            if valor:
                return str(valor)

    for valor in dados.values():
        if valor:
            return str(valor)

    return ""


@app.route("/bases/<area>")
@exigir_login
def bases(area):
    if area not in AREAS_CAM:
        flash("Área não encontrada.")
        return redirect(url_for("acessos"))

    try:
        resposta = (
            cliente_supabase()
            .table("bases_csv")
            .select("*")
            .eq("area", area)
            .order("criado_em", desc=True)
            .execute()
        )
        lista_bases = resposta.data or []

    except Exception as erro:
        return f"Erro ao consultar as bases: {erro}", 500

    return render_template_string("""
    <!doctype html>
    <html lang="pt-br">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Bases | {{ area.nome }}</title>
        <style>
            body{font-family:Arial;background:#f3f5f4;margin:0;padding:20px;color:#222}
            main{max-width:1000px;margin:auto}
            .card{background:white;padding:20px;border-radius:8px;margin:16px 0}
            input,button,a.botao{padding:11px;margin:5px 0}
            button,a.botao{background:#176b3a;color:white;border:0;
                border-radius:4px;text-decoration:none;cursor:pointer;display:inline-block}
            a.voltar{color:#176b3a}
            table{width:100%;border-collapse:collapse}
            th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd}
            .tabela{overflow-x:auto}
            .flash{background:#e3f4e7;padding:10px;margin:10px 0}
        </style>
    </head>
    <body>
    <main>
        <a class="voltar" href="{{ url_for('acessos') }}">← Voltar às áreas</a>
        <h1>{{ area.nome }}</h1>
        <p>{{ area.descricao }}</p>

        {% with mensagens = get_flashed_messages() %}
          {% for mensagem in mensagens %}
            <div class="flash">{{ mensagem }}</div>
          {% endfor %}
        {% endwith %}

        <section class="card">
            <h2>Importar nova base CSV</h2>
            <form method="post"
                  action="{{ url_for('importar_base', area=chave_area) }}"
                  enctype="multipart/form-data">
                <label>Nome da base</label><br>
                <input name="nome" placeholder="Ex.: Clientes de Salvador"
                       required style="width:min(400px,90%)"><br>
                <label>Arquivo CSV</label><br>
                <input type="file" name="arquivo" accept=".csv,.txt"
                       required><br>
                <button type="submit">Importar base</button>
            </form>
            <p>As colunas originais do arquivo serão preservadas.
               Não é obrigatório ter uma coluna CNPJ.</p>
        </section>

        <section class="card">
            <h2>Bases importadas ({{ lista_bases|length }})</h2>
            <div class="tabela">
            <table>
                <thead>
                    <tr><th>Nome</th><th>Registros</th><th>Data</th><th>Ação</th></tr>
                </thead>
                <tbody>
                {% for base in lista_bases %}
                    <tr>
                        <td>{{ base.nome }}</td>
                        <td>{{ base.total_registros }}</td>
                        <td>{{ base.criado_em }}</td>
                        <td><a href="{{ url_for('base_detalhe', base_id=base.id) }}">Abrir base</a></td>
                    </tr>
                {% else %}
                    <tr><td colspan="4">Nenhuma base importada nesta área.</td></tr>
                {% endfor %}
                </tbody>
            </table>
            </div>
        </section>

        <a class="botao" href="{{ url_for('inicio') }}">Abrir CRM antigo</a>
    </main>
    </body>
    </html>
    """, area=AREAS_CAM[area], chave_area=area, lista_bases=lista_bases)


@app.route("/bases/<area>/importar", methods=["POST"])
@exigir_login
def importar_base(area):
    if area not in AREAS_CAM:
        flash("Área não encontrada.")
        return redirect(url_for("acessos"))

    arquivo = request.files.get("arquivo")
    nome = request.form.get("nome", "").strip()

    if not arquivo or not arquivo.filename or not nome:
        flash("Informe o nome da base e selecione um CSV.")
        return redirect(url_for("bases", area=area))

    try:
        linhas = ler_csv_upload(arquivo)
        supabase = cliente_supabase()

        criada = (
            supabase.table("bases_csv")
            .insert({
                "area": area,
                "nome": nome,
                "arquivo_original": arquivo.filename,
                "total_registros": len(linhas)
            })
            .execute()
        )

        if not criada.data:
            raise RuntimeError("Não foi possível criar a base.")

        base_id = criada.data[0]["id"]

        lote = []
        for dados in linhas:
            lote.append({
                "base_id": base_id,
                "identificador": identificar_registro(dados),
                "dados": dados
            })

        for inicio_lote in range(0, len(lote), 100):
            supabase.table("base_registros").insert(
                lote[inicio_lote:inicio_lote + 100]
            ).execute()

        flash(f"Base importada: {len(linhas)} registros.")

    except Exception as erro:
        flash(f"Erro ao importar CSV: {erro}")

    return redirect(url_for("bases", area=area))


@app.route("/base/<base_id>")
@exigir_login
def base_detalhe(base_id):
    try:
        supabase = cliente_supabase()

        resultado_base = (
            supabase.table("bases_csv")
            .select("*")
            .eq("id", base_id)
            .limit(1)
            .execute()
        )

        if not resultado_base.data:
            flash("Base não encontrada.")
            return redirect(url_for("acessos"))

        base = resultado_base.data[0]

        resultado = (
            supabase.table("base_registros")
            .select("*")
            .eq("base_id", base_id)
            .order("id")
            .limit(500)
            .execute()
        )

        registros = resultado.data or []
        q = request.args.get("q", "").strip().lower()

        if q:
            registros = [
                registro for registro in registros
                if q in (
                    str(registro.get("identificador", "")) + " " +
                    " ".join(
                        str(v) for v in (registro.get("dados") or {}).values()
                    ) + " " +
                    " ".join(
                        str(registro.get(campo) or "")
                        for campo in CAMPOS_COMERCIAIS
                    )
                ).lower()
            ]

        colunas = []
        for registro in registros:
            for chave in (registro.get("dados") or {}):
                if chave not in colunas:
                    colunas.append(chave)

        colunas = colunas[:12]

        return render_template_string("""
        <!doctype html>
        <html lang="pt-br">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>{{ base.nome }} | CAM CRM</title>
            <style>
                body{font-family:Arial;background:#f3f5f4;margin:0;padding:16px;color:#222}
                main{max-width:1200px;margin:auto}
                .card{background:white;padding:16px;border-radius:8px;margin:16px 0}
                input,button{padding:10px}
                button{background:#176b3a;color:white;border:0;cursor:pointer}
                .tabela{overflow-x:auto}
                table{border-collapse:collapse;width:100%;background:white}
                th,td{text-align:left;padding:9px;border-bottom:1px solid #ddd;font-size:13px}
            </style>
        </head>
        <body>
        <main>
            <a href="{{ url_for('bases', area=base.area) }}">← Voltar às bases</a>
            <h1>{{ base.nome }}</h1>
            <p>Área: {{ areas[base.area].nome }}</p>
            <p>Registros encontrados nesta consulta: {{ registros|length }}
               (a tela carrega até 500 registros por vez).</p>

            <section class="card">
                <form method="get">
                    <input name="q" value="{{ q }}" placeholder="Pesquisar nesta base">
                    <button type="submit">Pesquisar</button>
                </form>
            </section>

            <section class="card">
                <div class="tabela">
                <table>
                    <thead><tr>
                        {% for coluna in colunas %}<th>{{ coluna }}</th>{% endfor %}
                        <th>Status comercial</th><th>Último contato</th><th>Ação</th>
                    </tr></thead>
                    <tbody>
                    {% for registro in registros %}
                        <tr>
                            {% for coluna in colunas %}
                                <td>{{ (registro.dados or {}).get(coluna, "") }}</td>
                            {% endfor %}
                            <td>{{ registro.status_comercial or "" }}</td>
                            <td>{{ registro.ultimo_contato or "" }}</td>
                            <td><a href="{{ url_for('registro_editar', registro_id=registro.id) }}">Editar</a></td>
                        </tr>
                    {% else %}
                        <tr><td colspan="{{ colunas|length + 3 }}">Nenhum registro encontrado.</td></tr>
                    {% endfor %}
                    </tbody>
                </table>
                </div>
            </section>
        </main>
        </body>
        </html>
        """, base=base, registros=registros, colunas=colunas,
           areas=AREAS_CAM, q=request.args.get("q", ""))

    except Exception as erro:
        return f"Erro ao abrir a base: {erro}", 500


@app.route("/base-registro/<int:registro_id>", methods=["GET", "POST"])
@exigir_login
def registro_editar(registro_id):
    try:
        supabase = cliente_supabase()

        resultado = (
            supabase.table("base_registros")
            .select("*")
            .eq("id", registro_id)
            .limit(1)
            .execute()
        )

        if not resultado.data:
            flash("Registro não encontrado.")
            return redirect(url_for("acessos"))

        registro = resultado.data[0]
        dados = registro.get("dados") or {}

        if request.method == "POST":
            atualizacoes = {
                campo: request.form.get(campo, "").strip() or None
                for campo in CAMPOS_COMERCIAIS
            }
            atualizacoes["visitou"] = request.form.get("visitou") == "sim"

            valor = atualizacoes.get("valor_ultima_compra")
            if valor:
                try:
                    atualizacoes["valor_ultima_compra"] = float(
                        valor.replace(".", "").replace(",", ".")
                    )
                except ValueError:
                    flash("Valor da compra inválido.")
                    return redirect(url_for("registro_editar", registro_id=registro_id))

            atualizacoes["atualizado_em"] = datetime.now().astimezone().isoformat()

            supabase.table("base_registros").update(
                atualizacoes
            ).eq("id", registro_id).execute()

            flash("Dados comerciais salvos.")
            return redirect(url_for("registro_editar", registro_id=registro_id))

        return render_template_string("""
        <!doctype html>
        <html lang="pt-br">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Editar registro | CAM CRM</title>
            <style>
                body{font-family:Arial;background:#f3f5f4;padding:16px}
                main{max-width:800px;margin:auto;background:white;padding:20px;border-radius:8px}
                label{display:block;margin:12px 0}
                input,select,textarea{box-sizing:border-box;width:100%;padding:10px;margin-top:5px}
                button{padding:11px;background:#176b3a;color:white;border:0;cursor:pointer}
            </style>
        </head>
        <body><main>
            <h2>Cadastro comercial</h2>
            <p><strong>Identificador:</strong> {{ registro.identificador or "—" }}</p>
            {% for chave, valor in dados.items() %}
                <p><strong>{{ chave }}:</strong> {{ valor }}</p>
            {% endfor %}

            <form method="post">
                <label>Status comercial
                    <select name="status_comercial">
                    {% for opcao in ["Novo cliente","Prospectando","Em negociação","Cliente ativo","Sem interesse","Inativo"] %}
                        <option value="{{ opcao }}" {% if registro.status_comercial == opcao %}selected{% endif %}>{{ opcao }}</option>
                    {% endfor %}
                    </select>
                </label>
                <label>Último contato
                    <input type="date" name="ultimo_contato" value="{{ registro.ultimo_contato or '' }}">
                </label>
                <label>Resultado do contato
                    <input name="resultado_contato" value="{{ registro.resultado_contato or '' }}">
                </label>
                <label>Visitou?
                    <select name="visitou">
                        <option value="nao" {% if not registro.visitou %}selected{% endif %}>Não</option>
                        <option value="sim" {% if registro.visitou %}selected{% endif %}>Sim</option>
                    </select>
                </label>
                <label>Última compra
                    <input type="date" name="ultima_compra" value="{{ registro.ultima_compra or '' }}">
                </label>
                <label>Valor da última compra
                    <input name="valor_ultima_compra" value="{{ registro.valor_ultima_compra or '' }}">
                </label>
                <label>Próximo retorno
                    <input type="date" name="proximo_retorno" value="{{ registro.proximo_retorno or '' }}">
                </label>
                <label>Observações
                    <textarea name="observacoes" rows="5">{{ registro.observacoes or '' }}</textarea>
                </label>
                <button type="submit">Salvar alterações</button>
            </form>
            <p><a href="{{ url_for('base_detalhe', base_id=registro.base_id) }}">Voltar à base</a></p>
        </main></body>
        </html>
        """, registro=registro, dados=dados)

    except Exception as erro:
        return f"Erro ao editar registro: {erro}", 500


HTML_CRM = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CAM AGROLOG CRM</title>
<style>
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;margin:0;background:#f3f5f4;color:#222}
header{background:#176b3a;color:white;padding:16px}
main{max-width:1200px;margin:auto;padding:16px}
.card{background:white;padding:16px;border-radius:8px;margin-bottom:16px}
input,select,textarea,button{padding:10px;margin:4px 0;max-width:100%}
input,select,textarea{border:1px solid #ccc;border-radius:4px}
button,.botao{background:#176b3a;color:white;border:0;padding:10px 14px;
border-radius:4px;cursor:pointer;text-decoration:none;display:inline-block}
table{width:100%;border-collapse:collapse;background:white}
th,td{text-align:left;padding:9px;border-bottom:1px solid #ddd;font-size:13px}
th{background:#e8eee9}
.tabela{overflow-x:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}
.flash{padding:10px;background:#e3f4e7;margin-bottom:10px;border-radius:5px}
.pequeno{font-size:12px;color:#666}
a{color:#176b3a}
</style>
</head>
<body>
<header>
<strong>CAM AGROLOG CRM</strong>
<span style="float:right"><a style="color:white" href="/logout">Sair</a></span>
</header>
<main>

{% with mensagens = get_flashed_messages() %}
{% for mensagem in mensagens %}
<div class="flash">{{ mensagem }}</div>
{% endfor %}
{% endwith %}

<div class="card">
<h3>Pesquisar empresas</h3>
<form method="get">
<input name="q" value="{{ q }}" placeholder="CNPJ, razão social, cidade ou telefone" style="width:min(500px,100%)">
<button type="submit">Pesquisar</button>
<a class="botao" href="/">Limpar</a>
<a class="botao" href="/exportar">Exportar CSV</a>
</form>
<p>Total nesta consulta: {{ empresas|length }}</p>
</div>

<div class="card">
<h3>Importar empresas por CSV</h3>
<form action="/importar" method="post" enctype="multipart/form-data">
<input type="file" name="arquivo" accept=".csv,.txt" required>
<button type="submit">Importar CSV</button>
</form>
<p class="pequeno">
O arquivo deve conter uma coluna de CNPJ. Os demais campos serão guardados nos dados da empresa.
</p>
</div>

<div class="card">
<h3>Empresas cadastradas</h3>
<div class="tabela">
<table>
<thead>
<tr>
<th>CNPJ</th>
<th>Razão social</th>
<th>Nome fantasia</th>
<th>Telefone</th>
<th>Status comercial</th>
<th>Último contato</th>
<th>Ação</th>
</tr>
</thead>
<tbody>
{% for empresa in empresas %}
<tr>
<td>{{ empresa.get("cnpj", "") }}</td>
<td>{{ empresa.get("dados", {}).get("razao_social", "") }}</td>
<td>{{ empresa.get("dados", {}).get("nome_fantasia", "") }}</td>
<td>{{ empresa.get("dados", {}).get("CONTATO 1", empresa.get("dados", {}).get("telefone_1", "")) }}</td>
<td>{{ empresa.get("status_comercial", "") or "" }}</td>
<td>{{ empresa.get("ultimo_contato", "") or "" }}</td>
<td><a href="/empresa/{{ empresa.get('cnpj', '') }}">Abrir</a></td>
</tr>
{% else %}
<tr><td colspan="7">Nenhuma empresa encontrada.</td></tr>
{% endfor %}
</tbody>
</table>
</div>
</div>
</main>
</body>
</html>
"""


@app.route("/")
@exigir_login
def inicio():
    q = request.args.get("q", "").strip()

    try:
        resposta = (
            cliente_supabase()
            .table("empresas")
            .select("*")
            .order("atualizado_em", desc=True)
            .limit(500)
            .execute()
        )

        empresas = resposta.data or []

        if q:
            termo = q.lower()
            filtradas = []

            for empresa in empresas:
                dados = empresa.get("dados") or {}
                texto = " ".join(
                    [str(empresa.get("cnpj", ""))]
                    + [str(v) for v in dados.values()]
                    + [str(empresa.get(campo, "") or "")
                       for campo in CAMPOS_COMERCIAIS]
                ).lower()

                if termo in texto:
                    filtradas.append(empresa)

            empresas = filtradas

        return render_template_string(
            HTML_CRM,
            empresas=empresas,
            q=q
        )

    except Exception as erro:
        return (
            "Erro ao consultar o Supabase. "
            "Verifique as configurações e os logs do Render. "
            f"Detalhe: {str(erro)}",
            500
        )


@app.route("/empresa/<cnpj>", methods=["GET", "POST"])
@exigir_login
def empresa_editar(cnpj):
    cnpj = normalizar_cnpj(cnpj)

    try:
        supabase = cliente_supabase()

        resultado = (
            supabase.table("empresas")
            .select("*")
            .eq("cnpj", cnpj)
            .limit(1)
            .execute()
        )

        if not resultado.data:
            flash("Empresa não encontrada.")
            return redirect(url_for("inicio"))

        empresa = resultado.data[0]
        dados = empresa.get("dados") or {}

        if request.method == "POST":
            atualizacoes = {}

            for campo in CAMPOS_COMERCIAIS:
                valor = request.form.get(campo, "").strip()
                atualizacoes[campo] = valor or None

            atualizacoes["visitou"] = (
                request.form.get("visitou") == "sim"
            )

            valor_compra = atualizacoes.get("valor_ultima_compra")

            if valor_compra:
                try:
                    valor_compra = valor_compra.replace(",", ".")
                    atualizacoes["valor_ultima_compra"] = float(valor_compra)
                except ValueError:
                    flash("Valor da última compra inválido.")
                    return redirect(url_for("empresa_editar", cnpj=cnpj))

            atualizacoes["atualizado_em"] = datetime.now().astimezone().isoformat()

            supabase.table("empresas").update(
                atualizacoes
            ).eq("cnpj", cnpj).execute()

            flash("Dados comerciais salvos.")
            return redirect(url_for("empresa_editar", cnpj=cnpj))

        return render_template_string(
            """
            <!doctype html>
            <html lang="pt-br">
            <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Editar empresa | CAM AGROLOG</title>
            <style>
            body{font-family:Arial;background:#f3f5f4;margin:0;padding:16px}
            main{max-width:800px;margin:auto;background:white;padding:20px;border-radius:8px}
            .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
            input,select,textarea{width:100%;padding:10px;margin-top:5px;box-sizing:border-box}
            button,.botao{display:inline-block;background:#176b3a;color:white;padding:11px;
            border:0;text-decoration:none;border-radius:4px;margin-top:14px}
            label{display:block}
            </style>
            </head>
            <body>
            <main>
            <h2>Cadastro comercial</h2>
            <p><a href="/">← Voltar para empresas</a></p>
            {% with mensagens = get_flashed_messages() %}
            {% for mensagem in mensagens %}<p>{{ mensagem }}</p>{% endfor %}
            {% endwith %}
            <p><strong>CNPJ:</strong> {{ empresa.get("cnpj", "") }}</p>
            <p><strong>Razão social:</strong> {{ dados.get("razao_social", "") }}</p>
            <p><strong>Nome fantasia:</strong> {{ dados.get("nome_fantasia", "") }}</p>
            <p><strong>Telefone:</strong> {{ dados.get("CONTATO 1", dados.get("telefone_1", "")) }}</p>

            <form method="post">
            <div class="grid">
            <label>Status comercial
            <select name="status_comercial">
            {% for opcao in ["Novo cliente","Prospectando","Em negociação","Cliente ativo","Sem interesse","Inativo"] %}
            <option value="{{ opcao }}" {% if empresa.get("status_comercial") == opcao %}selected{% endif %}>{{ opcao }}</option>
            {% endfor %}
            </select></label>

            <label>Último contato
            <input type="date" name="ultimo_contato" value="{{ empresa.get('ultimo_contato') or '' }}">
            </label>

            <label>Resultado do contato
            <input name="resultado_contato" value="{{ empresa.get('resultado_contato') or '' }}">
            </label>

            <label>Visitou?
            <select name="visitou">
            <option value="nao" {% if not empresa.get("visitou") %}selected{% endif %}>Não</option>
            <option value="sim" {% if empresa.get("visitou") %}selected{% endif %}>Sim</option>
            </select></label>

            <label>Última compra
            <input type="date" name="ultima_compra" value="{{ empresa.get('ultima_compra') or '' }}">
            </label>

            <label>Valor da última compra
            <input name="valor_ultima_compra" placeholder="Ex.: 1500,00" value="{{ empresa.get('valor_ultima_compra') or '' }}">
            </label>

            <label>Próximo retorno
            <input type="date" name="proximo_retorno" value="{{ empresa.get('proximo_retorno') or '' }}">
            </label>
            </div>

            <label>Observações
            <textarea name="observacoes" rows="5">{{ empresa.get("observacoes") or "" }}</textarea>
            </label>

            <button type="submit">Salvar alterações</button>
            </form>
            </main>
            </body>
            </html>
            """,
            empresa=empresa,
            dados=dados
        )

    except Exception as erro:
        return (
            "Erro ao abrir ou salvar a empresa. "
            f"Detalhe: {str(erro)}",
            500
        )


@app.route("/importar", methods=["POST"])
@exigir_login
def importar_csv():
    arquivo = request.files.get("arquivo")

    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo CSV.")
        return redirect(url_for("inicio"))

    try:
        conteudo = arquivo.read().decode("utf-8-sig", errors="replace")

        try:
            dialect = csv.Sniffer().sniff(conteudo[:10000], delimiters=";,\t|")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"

        leitor = csv.DictReader(io.StringIO(conteudo), dialect=dialect)

        if not leitor.fieldnames:
            flash("O CSV não possui cabeçalho.")
            return redirect(url_for("inicio"))

        campos = {
            str(campo).strip().lower(): campo
            for campo in leitor.fieldnames
            if campo
        }

        campo_cnpj = campos.get("cnpj")

        if not campo_cnpj:
            flash("O CSV precisa ter uma coluna chamada cnpj.")
            return redirect(url_for("inicio"))

        supabase = cliente_supabase()
        lote = []
        importadas = 0

        for linha in leitor:
            cnpj = normalizar_cnpj(linha.get(campo_cnpj, ""))

            if not cnpj:
                continue

            dados = {
                str(chave).strip(): valor
                for chave, valor in linha.items()
                if chave and chave != campo_cnpj
            }

            lote.append({
                "cnpj": cnpj,
                "dados": dados,
                "atualizado_em": datetime.now().astimezone().isoformat()
            })

            if len(lote) >= 100:
                supabase.table("empresas").upsert(
                    lote, on_conflict="cnpj"
                ).execute()
                importadas += len(lote)
                lote = []

        if lote:
            supabase.table("empresas").upsert(
                lote, on_conflict="cnpj"
            ).execute()
            importadas += len(lote)

        flash(f"Importação concluída. Registros processados: {importadas}.")

    except Exception as erro:
        flash(f"Erro na importação: {str(erro)}")

    return redirect(url_for("inicio"))


@app.route("/exportar")
@exigir_login
def exportar_csv():
    try:
        resposta = (
            cliente_supabase()
            .table("empresas")
            .select("*")
            .limit(10000)
            .execute()
        )

        empresas = resposta.data or []
        saida = io.StringIO(newline="")
        campos_dados = sorted({
            campo
            for empresa in empresas
            for campo in (empresa.get("dados") or {}).keys()
        })

        campos = ["cnpj"] + campos_dados + CAMPOS_COMERCIAIS
        escritor = csv.DictWriter(
            saida,
            fieldnames=list(dict.fromkeys(campos)),
            delimiter=";"
        )
        escritor.writeheader()

        for empresa in empresas:
            linha = {"cnpj": empresa.get("cnpj", "")}
            linha.update(empresa.get("dados") or {})

            for campo in CAMPOS_COMERCIAIS:
                linha[campo] = empresa.get(campo, "")

            linha["visitou"] = (
                "Sim" if empresa.get("visitou") else "Não"
            )
            escritor.writerow(linha)

        memoria = io.BytesIO(saida.getvalue().encode("utf-8-sig"))
        memoria.seek(0)

        return send_file(
            memoria,
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name="cam_agrolog_empresas.csv"
        )

    except Exception as erro:
        return f"Erro ao exportar CSV: {str(erro)}", 500


if __name__ == "__main__":
    app.run(debug=True)