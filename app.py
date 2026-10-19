import csv
import io
import os
import re
import hashlib
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
    import hashlib
    import io

    if area not in AREAS_CAM:
        flash("Área não encontrada.")
        return redirect(url_for("acessos"))

    arquivo = request.files.get("arquivo")
    nome = request.form.get("nome", "").strip()

    if not arquivo or not arquivo.filename or not nome:
        flash("Informe o nome da base e selecione um CSV.")
        return redirect(url_for("bases", area=area))

    base_id = None
    supabase = None

    try:
        nome_arquivo = secure_filename(arquivo.filename)

        if not nome_arquivo:
            raise ValueError("Nome de arquivo inválido.")

        # Lê o arquivo e calcula a assinatura do conteúdo.
        conteudo_csv = arquivo.read()
        checksum = hashlib.sha256(conteudo_csv).hexdigest()

        if not conteudo_csv:
            raise ValueError("O arquivo CSV está vazio.")

        # Lê o CSV antes de criar ou alterar a base.
        linhas = ler_csv_upload(io.BytesIO(conteudo_csv))

        if not linhas:
            raise ValueError("O CSV não contém registros para importar.")

        supabase = cliente_supabase()

        # Procura o mesmo conteúdo na mesma área.
        existente = (
            supabase.table("bases_csv")
            .select("id,nome,status_importacao")
            .eq("area", area)
            .eq("checksum_csv", checksum)
            .limit(1)
            .execute()
        )

        if existente.data:
            base = existente.data[0]
            base_id = base["id"]

            if base.get("status_importacao") == "concluida":
                flash(
                    f"Este CSV já foi importado. "
                    f"Base existente: {base['nome']}."
                )
                return redirect(url_for("bases", area=area))

            # A base existe, mas não foi concluída.
            # Reinicia somente os registros vinculados a ela.
            supabase.table("bases_csv").update({
                "status_importacao": "em_andamento"
            }).eq("id", base_id).execute()

            supabase.table("base_registros") \
                .delete() \
                .eq("base_id", base_id) \
                .execute()

        else:
            # Cria uma nova base.
            criada = (
                supabase.table("bases_csv")
                .insert({
                    "area": area,
                    "nome": nome,
                    "arquivo_original": arquivo.filename,
                    "total_registros": len(linhas),
                    "checksum_csv": checksum,
                    "status_importacao": "em_andamento"
                })
                .execute()
            )

            if not criada.data:
                raise RuntimeError(
                    "Não foi possível cadastrar a base no Supabase."
                )

            base_id = criada.data[0]["id"]

        # Guarda o CSV original no Supabase Storage.
        caminho_storage = f"{area}/{base_id}/{nome_arquivo}"

        try:
            supabase.storage.from_("bases-csv").upload(
                path=caminho_storage,
                file=conteudo_csv,
                file_options={
                    "content-type": "text/csv",
                    "upsert": "true"
                }
            )
        except Exception:
            # Tenta atualizar o arquivo caso ele já exista.
            supabase.storage.from_("bases-csv").update(
                path=caminho_storage,
                file=conteudo_csv,
                file_options={"content-type": "text/csv"}
            )

        # Importa em lotes de 200 registros.
        for inicio in range(0, len(linhas), 200):
            lote = [
                {
                    "base_id": base_id,
                    "identificador": identificar_registro(dados),
                    "dados": dados
                }
                for dados in linhas[inicio:inicio + 200]
            ]

            if lote:
                (
                    supabase.table("base_registros")
                    .insert(lote)
                    .execute()
                )

        # Marca como concluída somente após todos os lotes.
        supabase.table("bases_csv").update({
            "status_importacao": "concluida"
        }).eq("id", base_id).execute()

        flash(
            f"Base importada com sucesso: {len(linhas)} registros."
        )

    except Exception as erro:
        app.logger.exception("Erro ao importar base CSV")

        if base_id and supabase:
            try:
                supabase.table("bases_csv").update({
                    "status_importacao": "erro"
                }).eq("id", base_id).execute()
            except Exception:
                app.logger.exception(
                    "Não foi possível atualizar o status da base."
                )

        flash(
            "A importação não foi concluída. "
            "Confira os logs do Render antes de tentar novamente. "
            f"Detalhe: {erro}"
        )

    return redirect(url_for("bases", area=area))


