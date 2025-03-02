import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def get_telegram_code_from_request() -> str:
    logger.info("Telegram запрашивает авторизацию и должен отравить код на авторизованные сессии")

    port = 8000
    timeout = 300
    server_address = ("", port)

    telegram_auth_code: Optional[str] = None

    class LoginHelper(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal telegram_auth_code
            query = urlparse(self.path).query
            query_components = parse_qs(query)
            auth_code = query_components.get("telegram_auth_code", [""])[0]
            logger.info(f"Получен запрос: {query_components}")

            telegram_auth_code = auth_code

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(auth_code.encode("utf-8"))
            return

    httpd = HTTPServer(server_address, LoginHelper)
    httpd.timeout = timeout
    logger.info(
        f"Запущен сервер на порте {port} в контейнере, порт на хосте может отличаться. Ожидание кода авторизации в течении {timeout} сек. Отправьте код по http://localhost:7777/?telegram_auth_code=xxxxxx"
    )
    httpd.finish_request(*httpd.get_request())
    httpd.server_close()
    if not telegram_auth_code:
        raise ValueError("Ну удалось получить код авторизации")
    logger.info(f"Получен код авторизации: {telegram_auth_code}")
    return telegram_auth_code
