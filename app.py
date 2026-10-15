
from pathlib import Path
import csv
import re
import shutil
from datetime import datetime
from flask import send_file
from flask import (
    Flask, render_template, request, abort,
    redirect, url_for, flash
)

BASE_DIR = Path(__file__).resolve().parent
PASTA_DADOS = BASE_DIR / "data"
ARQUIVO_ACOMPANHAMENTO = PASTA_DADOS / "acompanhamento.csv"

app = Flask(__name__)
app.secret_key = "cam_agrolog_crm_chave_local"

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


def normalizar_cnpj(valor):
    return re.sub(r"\D", "", valor or "")


def carregar_empresas():
    arquivos = sorted(
    arquivo
    for arquivo in PASTA_DADOS.glob("*.csv")
    if arquivo.name.lower() != ARQUIVO_ACOMPANHAMENTO.name.lower()
    and not "_backup_" in arquivo.name.lower()
    and arquivo.name.lower() != "exportacao_empresas.csv"
    )

    if not arquivos:
        return [], [], "Nenhum CSV de empresas encontrado na pasta data."

    arquivo_csv = arquivos[0]

    with open(
        arquivo_csv, "r", encoding="utf-8-sig", newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=";")

        if not leitor.fieldnames:
            return [], [], "O CSV está vazio ou não possui cabeçalho."

        colunas = [c.strip() for c in leitor.fieldnames]
        empresas = []

        for linha in leitor:
            empresa = {}

            for chave, valor in linha.items():
                if chave is not None:
                    empresa[chave.strip()] = (valor or "").strip()

            empresas.append(empresa)

    return colunas, empresas, arquivo_csv.name


def carregar_acompanhamentos():
    if not ARQUIVO_ACOMPANHAMENTO.exists():
        return {}

    acompanhamentos = {}

    with open(
        ARQUIVO_ACOMPANHAMENTO,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=";")

        for linha in leitor:
            cnpj = normalizar_cnpj(linha.get("cnpj", ""))

            if cnpj:
                acompanhamentos[cnpj] = {
                    campo: (linha.get(campo) or "")
                    for campo in CAMPOS_COMERCIAIS
                    if campo != "cnpj"
                }

    return acompanhamentos


def salvar_acompanhamento(cnpj, dados):
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)

    acompanhamentos = carregar_acompanhamentos()

    acompanhamentos[cnpj] = {
        campo: (dados.get(campo) or "").strip()
        for campo in CAMPOS_COMERCIAIS
        if campo != "cnpj"
    }

    with open(
        ARQUIVO_ACOMPANHAMENTO,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=CAMPOS_COMERCIAIS,
            delimiter=";",
            extrasaction="ignore"
        )
        escritor.writeheader()

        for cnpj_salvo, dados_salvos in acompanhamentos.items():
            linha = {"cnpj": cnpj_salvo}
            linha.update(dados_salvos)
            escritor.writerow(linha)