@app.route("/base-detalhe/<string:base_id>")
@exigir_login
def base_detalhe(base_id):
    try:
        supabase = cliente_supabase()

        # Busca a base selecionada.
        resultado = (
            supabase.table("bases_csv")
            .select("*")
            .eq("id", base_id)
            .limit(1)
            .execute()
        )

        if not resultado.data:
            flash("Base não encontrada.")
            return redirect(url_for("acessos"))

        base = resultado.data[0]

        # Pesquisa textual.
        q = request.args.get("q", "").strip()

        # Carrega TODOS os registros em lotes de 1.000.
        registros = []
        tamanho_lote = 1000
        inicio = 0

        while True:
            resposta = (
                supabase.table("base_registros")
                .select("*")
                .eq("base_id", base_id)
                .order("id")
                .range(inicio, inicio + tamanho_lote - 1)
                .execute()
            )

            lote = resposta.data or []
            registros.extend(lote)

            if len(lote) < tamanho_lote:
                break

            inicio += tamanho_lote

        # Aplica a pesquisa sobre todos os registros carregados.
        if q:
            termo = q.casefold()
            registros = [
                registro for registro in registros
                if termo in str(registro.get("dados") or {}).casefold()
            ]

        # Descobre as colunas originais do CSV.
        colunas = []
        for registro in registros:
            dados = registro.get("dados") or {}
            for coluna in dados:
                if coluna not in colunas:
                    colunas.append(coluna)

        return render_template(
            "base_detalhe.html",
            base=base,
            areas=AREAS_CAM,
            registros=registros,
            colunas=colunas,
            q=q
        )

    except Exception as erro:
        app.logger.exception("Erro ao abrir detalhes da base")
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

        if not isinstance(dados, dict):
            dados = {}

        # Dados do acompanhamento comercial
        acompanhamento = dados.get("acompanhamento", {})

        if not isinstance(acompanhamento, dict):
            acompanhamento = {}

        if request.method == "POST":
            atualizacoes = {
                campo: request.form.get(campo, "").strip() or None
                for campo in CAMPOS_COMERCIAIS
            }

            # Corrige a leitura do campo "Visitou a empresa?"
            visitou = request.form.get("visitou", "").strip().lower()

            if visitou in ("sim", "true", "1"):
                atualizacoes["visitou"] = True
            elif visitou in ("não", "nao", "false", "0"):
                atualizacoes["visitou"] = False
            else:
                atualizacoes["visitou"] = None

            valor = request.form.get("valor_ultima_compra", "").strip()

            if valor:
                try:
                    valor_normalizado = valor.replace(" ", "")

                    if "," in valor_normalizado:
                        valor_normalizado = (
                            valor_normalizado
                            .replace(".", "")
                            .replace(",", ".")
                        )

                    atualizacoes["valor_ultima_compra"] = float(
                        valor_normalizado
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

        # Monta os grupos para exibição dos dados cadastrais.
        # Os nomes e campos devem corresponder à estrutura de dados da base.
        grupos = []

        for chave, valor in dados.items():
            if chave == "acompanhamento":
                continue

            if isinstance(valor, dict):
                itens = list(valor.items())
                if itens:
                    grupos.append((chave.replace("_", " ").title(), itens))
            elif isinstance(valor, list):
                itens = [
                    (str(i + 1), item)
                    for i, item in enumerate(valor)
                ]
                if itens:
                    grupos.append((chave.replace("_", " ").title(), itens))
            else:
                grupos.append((
                    chave.replace("_", " ").title(),
                    [(chave.replace("_", " ").title(), valor)]
                ))

        return render_template(
            "empresas_editar.html",
            empresa=dados,
            registro=registro,
            dados=dados,
            acompanhamento=acompanhamento,
            grupos=grupos
        )

    except Exception as erro:
        app.logger.exception(
            "Erro ao editar registro %s",
            registro_id
        )
        return f"Erro ao editar registro: {erro}", 500

@app.route("/")
@exigir_login
def inicio():
    q = request.args.get("q", "").strip()

    try:
        supabase = cliente_supabase()
        empresas = []
        tamanho_lote = 1000
        inicio_lote = 0

        while True:
            resposta = (
                supabase
                .table("empresas")
                .select("*")
                .order("atualizado_em", desc=True)
                .range(inicio_lote, inicio_lote + tamanho_lote - 1)
                .execute()
            )

            lote = resposta.data or []
            empresas.extend(lote)

            if len(lote) < tamanho_lote:
                break

            inicio_lote += tamanho_lote


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
            "login.html",
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