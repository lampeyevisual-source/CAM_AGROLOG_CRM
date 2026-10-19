
import csv
import io
import os
import re
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_file,
)
from supabase import create_client


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "troque-esta-chave-no-render")


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


AREAS_CAM = {
    "agrolog": {
        "nome": "CAM AGROLOG",
        "descricao": "Compra e venda de grãos.",
    },
    "distribuicao": {
        "nome": "CAM DISTRIBUIÇÃO",
        "descricao": "Clientes de ovos e outras mercadorias.",
    },
    "agrologistica": {
        "nome": "CAM AGROLOGÍSTICA",
        "descricao": "Fretes, caminhoneiros e transportadoras.",
    },
}


# =========================================================
# SUPABASE
# =========================================================

def cliente_supabase():
    url = os.getenv("SUPABASE_URL")
    chave = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
    )

    if not url or not chave:
        raise RuntimeError(
            "Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY "
            "nas variáveis de ambiente."
        )

    return create_client(url, chave)


def exigir_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("crm_autorizado"):
            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


def carregar_base(base_id):
    resposta = (
        cliente_supabase()
        .table("bases_csv")
        .select("*")
        .eq("id", base_id)
        .limit(1)
        .execute()
    )

    return resposta.data[0] if resposta.data else None


def carregar_registros(base_id, limite=10000):
    resposta = (
        cliente_supabase()
        .table("base_registros")
        .select("*")
        .eq("base_id", base_id)
        .order("id")
        .limit(limite)
        .execute()
    )

    return resposta.data or []


# =========================================================
# LEITURA E TRATAMENTO DO CSV
# =========================================================

def parse_csv_upload(arquivo):
    conteudo = arquivo.read()

    if not conteudo:
        raise ValueError("O arquivo está vazio.")

    texto = None

    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = conteudo.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if texto is None:
        raise ValueError(
            "Não foi possível ler a codificação do arquivo CSV."
        )

    amostra = texto[:8192]

    try:
        dialeto = csv.Sniffer().sniff(
            amostra,
            delimiters=";,\t|",
        )
        delimitador = dialeto.delimiter

    except csv.Error:
        primeira_linha = (
            amostra.splitlines()[0]
            if amostra.splitlines()
            else ""
        )

        delimitador = max(
            (",", ";", "\t", "|"),
            key=primeira_linha.count,
        )

    leitor = csv.DictReader(
        io.StringIO(texto),
        delimiter=delimitador,
    )

    if not leitor.fieldnames:
        raise ValueError(
            "O CSV precisa ter uma primeira linha com os nomes das colunas."
        )

    campos = [
        str(campo or "").strip()
        for campo in leitor.fieldnames
    ]

    if not any(campos):
        raise ValueError(
            "Não foram encontrados nomes de colunas no CSV."
        )

    linhas = []

    for linha in leitor:
        registro = {}

        for original, campo in zip(leitor.fieldnames, campos):
            if campo:
                valor = linha.get(original, "")
                registro[campo] = (
                    str(valor).strip()
                    if valor is not None
                    else ""
                )

        if any(str(valor).strip() for valor in registro.values()):
            linhas.append(registro)

    if not linhas:
        raise ValueError(
            "O CSV não contém registros para importar."
        )

    return linhas


def identificar_registro(dados):
    preferidos = (
        "cnpj",
        "cpf",
        "documento",
        "telefone",
        "telefone_1",
        "celular",
        "email",
        "e-mail",
        "id",
        "codigo",
        "nome_fantasia",
        "razao_social",
        "nome",
    )

    normalizados = {
        re.sub(r"[^a-z0-9]", "", str(chave).lower()): chave
        for chave in dados
    }

    for nome in preferidos:
        chave_normalizada = re.sub(
            r"[^a-z0-9]",
            "",
            nome.lower(),
        )

        if chave_normalizada in normalizados:
            chave_original = normalizados[chave_normalizada]
            valor = str(dados.get(chave_original, "")).strip()

            if valor:
                return valor

    for valor in dados.values():
        if str(valor).strip():
            return str(valor).strip()

    return "sem-identificador"


# =========================================================
# DADOS COMERCIAIS E FICHA DA EMPRESA
# =========================================================

