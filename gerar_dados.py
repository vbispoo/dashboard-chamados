"""
Gerador de dados estáticos para o Dashboard de Chamados.

Executa a query no portal Apdata, salva o resultado em dados.json
e faz git add + commit + push automaticamente.

Execute manualmente ou agende no Agendador de Tarefas do Windows
para rodar a cada 2 minutos.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env com as credenciais
load_dotenv(Path(__file__).parent / ".env")

import Requisicao

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR       = Path(__file__).parent
DADOS_JSON     = BASE_DIR / "dados.json"
DIAS_UTEIS_REF = 22

NOMES_MESES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

SQL_CHAMADOS = """
SELECT
    PDD_CdiPessoaContrato               AS IdResponsavel,
    PDD_DssNome                         AS NomeResponsavel,
    MONTH(CHA_DtdChamada_Fim)           AS Mes,
    COUNT(DISTINCT CHA_CdiChamada)      AS TotalChamados
FROM Contratos,
     Chamadas,
     ChamadasOcorrencias,
     ContratosItensxPessoas,
     ContratosItens,
     PessoasContratos,
     TiposChamadas,
     StatusChamadas,
     EquipesAtendimentos
WHERE (AHX_CdiContratoItemxPessoa_At = HFE_CdiContratoItemxPessoa)
  AND (HFE_CdiContratoItem           = PBB_CdiContratoItem)
  AND (PBB_CdiContrato               = PBG_CdiContrato)
  AND (HFE_CdiPessoaContrato         = PDD_CdiPessoaContrato)
  AND (CHA_CdiTipoChamada            = TCH_CdiTipoChamada)
  AND (CHA_CdiStatusChamada          = SCH_CdiStatusChamada)
  AND (AHX_CdiEquipeAtendimento      = AJG_CdiEquipeAtendimento)
  AND (CHA_CdiChamada                = AHX_CdiChamada)
  AND (CHA_DtdChamada_Fim            >= DATEADD(year, -6, GETDATE()))
  AND (PDD_CdiPessoaContrato         > 0)
  AND (AHX_CdiOcorrenciaChamada      IN (51, 52, 59, 66, 67, 85, 86, 93, 158, 159, 1023, 1024, 1032))
  AND (SCH_CdiStatusChamada          = 31)
  AND (AJG_D2sEquipeAtendimento      IS NOT NULL)
  AND (AJG_D2sEquipeAtendimento      NOT LIKE 'None')
  AND (YEAR(CHA_DtdChamada_Fim)      = YEAR(GETDATE()))
  AND (AJG_CdiEquipeAtendimento      IN (57))
GROUP BY
    PDD_CdiPessoaContrato,
    PDD_DssNome,
    MONTH(CHA_DtdChamada_Fim)
ORDER BY
    PDD_DssNome,
    Mes
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login() -> bool:
    Requisicao.cookiesLogin.clear()
    Requisicao.requisicaoLogin()
    return bool(Requisicao.cookiesLogin)


def _executar_com_retry(sql: str) -> list | None:
    if not Requisicao.cookiesLogin:
        if not _login():
            return None
    rows = Requisicao.executaComando(sql)
    if rows is None:
        logger.warning("Sessão expirada — reautenticando...")
        if not _login():
            return None
        rows = Requisicao.executaComando(sql)
    return rows


def _get_field(row: dict, index: int):
    return row.get(f"Field_{index}", row.get(f"Campo_{index}"))


def _parse_equipe(rows: list) -> list:
    pessoas: dict[int, dict] = {}
    for row in rows:
        if isinstance(row, dict):
            pid      = int(_get_field(row, 0) or 0)
            nome     = str(_get_field(row, 1) or "")
            mes      = int(_get_field(row, 2) or 0)
            chamados = int(_get_field(row, 3) or 0)
        elif isinstance(row, (list, tuple)):
            pid, nome, mes, chamados = int(row[0]), str(row[1]), int(row[2]), int(row[3])
        else:
            continue

        if mes < 1 or mes > 12 or pid <= 0:
            continue

        if pid not in pessoas:
            pessoas[pid] = {"id": pid, "nome": nome, "meses": []}

        dias_trabalhados = DIAS_UTEIS_REF
        chamados_por_dia = round(chamados / dias_trabalhados, 2)
        chamados_norm    = round(chamados_por_dia * DIAS_UTEIS_REF)

        pessoas[pid]["meses"].append({
            "mes":             mes,
            "nome":            NOMES_MESES[mes],
            "chamados":        chamados,
            "ferias":          0,
            "diasTrabalhados": dias_trabalhados,
            "chamadosPorDia":  chamados_por_dia,
            "chamadosNorm":    chamados_norm,
        })

    resultado = []
    for p in pessoas.values():
        p["meses"].sort(key=lambda m: m["mes"])
        resultado.append(p)
    resultado.sort(key=lambda p: p["nome"])
    return resultado


def _git_push(mensagem: str) -> bool:
    """Faz git add dados.json + commit + push. Retorna True se OK."""
    try:
        subprocess.run(
            ["git", "add", "dados.json"],
            cwd=BASE_DIR, check=True, capture_output=True
        )
        # Verifica se há algo para commitar
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=BASE_DIR, capture_output=True
        )
        if result.returncode == 0:
            logger.info("Nenhuma alteração nos dados — push ignorado.")
            return True

        subprocess.run(
            ["git", "commit", "-m", mensagem],
            cwd=BASE_DIR, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push"],
            cwd=BASE_DIR, check=True, capture_output=True
        )
        logger.info("Push realizado com sucesso.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Erro no git: %s", e.stderr.decode(errors="replace"))
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    logger.info("=== Iniciando geração de dados ===")

    # 1. Autenticar
    logger.info("Autenticando no portal...")
    if not _login():
        logger.error("Falha na autenticação. Verifique APP_USER e APP_PASS no .env")
        return 1

    # 2. Executar query
    logger.info("Executando query...")
    rows = _executar_com_retry(SQL_CHAMADOS)
    if rows is None:
        logger.error("Query retornou None após retry. Abortando.")
        return 1

    if not rows:
        logger.warning("Query retornou 0 registros.")

    # 3. Parsear
    equipe = _parse_equipe(rows) if rows else []
    agora  = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    payload = {
        "ok":         True,
        "equipe":     equipe,
        "atualizado": agora,
    }

    # 4. Salvar dados.json
    DADOS_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info("dados.json salvo — %d pessoa(s), %s", len(equipe), agora)

    # 5. Git push
    _git_push(f"dados: atualização automática {agora}")

    logger.info("=== Concluído ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
