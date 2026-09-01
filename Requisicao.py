"""
Módulo de comunicação com o portal corporativo Apdata.
Gerencia autenticação e execução de queries SQL via API HTTP.
"""

import logging
import sys
from os import environ
from pathlib import Path

from dotenv import load_dotenv
from requests import post, Response


def _get_base_path() -> Path:
    """Retorna o diretório base para localizar o .env (compatível com PyInstaller)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


load_dotenv(_get_base_path() / ".env")

logger = logging.getLogger(__name__)

# Cookies de sessão — preenchidos após requisicaoLogin()
cookiesLogin: dict = {}

# Payload base reutilizado em todas as requisições
fixPayload: dict = {
    "captcha": "",
    "NTLMLogin": False,
    "loginAuthenticOnserver": True,
    "tenantName": "Apdata",
    "baseURL": "",
    "tsc": "",
    "sessionID": 0,
    "selectedEmployee": 0,
    "selectedCandidate": 0,
    "selectedVacancy": 0,
    "dtFmt": "d/m/Y",
    "tmFmt": "H:i:s",
    "shTmFmt": "H:i",
    "dtTmFmt": "d/m/Y H:i:s",
    "language": 0,
    "idEmployeeLogged": 0,
}

# Resultado da última query executada
resultado = None

_BASE_URL = "https://portal.apdata.com.br/corporativo/.net/index.ashx/"


def request(metodo: str, payload: dict, cookie: dict) -> Response:
    """Executa uma requisição POST para o portal corporativo.

    Args:
        metodo:  Endpoint relativo (ex: 'login', 'genericTransactionExecute').
        payload: Parâmetros da requisição.
        cookie:  Cookies de sessão autenticada.

    Returns:
        Objeto Response da requisição.
    """
    return post(_BASE_URL + metodo, params=payload, cookies=cookie, timeout=60)


def requisicaoLogin() -> None:
    """Autentica no portal corporativo e armazena os cookies de sessão.

    As credenciais são lidas das variáveis de ambiente APP_USER e APP_PASS.
    Após o login, define o perfil de acesso automaticamente.
    """
    global cookiesLogin

    login_payload = fixPayload.copy()
    login_payload["UserName"] = environ.get("APP_USER", "")
    login_payload["password"] = environ.get("APP_PASS", "")

    if not login_payload["UserName"] or not login_payload["password"]:
        logger.error("Credenciais APP_USER/APP_PASS não configuradas no .env")
        return

    try:
        resp = post(_BASE_URL + "login", params=login_payload, timeout=60)
        resp.raise_for_status()
        cookiesLogin = resp.cookies.get_dict()
        fixPayload["tsc"] = cookiesLogin.get("ts", "")
        requisicaoSetPerfil()
        logger.info("Login realizado com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao realizar login: {e}", exc_info=True)


def requisicaoSetPerfil() -> int:
    """Define o perfil de acesso 34 na sessão atual.

    Returns:
        HTTP status code da requisição (200 = sucesso).
    """
    perfil_payload = fixPayload.copy()
    perfil_payload["perfil"] = 1029
    try:
        resp = request("SetPerfil", perfil_payload, cookiesLogin)
        return resp.status_code
    except Exception as e:
        logger.error(f"Erro ao definir perfil: {e}", exc_info=True)
        return 0


def executaComando(sql: str) -> list | None:
    """Executa uma query SQL via API do portal corporativo.

    O protocolo requer 4 requisições sequenciais:
      1. msg 1895866383 — inicializa o objeto grid SQL e retorna o handle (hwd)
      2. msg 1879056385 — configura e envia a query ao grid
      3. msg 1879056410 — define o limite máximo de linhas retornadas (90.000)
      4. msg 1879056391 — busca os registros e retorna o resultado (comBuf)

    Args:
        sql: Query SQL a ser executada no servidor.

    Returns:
        Lista de registros (comBuf) ou None se não houver resultados.
    """
    global resultado

    try:
        # Passo 1: inicializa o grid e obtém o handle do objeto
        payload = fixPayload.copy()
        payload.update({
            "sMessage": 1895866383,
            "serverClass": "ISQLGrid",
            "killAfterUse": False,
            "Field_0": sql,       "Field_0_TP": "string",
            "Field_1": False,     "Field_1_TP": "boolean",
            "Field_2": False,     "Field_2_TP": "boolean",
            "Field_3": False,     "Field_3_TP": "boolean",
            "Field_4": False,     "Field_4_TP": "boolean",
        })
        resp = request("genericTransactionExecute", payload, cookiesLogin)
        resp.raise_for_status()
        hwd = resp.json()["hwd"]

        # Passo 2: configura a query no grid
        payload = fixPayload.copy()
        payload.update({
            "sMessage": 1879056385,
            "hwd": hwd,
            "Field_0": 5,         "Field_0_TP": "int",
            "Field_1": "",        "Field_1_TP": "string",
            "Field_2": sql,       "Field_2_TP": "string",
            "Field_2_AsRec": True,
            "Field_3": False,     "Field_3_TP": "boolean",
        })
        request("genericTransactionExecute", payload, cookiesLogin)

        # Passo 3: define limite de linhas
        payload = fixPayload.copy()
        payload.update({
            "sMessage": 1879056410,
            "hwd": hwd,
            "Field_0": 90000,     "Field_0_TP": "int",
        })
        request("genericTransactionExecute", payload, cookiesLogin)

        # Passo 4: busca os registros
        payload = fixPayload.copy()
        payload.update({
            "sMessage": 1879056391,
            "rMessage": 1879056402,
            "hwd": hwd,
            "Field_0": -2,        "Field_0_TP": "int",
        })
        resp = request("genericTransactionExecute", payload, cookiesLogin)
        resp.raise_for_status()

        dados = resp.json()
        resultado = dados.get("comBuf", None)

    except Exception as e:
        logger.error(f"Erro ao executar comando SQL: {e}", exc_info=True)
        resultado = None

    return resultado


def executaComandoDML(sql: str) -> bool:
    """Executa um comando DML (INSERT/UPDATE/DELETE) via API do portal.

    Diferente de executaComando(), não há retorno de registros: apenas
    inicializa o grid SQL e dispara a execução do comando (msg 1895866383).

    Args:
        sql: Comando T-SQL a ser executado no servidor.

    Returns:
        True se o comando foi enviado com sucesso, False em caso de erro.
    """
    try:
        payload = fixPayload.copy()
        payload.update({
            "sMessage": 1895866383,
            "serverClass": "ISQLGrid",
            "killAfterUse": False,
            "Field_0": sql,       "Field_0_TP": "string",
            "Field_1": False,     "Field_1_TP": "boolean",
            "Field_2": False,     "Field_2_TP": "boolean",
            "Field_3": False,     "Field_3_TP": "boolean",
            "Field_4": False,     "Field_4_TP": "boolean",
        })
        resp = request("genericTransactionExecute", payload, cookiesLogin)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Erro ao executar comando DML: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    # Bloco de teste — executado apenas diretamente, nunca ao importar
    requisicaoLogin()
    executaComando("Select HFE_CdiContratoItemxPessoa from ContratosItensxPessoas where HFE_CdiContratoItemxPessoa = 29004")
    print(resultado)


