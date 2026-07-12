from __future__ import annotations

import sys
import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("Harness Test Server", instructions="测试服务说明：只用于协议集成验收。", port=int(os.getenv("MCP_TEST_PORT", "8000")))


@server.tool()
def echo_chinese(text: str) -> dict[str, str]:
    """返回输入的中文文本。"""
    return {"echo": text}


@server.resource("memo://acceptance")
def acceptance_resource() -> str:
    """返回阶段五资源验收内容。"""
    return "真实 MCP 资源读取成功"


@server.prompt()
def chinese_review(topic: str) -> str:
    """生成中文审查提示词。"""
    return f"请用中文审查：{topic}"


def main() -> None:
    """Run the fixture with the transport selected by the first argument."""
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    server.run(transport=transport)


if __name__ == "__main__":
    main()