def acompanhamento_do(registro):
    dados = registro.get("dados") or {}
    acompanhamento = dados.get("acompanhamento") or {}

    resultado = {
        campo: acompanhamento.get(campo, "")
        for campo in CAMPOS_COMERCIAIS
    }

    resultado["atualizado_em"] = acompanhamento.get(
        "atualizado_em",
        "",
    )

    return resultado


def montar_grupos(empresa):
    grupos_campos = [
        (
            "Identificação",
            [
                "cnpj",
                "identificador_matriz_filial",
                "razao_social",
                "nome_fantasia",
                "situacao_cadastral",
                "data_inicio_atividade",
            ],
        ),
        (
            "Contato",
            [
                "ddd_1",
                "telefone_1",
                "ddd_2",
                "telefone_2",
                "email",
            ],
        ),
        (
            "Endereço",
            [
                "tipo_logradouro",
                "logradouro",
                "numero",
                "complemento",
                "bairro",
                "cep",
                "municipio",
                "codigo_municipio",
                "uf",
            ],
        ),
        ("Informações adicionais", None),
    ]

    chaves_usadas = set()
    grupos = []

    for titulo, chaves in grupos_campos:
        if chaves is None:
            itens = [
                (chave, valor)
                for chave, valor in empresa.items()
                if chave not in chaves_usadas
                and chave != "acompanhamento"
                and str(valor or "").strip()
            ]
        else:
            itens = [
                (
                    chave.replace("_", " ").title(),
                    empresa.get(chave, ""),
                )
                for chave in chaves
                if chave in empresa
            ]

            chaves_usadas.update(chaves)

        if itens:
            grupos.append((titulo, itens))

    return grupos


# =========================================================
# INÍCIO E LOGIN
# =========================================================

@app.route("/")
def raiz():
    if session.get("crm_autorizado"):
        return redirect(url_for("acessos"))

    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None
    senha_configurada = os.getenv("CRM_ACCESS_PASSWORD")

    if request.method == "POST":
        senha_digitada = request.form.get("senha", "")

        if not senha_configurada:
            erro = (
                "A senha de acesso não está configurada no servidor. "
                "Configure CRM_ACCESS_PASSWORD."
            )

        elif senha_digitada and senha_digitada == senha_configurada:
            session.clear()
            session["crm_autorizado"] = True

            return redirect(url_for("acessos"))

        else:
            erro = "Senha incorreta."

    return render_template("login.html", erro=erro)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# TELA DE ACESSOS
# =========================================================

@app.route("/acessos")
@exigir_login
def acessos():
    return render_template(
        "acessos.html",
        areas=AREAS_CAM,
    )


# =========================================================
# LISTAGEM DAS BASES
# =========================================================

@app.route("/bases/<area>")
@exigir_login
def bases(area):
    if area not in AREAS_CAM:
        flash("Área não encontrada.")
        return redirect(url_for("acessos"))

    try:
        db = cliente_supabase()

        resposta = (
            db.table("bases_csv")
            .select("*")
            .eq("area", area)
            .order("criado_em", desc=True)
            .limit(1000)
            .execute()
        )

        lista_bases = resposta.data or []

        for base in lista_bases:
            try:
                total = (
                    db.table("base_registros")
                    .select("id", count="exact")
                    .eq("base_id", base["id"])
                    .limit(1)
                    .execute()
                )

                base["total_registros"] = (
                    getattr(total, "count", None) or 0
                )

            except Exception:
                base["total_registros"] = 0

        return render_template(
            "base.html",
            area=AREAS_CAM[area],
            chave_area=area,
            lista_bases=lista_bases,
        )

    except Exception as exc:
        app.logger.exception("Erro ao carregar bases")

        flash(f"Não foi possível carregar as bases: {exc}")

        return render_template(
            "base.html",
            area=AREAS_CAM[area],
            chave_area=area,
            lista_bases=[],
        )


# =========================================================
# IMPORTAÇÃO DE UMA BASE CSV
# =========================================================