@app.route("/importar", methods=["POST"])
def importar_empresas():
    arquivo_enviado = request.files.get("arquivo_csv")

    if not arquivo_enviado or not arquivo_enviado.filename:
        flash("Selecione um arquivo CSV para importar.")
        return redirect(url_for("inicio"))

    nome = Path(arquivo_enviado.filename).name

    if not nome.lower().endswith(".csv"):
        flash("O arquivo precisa estar no formato CSV.")
        return redirect(url_for("inicio"))

    try:
        conteudo = arquivo_enviado.read().decode("utf-8-sig")

        if not conteudo.strip():
            raise ValueError("O arquivo está vazio.")

        primeira_linha = conteudo.splitlines()[0]
        separador = ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","

        leitor = csv.DictReader(conteudo.splitlines(), delimiter=separador)

        if not leitor.fieldnames:
            raise ValueError("O arquivo não possui cabeçalho.")

        colunas_importadas = [
            (coluna or "").strip()
            for coluna in leitor.fieldnames
        ]

        if "cnpj" not in colunas_importadas:
            raise ValueError("Não encontrei a coluna CNPJ no arquivo.")

        novas_empresas = []

        for linha in leitor:
            empresa = {}

            for chave, valor in linha.items():
                if chave is not None:
                    empresa[chave.strip()] = (valor or "").strip()

            cnpj = normalizar_cnpj(empresa.get("cnpj", ""))

            if cnpj:
                empresa["cnpj"] = cnpj
                novas_empresas.append(empresa)

        if not novas_empresas:
            raise ValueError("Nenhuma empresa com CNPJ válido foi encontrada.")

        colunas, existentes, origem = carregar_empresas()

        if origem.startswith("Nenhum CSV") or origem.startswith("O CSV está"):
            raise ValueError("Não encontrei a base atual de empresas.")

        caminho_base = PASTA_DADOS / origem

        if not caminho_base.exists():
            raise ValueError("O arquivo principal de empresas não foi encontrado.")

        colunas_finais = list(colunas)

        for empresa in novas_empresas:
            for coluna in empresa:
                if coluna not in colunas_finais:
                    colunas_finais.append(coluna)

        cnpjs_existentes = {
            normalizar_cnpj(empresa.get("cnpj", ""))
            for empresa in existentes
        }

        adicionadas = 0

        for empresa in novas_empresas:
            cnpj = normalizar_cnpj(empresa.get("cnpj", ""))

            if cnpj and cnpj not in cnpjs_existentes:
                existentes.append(empresa)
                cnpjs_existentes.add(cnpj)
                adicionadas += 1

        PASTA_DADOS.mkdir(parents=True, exist_ok=True)

        # Faz uma cópia de segurança antes de alterar a base.
        data_backup = datetime.now().strftime("%Y%m%d_%H%M%S")
        caminho_backup = caminho_base.with_name(
            f"{caminho_base.stem}_backup_{data_backup}.csv"
        )
        shutil.copy2(caminho_base, caminho_backup)

        # Atualiza a base principal mantendo os registros já existentes.
        with open(
            caminho_base, "w", encoding="utf-8-sig", newline=""
        ) as arquivo:
            escritor = csv.DictWriter(
                arquivo,
                fieldnames=colunas_finais,
                delimiter=";",
                extrasaction="ignore"
            )
            escritor.writeheader()

            for empresa in existentes:
                escritor.writerow({
                    coluna: empresa.get(coluna, "")
                    for coluna in colunas_finais
                })

        flash(
            f"Importação concluída: {adicionadas} empresas adicionadas. "
            f"{len(novas_empresas) - adicionadas} já existiam ou eram duplicadas. "
            f"Backup criado: {caminho_backup.name}"
        )

    except (UnicodeDecodeError, csv.Error, ValueError, OSError) as erro:
        flash(f"Não foi possível importar o arquivo: {erro}")

    return redirect(url_for("inicio"))


@app.route("/exportar/empresas")
def exportar_empresas():
    colunas, empresas, origem = carregar_empresas()

    if origem.startswith("Nenhum CSV") or origem.startswith("O CSV está"):
        abort(404)

    arquivo_saida = PASTA_DADOS / "exportacao_empresas.csv"

    with open(
        arquivo_saida, "w", encoding="utf-8-sig", newline=""
    ) as arquivo:
        escritor = csv.DictWriter(
            arquivo,
            fieldnames=colunas,
            delimiter=";",
            extrasaction="ignore"
        )
        escritor.writeheader()
        escritor.writerows(empresas)

    return send_file(
        arquivo_saida,
        as_attachment=True,
        download_name="empresas.csv",
        mimetype="text/csv"
    )


@app.route("/exportar/acompanhamentos")
def exportar_acompanhamentos():
    if not ARQUIVO_ACOMPANHAMENTO.exists():
        flash("Ainda não existem acompanhamentos para exportar.")
        return redirect(url_for("inicio"))

    return send_file(
        ARQUIVO_ACOMPANHAMENTO,
        as_attachment=True,
        download_name="acompanhamento.csv",
        mimetype="text/csv"
    )


@app.route("/")
def inicio():
    colunas, empresas, origem = carregar_empresas()

    if origem.startswith("Nenhum CSV") or origem.startswith("O CSV está"):
        return origem, 404

    pesquisa = request.args.get("q", "").strip().lower()

    campos_pesquisa = [
        "cnpj",
        "razao_social",
        "nome_fantasia",
        "CONTATO 1",
        "CONTATO 2",
        "telefone_1",
        "telefone_2",
        "codigo_municipio",
        "uf",
        "email",
        "cnae_principal",
        "cnae_principal_codigo",
    ]

    if pesquisa:
        empresas = [
            empresa for empresa in empresas
            if any(
                pesquisa in empresa.get(campo, "").lower()
                for campo in campos_pesquisa
            )
        ]

    acompanhamentos = carregar_acompanhamentos()

    for empresa in empresas:
        cnpj = normalizar_cnpj(empresa.get("cnpj", ""))
        comercial = acompanhamentos.get(cnpj, {})
        empresa["status_comercial"] = comercial.get(
            "status_comercial", "Novo contato"
        )

    total = len(empresas)
    por_pagina = 50

    try:
        pagina = max(1, int(request.args.get("pagina", "1")))
    except ValueError:
        pagina = 1

    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = min(pagina, total_paginas)

    inicio_indice = (pagina - 1) * por_pagina
    empresas_pagina = empresas[
        inicio_indice:inicio_indice + por_pagina
    ]

    return render_template(
        "index.html",
        empresas=empresas_pagina,
        total=total,
        pesquisa=pesquisa,
        pagina=pagina,
        total_paginas=total_paginas,
        origem=origem,
    )


