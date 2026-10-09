"""[aipdd fork delta] 流水线编排：服务端权威计划器 + 可复用传输层。

统一 MCP 步进器、前端 episode-auto-pipeline 与新 REST 编排的自动化逻辑到
一份「计划器」（planner）：只读进度、只产出「下一步该做的动作」，不执行；
执行由薄消费方完成——MCP 经真实 HTTP（``_request``）、REST 路由经进程内
ASGI 自调用（``asgi_caller``）、前端经 api client + awaitTaskCompletion。
"""
