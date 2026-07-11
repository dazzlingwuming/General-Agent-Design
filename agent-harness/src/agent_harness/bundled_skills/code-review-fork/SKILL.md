---
name: code-review-fork
description: 在独立 reviewer 子 Agent 中审查代码正确性、回归风险和缺失测试。用户要求进行隔离代码审查时使用。
allowed-tools: list_files read_file search_text submit_result
context: fork
agent: reviewer
---
# 独立代码审查

1. 阅读任务涉及的代码和测试。
2. 优先识别正确性、安全性和行为回归。
3. 使用路径和行号记录证据。
4. 通过 `submit_result` 返回结构化结论。