@app.route("/empresa/<cnpj>", methods=["GET"])
def empresa(cnpj):
    colunas, empresas, origem = carregar_empresas()

    if origem.startswith("Nenhum CSV") or origem.startswith("O CSV está"):
        abort(404)

    cnpj_procurado = normalizar_cnpj(cnpj)

    if not cnpj_procurado:
        abort(404)

    dados_empresa = next(
        (
            empresa for empresa in empresas
            if normalizar_cnpj(empresa.get("cnpj", "")) == cnpj_procurado
        ),
        None,
    )

    if dados_empresa is None:
        abort(404)

    definicao_grupos = [
        ("Identificação da empresa", [
            "cnpj", "identificador_matriz_filial", "razao_social",
            "nome_fantasia", "situacao_cadastral_codigo",
            "situacao_cadastral", "natureza_juridica", "porte_empresa",
            "capital_social", "ente_federativo_responsavel",
        ]),
        ("Atividade econômica", [
            "cnae_principal_codigo", "cnae_principal",
            "cnae_secundario_codigo", "cnae_secundario",
        ]),
        ("Endereço", [
            "tipo_logradouro", "logradouro", "numero", "complemento",
            "bairro", "cep", "codigo_municipio", "uf",
        ]),
        ("Contatos cadastrados", [
            "CONTATO 1", "STATUS CONTATO 1", "CONTATO 2",
            "STATUS CONTATO 2", "ddd_1", "telefone_1", "ddd_2",
            "telefone_2", "email",
        ]),
        ("Responsável pela empresa", [
            "qualificacao_responsavel", "cpf_representante_legal",
            "nome_representante_legal", "qualificacao_representante_legal",
        ]),
        ("Quadro societário", [
            "identificador_socio", "nome_socio", "cpf_cnpj_socio",
            "qualificacao_socio", "data_entrada_sociedade",
            "data_saida_sociedade", "faixa_etaria",
        ]),
    ]

    grupos = []
    campos_usados = set()

    for titulo, campos in definicao_grupos:
        itens = [
            (campo, dados_empresa.get(campo, ""))
            for campo in campos
            if campo in colunas
        ]

        if itens:
            grupos.append((titulo, itens))
            campos_usados.update(campo for campo, valor in itens)

    outros = [
        (campo, dados_empresa.get(campo, ""))
        for campo in colunas
        if campo not in campos_usados
    ]

    if outros:
        grupos.append(("Outros dados", outros))

    acompanhamento = carregar_acompanhamentos().get(
        cnpj_procurado,
        {
            campo: ""
            for campo in CAMPOS_COMERCIAIS
            if campo != "cnpj"
        }
    )

    if not acompanhamento.get("status_comercial"):
        acompanhamento["status_comercial"] = "Novo contato"

    return render_template(
        "empresa.html",
        empresa=dados_empresa,
        grupos=grupos,
        origem=origem,
        acompanhamento=acompanhamento,
    )


@app.route("/empresa/<cnpj>/salvar", methods=["POST"])
def salvar_empresa(cnpj):
    cnpj_procurado = normalizar_cnpj(cnpj)

    if not cnpj_procurado:
        abort(404)

    _, empresas, origem = carregar_empresas()

    if origem.startswith("Nenhum CSV") or origem.startswith("O CSV está"):
        abort(404)

    existe = any(
        normalizar_cnpj(empresa.get("cnpj", "")) == cnpj_procurado
        for empresa in empresas
    )

    if not existe:
        abort(404)

    dados = {
        campo: request.form.get(campo, "")
        for campo in CAMPOS_COMERCIAIS
        if campo != "cnpj"
    }

    salvar_acompanhamento(cnpj_procurado, dados)

    flash("Acompanhamento comercial salvo com sucesso!")
    return redirect(url_for("empresa", cnpj=cnpj_procurado))


if __name__ == "__main__":
    app.run(debug=True)