@app.route("/bases/<area>/importar", methods=["POST"])
@exigir_login
def importar_base(area):
    if area not in AREAS_CAM:
        flash("Área não encontrada.")
        return redirect(url_for("acessos"))

    nome = request.form.get("nome", "").strip()
    arquivo = request.files.get("arquivo")

    if not nome or not arquivo or not arquivo.filename:
        flash("Informe o nome da base e selecione um arquivo CSV.")
        return redirect(url_for("bases", area=area))

    base_id = None

    try:
        linhas = parse_csv_upload(arquivo)
        db = cliente_supabase()

        agora = datetime.utcnow().isoformat()

        resposta = (
            db.table("bases_csv")
            .insert(
                {
                    "nome": nome,
                    "area": area,
                    "criado_em": agora,
                }
            )
            .execute()
        )

        if not resposta.data:
            raise RuntimeError(
                "O Supabase não retornou a base criada."
            )

        base_id = resposta.data[0]["id"]

        payload = [
            {
                "base_id": base_id,
                "identificador": identificar_registro(linha),
                "dados": linha,
            }
            for linha in linhas
        ]

        for inicio in range(0, len(payload), 500):
            db.table("base_registros").insert(
                payload[inicio:inicio + 500]
            ).execute()

        flash(
            f"Base importada com sucesso: {len(linhas)} registros."
        )

        return redirect(url_for("bases", area=area))

    except Exception as exc:
        app.logger.exception("Erro na importação CSV")

        # Se a base foi criada mas a importação dos registros falhou,
        # tenta remover os dados parciais para não deixar uma base vazia.
        if base_id is not None:
            try:
                db = cliente_supabase()
                db.table("base_registros").delete().eq(
                    "base_id", base_id
                ).execute()
                db.table("bases_csv").delete().eq(
                    "id", base_id
                ).execute()
            except Exception:
                app.logger.exception(
                    "Não foi possível limpar a importação incompleta."
                )

        flash(f"Falha ao importar o CSV: {exc}")
        return redirect(url_for("bases", area=area))


# =========================================================
# DETALHES DA BASE
# =========================================================

@app.route("/base/<int:base_id>")
@exigir_login
def base_detalhe(base_id):
    try:
        base = carregar_base(base_id)

        if not base:
            flash("Base não encontrada.")
            return redirect(url_for("acessos"))

        registros_originais = carregar_registros(base_id)
        q = request.args.get("q", "").strip().lower()

        registros = []

        for original in registros_originais:
            registro = dict(original)
            dados = dict(registro.get("dados") or {})

            acompanhamento = dados.pop("acompanhamento", {}) or {}
            registro["dados"] = dados

            for campo in CAMPOS_COMERCIAIS:
                registro[campo] = acompanhamento.get(campo, "")

            if q:
                partes = [
                    str(registro.get("identificador", "")),
                ]

                for chave, valor in dados.items():
                    partes.append(str(chave))
                    partes.append(str(valor))

                texto = " ".join(partes).lower()

                if q not in texto:
                    continue

            registros.append(registro)

        return render_template(
            "base_detalhe.html",
            base=base,
            areas=AREAS_CAM,
            registros=registros,
            q=q,
        )

    except Exception as exc:
        app.logger.exception("Erro ao abrir detalhes da base")
        flash(f"Não foi possível abrir esta base: {exc}")
        return redirect(url_for("acessos"))


# =========================================================
# EDIÇÃO DO ACOMPANHAMENTO COMERCIAL
# =========================================================

