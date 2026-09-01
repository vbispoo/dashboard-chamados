"""
Servidor Flask — API para o Dashboard de Chamados da Equipe.
Retorna os chamados por pessoa e por mês para a equipe 57.
A query é executada em background a cada 2 minutos e o resultado
fica em cache na memória — as requisições ao /api/chamados respondem
instantaneamente com os dados mais recentes.
"""

import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

import Requisicao

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
DIAS_UTEIS_REF  = 22
INTERVALO_SEG   = 120  # atualiza a cada 2 minutos

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
# Cache em memória
# ---------------------------------------------------------------------------

_cache_lock       = threading.Lock()
_cache_equipe     = []       # resultado parseado mais recente
_cache_atualizado = None     # datetime da última atualização bem-sucedida
_cache_erro       = None     # mensagem do último erro, se houver


# ---------------------------------------------------------------------------
# Helpers de autenticação e query
# ---------------------------------------------------------------------------

def _login() -> bool:
    """Faz login no portal e retorna True se bem-sucedido."""
    Requisicao.cookiesLogin.clear()
    Requisicao.requisicaoLogin()
    return bool(Requisicao.cookiesLogin)


def _executar_com_retry(sql: str) -> list | None:
    """Executa a query com reautenticação automática em caso de sessão expirada."""
    if not Requisicao.cookiesLogin:
        if not _login():
            return None

    rows = Requisicao.executaComando(sql)

    if rows is None:
        logger.warning("Query retornou None — sessão expirada. Reautenticando...")
        if not _login():
            logger.error("Reautenticação falhou.")
            return None
        rows = Requisicao.executaComando(sql)

    return rows


def _get_field(row: dict, index: int):
    """Lê Field_N ou Campo_N — o portal alterna entre os dois prefixos."""
    return row.get(f"Field_{index}", row.get(f"Campo_{index}"))


def _parse_equipe(rows: list) -> list:
    """Agrupa o comBuf por pessoa, retornando lista de colaboradores com seus meses."""
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


# ---------------------------------------------------------------------------
# Atualização em background
# ---------------------------------------------------------------------------

def _atualizar_cache() -> None:
    """Executa a query e atualiza o cache. Chamado pelo scheduler."""
    global _cache_equipe, _cache_atualizado, _cache_erro

    logger.info("Atualizando cache...")
    try:
        rows = _executar_com_retry(SQL_CHAMADOS)
        if rows is None:
            raise RuntimeError("Sessão expirada e reautenticação falhou.")

        equipe = _parse_equipe(rows) if rows else []

        with _cache_lock:
            _cache_equipe     = equipe
            _cache_atualizado = datetime.now()
            _cache_erro       = None

        logger.info(
            "Cache atualizado — %d pessoa(s), próxima atualização em %ds.",
            len(equipe), INTERVALO_SEG
        )
    except Exception as exc:
        with _cache_lock:
            _cache_erro = str(exc)
        logger.error("Erro ao atualizar cache: %s", exc, exc_info=True)


def _scheduler() -> None:
    """Loop infinito que dispara _atualizar_cache a cada INTERVALO_SEG segundos."""
    while True:
        _atualizar_cache()
        time.sleep(INTERVALO_SEG)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve o dashboard HTML."""
    return send_from_directory(BASE_DIR, "dashboard-chamados.html")


@app.route("/api/chamados")
def api_chamados():
    """Retorna os dados do cache (atualizado automaticamente a cada 2 min)."""
    with _cache_lock:
        equipe     = list(_cache_equipe)
        atualizado = _cache_atualizado
        erro       = _cache_erro

    # Cache ainda não preenchido (servidor acabou de iniciar)
    if atualizado is None and not equipe:
        if erro:
            return jsonify({"ok": False, "erro": erro}), 503
        return jsonify({"ok": False, "erro": "Dados ainda sendo carregados. Tente em alguns segundos."}), 503

    return jsonify({
        "ok":         True,
        "equipe":     equipe,
        "atualizado": atualizado.strftime("%d/%m/%Y %H:%M:%S") if atualizado else None,
        "erro":       erro,  # pode ter erro parcial mas ainda retornar dados antigos
    })


@app.route("/api/status")
def api_status():
    """Saúde do servidor e estado do cache."""
    with _cache_lock:
        atualizado = _cache_atualizado
        erro       = _cache_erro
        qtd        = len(_cache_equipe)

    return jsonify({
        "ok":              True,
        "autenticado":     bool(Requisicao.cookiesLogin),
        "cache_pessoas":   qtd,
        "cache_atualizado": atualizado.strftime("%d/%m/%Y %H:%M:%S") if atualizado else None,
        "cache_erro":      erro,
        "intervalo_seg":   INTERVALO_SEG,
    })


@app.route("/api/raw")
def api_raw():
    """Retorna o comBuf bruto para diagnóstico (executa query na hora)."""
    try:
        rows = _executar_com_retry(SQL_CHAMADOS)
    except Exception as exc:
        return jsonify({"ok": False, "erro": str(exc)}), 500
    return jsonify({"ok": True, "rows": rows, "total": len(rows) if rows else 0})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Autenticando no portal corporativo...")
    _login()

    # Inicia o scheduler em thread daemon (encerra automaticamente com o processo)
    t = threading.Thread(target=_scheduler, daemon=True, name="cache-scheduler")
    t.start()
    logger.info("Scheduler iniciado — atualizando a cada %ds.", INTERVALO_SEG)

    logger.info("Iniciando servidor Flask em http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
