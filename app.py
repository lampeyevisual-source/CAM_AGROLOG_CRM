import csv
import io
import os
import re
from datetime import datetime
from functools import wraps
from pathlib import Path
from werkzeug.utils import secure_filename
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

@app.route("/login", methods=["GET", "POST"])
def login():
    senha_configurada = os.environ.get("CRM_ACCESS_PASSWORD", "")
    erro = None

    if request.method == "POST":
        senha_informada = request.form.get("senha", "")

        if not senha_configurada:
            erro = "Configure CRM_ACCESS_PASSWORD no Render."
        elif senha_informada == senha_configurada:
            session["crm_autorizado"] = True
            return redirect(url_for("acessos"))
        else:
            erro = "Senha incorreta."

    return render_template("login.html", erro=erro)

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

    return render_template(
        "bases.html",
        area=AREAS_CAM[area],
        chave_area=area,
        lista_bases=lista_bases
    )

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
        # 1. Ler o CSV usando a função que já existe
        linhas = ler_csv_upload(arquivo)

        # 2. Criar a pasta correspondente à área
        pasta_bases = (
            Path(__file__).resolve().parent
            / "data"
            / "bases"
            / area
        )
        pasta_bases.mkdir(parents=True, exist_ok=True)

        # 3. Preparar o nome do arquivo
        nome_arquivo = secure_filename(arquivo.filename)

        if not nome_arquivo:
            raise ValueError("O nome do arquivo CSV é inválido.")

        # 4. Evitar sobrescrever arquivos anteriores
        destino = pasta_bases / nome_arquivo
        contador = 1

        while destino.exists():
            destino = pasta_bases / (
                f"{Path(nome_arquivo).stem}_{contador}"
                f"{Path(nome_arquivo).suffix}"
            )
            contador += 1

        # 5. Salvar uma cópia do CSV original
        arquivo.seek(0)
        destino.write_bytes(arquivo.read())

        # 6. Registrar a base no Supabase
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
            raise RuntimeError("Não foi possível criar a base no Supabase.")

        base_id = criada.data[0]["id"]

        # 7. Importar os registros em lotes de 100
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

        flash(
            f"Base importada: {len(linhas)} registros. "
            f"CSV salvo em data/bases/{area}/."
        )

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
                        str(v)
                        for v in (registro.get("dados") or {}).values()
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

        return render_template(
            "base_detalhe.html",
            base=base,
            registros=registros,
            colunas=colunas,
            areas=AREAS_CAM,
            q=request.args.get("q", "")
        )

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

            atualizacoes["visitou"] = (
                request.form.get("visitou") == "sim"
            )

            valor = atualizacoes.get("valor_ultima_compra")

            if valor:
                try:
                    atualizacoes["valor_ultima_compra"] = float(
                        valor.replace(".", "").replace(",", ".")
                    )
                except ValueError:
                    flash("Valor da compra inválido.")
                    return redirect(
                        url_for(
                            "registro_editar",
                            registro_id=registro_id
                        )
                    )

            atualizacoes["atualizado_em"] = (
                datetime.now().astimezone().isoformat()
            )

            (
                supabase.table("base_registros")
                .update(atualizacoes)
                .eq("id", registro_id)
                .execute()
            )

            flash("Dados comerciais salvos.")
            return redirect(
                url_for(
                    "registro_editar",
                    registro_id=registro_id
                )
            )

        return render_template(
            "registro_editar.html",
            registro=registro,
            dados=dados
        )

    except Exception as erro:
        return f"Erro ao editar registro: {erro}", 500

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
                    + [
                        str(empresa.get(campo, "") or "")
                        for campo in CAMPOS_COMERCIAIS
                    ]
                ).lower()

                if termo in texto:
                    filtradas.append(empresa)

            empresas = filtradas

        return render_template(
            "inicio.html",
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
                    return redirect(
                        url_for("empresa_editar", cnpj=cnpj)
                    )

            atualizacoes["atualizado_em"] = (
                datetime.now().astimezone().isoformat()
            )

            (
                supabase.table("empresas")
                .update(atualizacoes)
                .eq("cnpj", cnpj)
                .execute()
            )

            flash("Dados comerciais salvos.")
            return redirect(
                url_for("empresa_editar", cnpj=cnpj)
            )

        return render_template(
            "empresa_editar.html",
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