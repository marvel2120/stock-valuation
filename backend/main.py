"""
A股股票估值工具 - HTTP 服务器入口

使用 Python 内置 http.server 模块，零额外依赖启动 API 服务
"""

import json
import os
import sys
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SERVER_CONFIG
from routes.valuation import handle_api


class ValuationAPIHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

    def do_GET(self):
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parsed.query

        # API 请求
        if path.startswith("/api/"):
            try:
                status, data = handle_api(path, query)
                self._send_json(status, data)
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {"error": str(e)})
            return

        # 静态文件请求
        self._serve_static(path)

    def do_POST(self):
        """处理 POST 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        if not path.startswith("/api/"):
            self._send_json(404, {"error": "Not Found"})
            return

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            raw = self.rfile.read(content_length)
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                pass

        try:
            status, data = handle_api(path, "", body)
            self._send_json(status, data)
        except Exception as e:
            traceback.print_exc()
            self._send_json(500, {"error": str(e)})

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def _serve_static(self, path: str):
        """服务前端静态文件"""
        # 默认首页
        if path == "/" or path == "":
            path = "/index.html"

        file_path = os.path.join(self.FRONTEND_DIR, path.lstrip("/"))

        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            # SPA fallback: 所有非API路径返回index.html
            file_path = os.path.join(self.FRONTEND_DIR, "index.html")
            if not os.path.exists(file_path):
                self._send_json(404, {"error": "Not Found"})
                return

        content_type = self._get_content_type(file_path)

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_json(500, {"error": f"读取文件失败: {str(e)}"})

    def _send_json(self, status: int, data: dict):
        """发送 JSON 响应"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def _set_cors_headers(self):
        """设置 CORS 头"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _get_content_type(self, path: str) -> str:
        """根据文件扩展名返回 Content-Type"""
        ext = os.path.splitext(path)[1].lower()
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        return types.get(ext, "application/octet-stream")

    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} - {format % args}")


def main():
    host = SERVER_CONFIG["host"]
    port = SERVER_CONFIG["port"]

    server = HTTPServer((host, port), ValuationAPIHandler)
    print(f"🚀 A股估值工具已启动: http://localhost:{port}")
    print(f"   API 端点: http://localhost:{port}/api/health")
    print(f"   Ctrl+C 停止服务")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹  服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