@app.route(
    "/base-registro/<int:registro_id>",
    methods=["GET", "POST"],
)
@exigir_login
def registro_editar(registro_id):
    try:
        db = cliente_supabase()

        resposta = (
            db.table("base_registros")
            .select("*")
            .eq("id", registro_id)
            .limit(1)
            .execute()
        )

        if not resposta.data:
            flash("Registro não encontrado.")
            return redirect(url_for("acessos"))

        registro = resposta.data[0]
        dados = dict(registro.get("dados") or {})
        acompanhamento = dict(
            dados.get("acompanhamento") or {}
        )

        if request.method == "POST":
            for campo in CAMPOS_COMERCIAIS:
                acompanhamento[campo] = (
                    request.form.get(campo, "").strip()
                )

            valor = acompanhamento.get(
                "valor_ultima_compra",
                "",
            )

            if valor:
                try:
                    valor_normalizado = (
                        str(valor)
                        .replace("R$", "")
                        .replace(" ", "")
                    )

                    if "," in valor_normalizado:
                        valor_normalizado = (
                            valor_normalizado
                            .replace(".", "")
                            .replace(",", ".")
                        )

                    acompanhamento["valor_ultima_compra"] = float(
                        valor_normalizado
                    )

                except ValueError:
                    flash(
                        "O valor da última compra não é válido."
                    )
                    return redirect(
                        url_for(
                            "registro_editar",
                            registro_id=registro_id,
                        )
                    )

            else:
                acompanhamento["valor_ultima_compra"] = ""

            acompanhamento["atualizado_em"] = (
                datetime.utcnow().isoformat()
            )

            # O acompanhamento fica dentro de "dados", preservando
            # todas as colunas originais do CSV.
            dados["acompanhamento"] = acompanhamento

            (
                db.table("base_registros")
                .update({"dados": dados})
                .eq("id", registro_id)
                .execute()
            )

            flash("Acompanhamento comercial salvo.")

            return redirect(
                url_for(
                    "registro_editar",
                    registro_id=registro_id,
                )
            )

        empresa = {
            chave: valor
            for chave, valor in dados.items()
            if chave != "acompanhamento"
        }

        if (
            not empresa.get("cnpj")
            and registro.get("identificador")
        ):
            empresa["cnpj"] = registro["identificador"]

        grupos = montar_grupos(empresa)

        return render_template(
            "empresa_editar.html",
            registro=registro,
            empresa=empresa,
            dados=empresa,
            acompanhamento=acompanhamento,
            grupos=grupos,
        )

    except Exception as exc:
        app.logger.exception("Erro ao editar registro")
        flash(
            f"Não foi possível abrir ou salvar o registro: {exc}"
        )
        return redirect(url_for("acessos"))


# =========================================================
# EXPORTAÇÃO DA BASE PARA CSV
# =========================================================

@app.route("/bases/<int:base_id>/exportar")
@exigir_login
def exportar_base_csv(base_id):
    try:
        base = carregar_base(base_id)

        if not base:
            flash("Base não encontrada.")
            return redirect(url_for("acessos"))

        registros = carregar_registros(base_id)

        campos = []
        linhas = []

        for registro in registros:
            dados = dict(registro.get("dados") or {})

            # Não exporta o acompanhamento como se fosse uma coluna
            # original do CSV.
            dados.pop("acompanhamento", None)

            for campo in dados:
                if campo not in campos:
                    campos.append(campo)

            linhas.append(dados)

        if not campos:
            campos = ["identificador"]

        saida = io.StringIO(newline="")

        escritor = csv.DictWriter(
            saida,
            fieldnames=campos,
            extrasaction="ignore",
        )

        escritor.writeheader()

        for linha in linhas:
            escritor.writerow(
                {
                    campo: linha.get(campo, "")
                    for campo in campos
                }
            )

        buffer = io.BytesIO(
            saida.getvalue().encode("utf-8-sig")
        )
        buffer.seek(0)

        nome = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            base.get("nome", "base"),
        )

        return send_file(
            buffer,
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name=f"{nome}.csv",
        )

    except Exception as exc:
        app.logger.exception("Erro ao exportar base")
        flash(f"Não foi possível exportar a base: {exc}")
        return redirect(url_for("acessos"))


# =========================================================
# EXCLUSÃO DA BASE E DE SEUS REGISTROS
# =========================================================

@app.route(
    "/bases/<int:base_id>/excluir",
    methods=["POST"],
)
@exigir_login
def excluir_base(base_id):
    try:
        db = cliente_supabase()
        base = carregar_base(base_id)

        if not base:
            flash("Base não encontrada.")
            return redirect(url_for("acessos"))

        area = base.get("area")

        if area not in AREAS_CAM:
            area = "agrolog"

        # Primeiro exclui os registros vinculados à base.
        (
            db.table("base_registros")
            .delete()
            .eq("base_id", base_id)
            .execute()
        )

        # Depois exclui a base.
        (
            db.table("bases_csv")
            .delete()
            .eq("id", base_id)
            .execute()
        )

        flash("Base e registros excluídos.")

        return redirect(url_for("bases", area=area))

    except Exception as exc:
        app.logger.exception("Erro ao excluir base")
        flash(f"Não foi possível excluir a base: {exc}")
        return redirect(url_for("acessos"))


# =========================================================
# COMPATIBILIDADE COM O LINK ANTIGO
# =========================================================

@app.route("/inicio")
@exigir_login
def inicio():
    return redirect(url_for("acessos"))


# =========================================================
# EXECUÇÃO LOCAL
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
