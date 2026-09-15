"""
Servidor local para o botão "Atualizar agora" do dashboard.

Roda na porta 5001 e expõe dois endpoints:
  GET  /atualizar  — executa gerar_dados.py e retorna o resultado
  GET  /status     — retorna se o servidor está no ar

Inicie com: pythonw servidor_local.py
(pythonw = sem janela no Windows)
"""

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

BASE_DIR   = Path(__file__).parent
PORTA      = 5001
PYTHONW    = sys.executable          # mesmo interpretador que está rodando este script
GERAR_PY   = str(BASE_DIR / "gerar_dados.py")

# Flag para evitar execuções simultâneas
_em_execucao = False


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        # CORS — permite chamada do GitHub Pages
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

        if self.path.startswith("/atualizar"):
            self._atualizar()
        elif self.path.startswith("/status"):
            self._status()
        else:
            self.wfile.write(json.dumps({"ok": False, "erro": "Rota não encontrada."}).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def _atualizar(self):
        global _em_execucao
        if _em_execucao:
            self.wfile.write(json.dumps({
                "ok": False, "erro": "Atualização já em andamento. Aguarde."
            }).encode())
            return

        _em_execucao = True
        try:
            NO_WINDOW = 0x08000000
            result = subprocess.run(
                [PYTHONW, GERAR_PY],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=90,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )
            ok  = result.returncode == 0
            log = (result.stdout + result.stderr).strip()
            self.wfile.write(json.dumps({"ok": ok, "log": log}).encode())
        except subprocess.TimeoutExpired:
            self.wfile.write(json.dumps({"ok": False, "erro": "Timeout ao gerar dados."}).encode())
        except Exception as e:
            self.wfile.write(json.dumps({"ok": False, "erro": str(e)}).encode())
        finally:
            _em_execucao = False

    def _status(self):
        self.wfile.write(json.dumps({"ok": True, "porta": PORTA}).encode())

    def log_message(self, *args):
        pass  # silencia o log HTTP no terminal


if __name__ == "__main__":
    servidor = HTTPServer(("127.0.0.1", PORTA), Handler)
    print(f"Servidor local rodando em http://127.0.0.1:{PORTA}")
    servidor.serve_forever()
