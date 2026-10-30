"""Generate docs/infrastructure.svg and docs/agent.svg from the Terraform and agent source.

Stdlib only. Run: python scripts/gen_diagrams.py [--out docs]
"""

import argparse
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = re.compile(r'^(resource|module)\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{')
REF = re.compile(r"\b(module\.\w+|aws_\w+\.\w+)")

STYLE = """<defs>
<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#445"/></marker>
<style>
.box{fill:#fff;stroke:#232f3e;stroke-width:1.5}.lam{fill:#fff4e5;stroke:#e07b00;stroke-width:1.5}
.dat{fill:#e8f5e9;stroke:#2e7d32;stroke-width:1.5}.ai{fill:#f3e8ff;stroke:#7b1fa2;stroke-width:1.5}
.ops{fill:#e8f0fe;stroke:#1a56db;stroke-width:1.5}.t{fill:#232f3e}.s{fill:#556;font-size:11px}
.h{font-weight:700;font-size:20px;fill:#232f3e}.c{font-weight:700;fill:#556;font-size:12px}
.l{stroke:#445;stroke-width:1.3;fill:none;marker-end:url(#a)}
</style></defs>"""


def parse_blocks(directory: Path) -> list[dict]:
    blocks = []
    for path in sorted(directory.glob("*.tf")):
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            match = HEADER.match(line)
            if match and current is None:
                kind, first, second = match.groups()
                current = {"kind": kind, "type": first if kind == "resource" else "module",
                           "name": second if kind == "resource" else first, "body": [], "file": path.name}
                continue
            if current is not None:
                if line == "}":
                    current["body"] = "\n".join(current["body"])
                    blocks.append(current)
                    current = None
                else:
                    current["body"].append(line)
    return blocks


def node_id(block: dict) -> str:
    return f"module.{block['name']}" if block["kind"] == "module" else f"{block['type']}.{block['name']}"


def canonical(ref: str) -> str:
    """Fold sub-resources into the resource they configure."""
    kind, name = ref.split(".", 1)
    if kind.startswith("aws_s3_bucket_"):
        return f"aws_s3_bucket.{name}"
    if kind == "aws_bedrock_guardrail_version":
        return f"aws_bedrock_guardrail.{name}"
    if kind == "aws_apigatewayv2_stage":
        return "aws_apigatewayv2_api.this"
    return ref


def classify(block: dict):
    """Return (column, css class, title, subtitle) or None to hide the block."""
    t, name, body = block["type"], block["name"], block["body"]
    if t == "module":
        route = re.search(r'route_key\s*=\s*"([^"]+)"', body)
        source = re.search(r'source\s*=\s*"\./modules/lambda_endpoint"', body)
        if source and route:
            return 2, "lam", name.replace("_", "-"), route.group(1)
        return None
    if t == "aws_apigatewayv2_api":
        return 1, "box", "API Gateway", "HTTP API"
    if t == "aws_dynamodb_table":
        return 3, "dat", f"DynamoDB: {name}", "table"
    if t == "aws_s3_bucket":
        return 3, "dat", f"S3: {name}", "bucket"
    if t == "aws_ssm_parameter":
        return 3, "dat", f"SSM: {name}", "parameter"
    if t == "aws_ecr_repository":
        return 3, "dat", "ECR: agent image", "repository"
    if t == "aws_bedrockagentcore_agent_runtime":
        return 4, "ai", "AgentCore Runtime", "LangGraph agent container"
    if t == "aws_bedrockagentcore_gateway":
        return 4, "ai", "AgentCore Gateway", "MCP tools"
    if t == "aws_bedrockagentcore_gateway_target":
        return 4, "ai", f"Gateway target: {name}", "Lambda tool"
    if t == "aws_bedrockagentcore_memory":
        return 4, "ai", "AgentCore Memory", "conversation state"
    if t == "aws_bedrock_guardrail":
        return 4, "ai", "Bedrock Guardrail", "content safety"
    if t == "aws_codebuild_project":
        return 5, "ops", "CodeBuild", "agent image build"
    if t == "aws_cloudwatch_dashboard":
        return 5, "ops", "CloudWatch dashboard", name
    return None


def curve(x1, y1, x2, y2) -> str:
    dx = max(40, abs(x2 - x1) / 2)
    sign = 1 if x2 >= x1 else -1
    return f'<path class="l" d="M{x1} {y1} C{x1 + sign * dx} {y1} {x2 - sign * dx} {y2} {x2} {y2}"/>'


def box(x, y, w, h, css, title, sub="") -> str:
    out = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" class="{css}"/>'
    out += f'<text x="{x + w / 2}" y="{y + (h / 2 - 3 if sub else h / 2 + 4)}" text-anchor="middle" class="t">{escape(title)}</text>'
    if sub:
        out += f'<text x="{x + w / 2}" y="{y + h / 2 + 13}" text-anchor="middle" class="s">{escape(sub)}</text>'
    return out


def infrastructure_svg(blocks: list[dict]) -> str:
    nodes: dict[str, tuple] = {}
    for block in blocks:
        info = classify(block)
        if info:
            nodes[node_id(block)] = info
    alarms = sum(1 for b in blocks if b["type"] == "aws_cloudwatch_metric_alarm")
    if alarms:
        nodes["alarms"] = (5, "ops", f"{alarms} CloudWatch alarms", "API 5xx, Lambda errors")

    width, height_node, gap, top = 190, 46, 24, 90
    columns: dict[int, list[str]] = {}
    for key, info in nodes.items():
        columns.setdefault(info[0], []).append(key)
    pos = {}
    for col, keys in columns.items():
        for row, key in enumerate(keys):
            pos[key] = (20 + col * 230, top + row * (height_node + gap))
    pos["client"] = (20, top)
    height = top + max(len(k) for k in columns.values()) * (height_node + gap) + 20

    edges = set()
    for block in blocks:
        source = node_id(block)
        if source not in nodes:
            continue
        for ref in REF.findall(block["body"]):
            target = canonical(ref)
            if target in nodes and target != source:
                edges.add((target, source) if target.startswith("aws_apigatewayv2_api") else (source, target))
    api = next((k for k in nodes if k.startswith("aws_apigatewayv2_api")), None)
    if api:
        edges.add(("client", api))
    if "aws_cloudwatch_dashboard.agent" in nodes and "alarms" in nodes:
        pass

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1420 {height}" font-family="Segoe UI, Arial, sans-serif" font-size="13">', STYLE,
           f'<rect width="1420" height="{height}" fill="#fafbfc"/>',
           '<text x="20" y="32" class="h">project1 - Serverless AWS infrastructure (generated from infra/*.tf)</text>',
           '<text x="20" y="52" class="s">One Lambda per endpoint behind API Gateway; Bedrock AgentCore for the agent</text>']
    for col, label in {0: "Client", 1: "Edge", 2: "Lambda endpoints", 3: "Data & storage", 4: "AI / AgentCore", 5: "Build & ops"}.items():
        svg.append(f'<text x="{20 + col * 230}" y="78" class="c">{escape(label)}</text>')
    for source, target in sorted(edges):
        sx, sy = pos[source]
        tx, ty = pos[target]
        if tx >= sx:
            svg.append(curve(sx + width, sy + height_node / 2, tx, ty + height_node / 2))
        else:
            svg.append(curve(sx, sy + height_node / 2, tx + width, ty + height_node / 2))
    svg.append(box(*pos["client"], width, height_node, "box", "Browser", "/app SPA"))
    for key, (col, css, title, sub) in nodes.items():
        svg.append(box(*pos[key], width, height_node, css, title, sub))
    svg.append("</svg>")
    return "\n".join(svg)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def agent_svg(blocks: list[dict]) -> str:
    tools_source = read(ROOT / "agent" / "tools.py")
    local_tools = re.findall(r"@tool\s+(?:async\s+)?def (\w+)\(", tools_source)
    tool_backend = {"run_python": "Code Interpreter", "browse_page": "Browser"}
    runtime = next((b for b in blocks if b["type"] == "aws_bedrockagentcore_agent_runtime"), None)
    env = runtime["body"] if runtime else ""
    all_tf = "\n".join(b["body"] for b in blocks)
    models = [("Chat model", "MODEL_ID", "agent_model_id"), ("Vision model", "VISION_MODEL_ID", "agent_vision_model_id")]
    model_nodes = []
    for label, var, local in models:
        if var in env:
            found = re.search(rf'{local}\s*=\s*"([^"]+)"', read(ROOT / "infra" / "agent_runtime.tf"))
            model_nodes.append((label, found.group(1) if found else var))
    gateway_tools = re.findall(r'name\s*=\s*"(\w+)"\s*\n\s*description[^\n]*\n\s*\n?\s*input_schema', all_tf)

    side = []
    if "GUARDRAIL_ID" in env:
        side.append(("ai", "Bedrock Guardrail", "input/output filtering"))
    if "MEMORY_ID" in env:
        side.append(("ai", "AgentCore Memory", "checkpointer"))
    if "AGENT_OBSERVABILITY_ENABLED" in env:
        side.append(("ops", "CloudWatch GenAI Obs.", "OTEL traces (no content)"))

    tool_nodes = [(name, tool_backend.get(name, "local tool")) for name in local_tools]
    tool_nodes += [(name, "via Gateway") for name in gateway_tools]
    backends = []
    for _, backend in tool_nodes:
        if backend not in [b for b in backends]:
            backends.append(backend)
    backend_label = {"via Gateway": ("ai", "AgentCore Gateway (MCP)", "MCP server -> Lambda"),
                     "Code Interpreter": ("ai", "Code Interpreter", "isolated sandbox"),
                     "Browser": ("ai", "AgentCore Browser", "sandboxed browsing")}

    rows = max(len(tool_nodes), len(model_nodes) + len(side), 4)
    height = 120 + rows * 74
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 {height}" font-family="Segoe UI, Arial, sans-serif" font-size="13">', STYLE,
           f'<rect width="1200" height="{height}" fill="#fafbfc"/>',
           '<text x="20" y="32" class="h">project1 - Agent architecture (generated from agent/ and infra/)</text>',
           '<text x="20" y="52" class="s">LangGraph tool-calling loop on Bedrock AgentCore Runtime</text>']
    mid = height / 2 + 20
    svg.append(box(20, mid - 23, 150, 46, "lam", "agent-invoke", "Lambda"))
    svg.append(box(230, mid - 40, 210, 80, "ai", "AgentCore Runtime", "LangGraph create_agent loop"))
    svg.append(curve(170, mid, 230, mid))
    if "agent_mcp" in {b["name"] for b in blocks}:
        svg.append(box(20, mid + 60, 150, 46, "lam", "agent-mcp", "Lambda: /agent/mcp"))
        svg.append(box(20, mid + 125, 150, 46, "box", "External MCP client", "inbound (we are server)"))
        svg.append(curve(95, mid + 125, 95, mid + 106))
        svg.append(curve(170, mid + 83, 230, mid + 20))
    column_x = {"models": 520, "tools": 760, "backends": 990}
    svg.append(f'<text x="{column_x["models"]}" y="84" class="c">Models &amp; safety</text>')
    svg.append(f'<text x="{column_x["tools"]}" y="84" class="c">Tools</text>')
    svg.append(f'<text x="{column_x["backends"]}" y="84" class="c">Tool backends</text>')

    left = [("ai", label, model) for label, model in model_nodes] + side
    for i, (css, title, sub) in enumerate(left):
        y = 100 + i * 74
        svg.append(box(column_x["models"], y, 200, 46, css, title, sub))
        svg.append(curve(440, mid, column_x["models"], y + 23))

    backend_pos = {}
    for i, backend in enumerate(backends):
        css, title, sub = backend_label.get(backend, ("box", backend, ""))
        y = 100 + i * 74
        backend_pos[backend] = y
        svg.append(box(column_x["backends"], y, 190, 46, css, title, sub))
    for i, (name, backend) in enumerate(tool_nodes):
        y = 100 + i * 74
        svg.append(box(column_x["tools"], y, 200, 46, "lam", name, "tool"))
        svg.append(curve(440, mid, column_x["tools"], y + 23))
        svg.append(curve(column_x["tools"] + 200, y + 23, column_x["backends"], backend_pos[backend] + 23))
    svg.append("</svg>")
    return "\n".join(svg)


def sequence_svg(blocks: list[dict]) -> str:
    """User input -> model invocation. Participants and tool calls come from Terraform and agent/tools.py."""
    types = {b["type"] for b in blocks}
    modules = {b["name"] for b in blocks if b["kind"] == "module"}
    local_tools = re.findall(r"@tool\s+(?:async\s+)?def (\w+)\(", read(ROOT / "agent" / "tools.py"))
    gateway_tools = re.findall(r'name\s*=\s*"(\w+)"\s*\n\s*description[^\n]*\n\s*\n?\s*input_schema', "\n".join(b["body"] for b in blocks))
    tools = local_tools + gateway_tools
    route = next((re.search(r'route_key\s*=\s*"([^"]+)"', b["body"]).group(1) for b in blocks
                  if b["name"] == "agent_invoke" and re.search(r'route_key\s*=\s*"([^"]+)"', b["body"])), "POST /agent/invoke")
    env = next((b["body"] for b in blocks if b["type"] == "aws_bedrockagentcore_agent_runtime"), "")

    actors = [("user", "Browser"), ("api", "API Gateway"), ("lambda", "agent-invoke\nLambda"), ("ddb", "DynamoDB\n(auth)")]
    if "aws_s3_bucket" in types:
        actors.append(("s3", "S3 uploads"))
    actors.append(("runtime", "AgentCore\nRuntime"))
    if "aws_bedrockagentcore_memory" in types:
        actors.append(("memory", "AgentCore\nMemory"))
    actors.append(("model", "Bedrock model" + ("\n+ Guardrail" if "GUARDRAIL_ID" in env else "")))
    if local_tools:
        actors.append(("tools", "Code Interpreter\n/ Browser"))
    if gateway_tools:
        actors.append(("gateway", "AgentCore\nGateway (MCP)"))
        actors.append(("hello", "Tool Lambda\n(hello)"))
    last_actor = actors[-1][0]

    step = max(1400 // len(actors), 120)
    x = {key: 190 + i * step for i, (key, _) in enumerate(actors)}
    width = 190 + len(actors) * step
    messages = [
        ("user", "api", f"{route} (prompt, file?)", False),
        ("api", "lambda", "invoke (throttled)", False),
        ("lambda", "ddb", "validate auth_session cookie", False),
        ("ddb", "lambda", "user (phone hash)", True),
    ]
    if "s3" in x:
        messages += [("lambda", "s3", "if file: check owner prefix, type, size; read", False), ("s3", "lambda", "file bytes (base64)", True)]
    messages.append(("lambda", "runtime", "InvokeAgentRuntime (session = hash(user, cookie))", False))
    if "memory" in x:
        messages += [("runtime", "memory", "load conversation checkpoint", False), ("memory", "runtime", "prior messages", True)]
    messages.append(("runtime", "model", "Converse: history + prompt (+ image/document block)", False))
    messages.append(("model", "runtime", "answer, or tool call", True))
    loop_from = len(messages)
    if tools:
        if local_tools:
            messages += [("runtime", "tools", "direct SDK call: " + ", ".join(local_tools), False),
                         ("tools", "runtime", "tool result (untrusted data)", True)]
        if gateway_tools:
            messages += [("runtime", "gateway", "MCP tools/call: " + ", ".join(gateway_tools) + " (SigV4)", False),
                         ("gateway", "hello", "lambda:Invoke", False),
                         ("hello", "gateway", "result", True),
                         ("gateway", "runtime", "MCP tool result (untrusted data)", True)]
        messages += [("runtime", "model", "Converse: tool result", False), ("model", "runtime", "final answer", True)]
    loop_to = len(messages)
    if "memory" in x:
        messages.append(("runtime", "memory", "save checkpoint", False))
    messages += [("runtime", "lambda", "response or blocked flag", True),
                 ("lambda", "api", "200 | 422 blocked | 413 too large | 502/504", True),
                 ("api", "user", "JSON response shown in chat", True)]

    top, row = 135, 46
    height = top + len(messages) * row + 50
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="Segoe UI, Arial, sans-serif" font-size="12">', STYLE,
           f'<rect width="{width}" height="{height}" fill="#fafbfc"/>',
           '<text x="20" y="30" class="h">project1 - Sequence: user input to model invocation (generated)</text>',
           '<text x="20" y="50" class="s">Solid = request, dashed = response. Prompts and session IDs are never logged.</text>']
    for key, label in actors:
        lines = label.split("\n")
        svg.append(f'<rect x="{x[key] - 55}" y="62" width="110" height="{20 + 15 * len(lines)}" rx="6" class="{"ai" if key in ("runtime", "memory", "model") else "lam" if key == "lambda" else "box"}"/>')
        for i, line in enumerate(lines):
            svg.append(f'<text x="{x[key]}" y="{80 + 15 * i}" text-anchor="middle" class="t">{escape(line)}</text>')
        svg.append(f'<path d="M{x[key]} {64 + 20 + 15 * len(lines)} V{height - 20}" stroke="#aab" stroke-dasharray="4 4" fill="none"/>')
    if tools:
        y1, y2 = top + loop_from * row - 42, top + loop_to * row - 24
        svg.append(f'<rect x="{x["runtime"] - 20}" y="{y1}" width="{x[last_actor] - x["runtime"] + 40}" height="{y2 - y1 + 4}" fill="none" stroke="#7b1fa2" stroke-dasharray="6 4"/>')
        svg.append(f'<text x="{x["runtime"] - 14}" y="{y1 + 12}" class="c">loop while the model asks for tools</text>')
    for i, (src, dst, label, dashed) in enumerate(messages):
        y = top + i * row
        style = ' stroke-dasharray="6 4"' if dashed else ""
        svg.append(f'<path d="M{x[src]} {y} H{x[dst]}" class="l"{style}/>')
        svg.append(f'<text x="{(x[src] + x[dst]) / 2}" y="{y - 6}" text-anchor="middle" class="t">{i + 1}. {escape(label)}</text>')
    svg.append("</svg>")
    return "\n".join(svg)


def mcp_sequence_svg(blocks: list[dict]) -> str:
    """Inbound MCP: an external MCP client calls the agent through the agent-mcp Lambda."""
    handler = read(ROOT / "src" / "agent_mcp" / "handler.py")
    names = re.findall(r'^(?:TOOL_NAME|IMAGE_TOOL_NAME|UPLOAD_URL_TOOL_NAME|FILE_TOOL_NAME)\s*=\s*"([^"]+)"', handler, re.M)
    route = next((re.search(r'route_key\s*=\s*"([^"]+)"', b["body"]).group(1) for b in blocks
                  if b["name"] == "agent_mcp" and re.search(r'route_key\s*=\s*"([^"]+)"', b["body"])), "ANY /agent/mcp")
    actors = [("c", "External\nMCP client"), ("api", "API Gateway"), ("l", "agent-mcp\nLambda"), ("d", "DynamoDB\n(MCP sessions)"),
              ("r", "AgentCore\nRuntime"), ("m", "Bedrock model\n(+ tools)")]
    x = {k: 170 + i * 190 for i, (k, _) in enumerate(actors)}
    width = 170 + len(actors) * 190
    messages = [("c", "api", f"{route}: initialize", False), ("api", "l", "invoke", False), ("l", "d", "create MCP session", False),
                ("l", "c", "Mcp-Session-Id + server info", True), ("c", "l", "tools/list", False),
                ("l", "c", "tools: " + (", ".join(names) or "agent tools"), True), ("c", "l", "tools/call (prompt, file?)", False),
                ("l", "d", "validate session", False), ("l", "r", "InvokeAgentRuntime (same path as chat)", False),
                ("r", "m", "Converse + tool loop (see sequence.svg)", False), ("m", "r", "answer", True),
                ("r", "l", "response", True), ("l", "c", "MCP result (text)", True)]
    top, row = 135, 46
    height = top + len(messages) * row + 40
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="Segoe UI, Arial, sans-serif" font-size="12">', STYLE,
           f'<rect width="{width}" height="{height}" fill="#fafbfc"/>',
           '<text x="20" y="30" class="h">project1 - Sequence: inbound MCP (external client to agent, generated)</text>',
           '<text x="20" y="50" class="s">The agent also serves MCP: clients connect to /agent/mcp. Outbound MCP (agent to Gateway tools) is in sequence.svg.</text>']
    for key, label in actors:
        lines = label.split("\n")
        svg.append(f'<rect x="{x[key] - 55}" y="62" width="110" height="{20 + 15 * len(lines)}" rx="6" class="{"ai" if key in ("r", "m") else "lam" if key == "l" else "box"}"/>')
        for i, line in enumerate(lines):
            svg.append(f'<text x="{x[key]}" y="{80 + 15 * i}" text-anchor="middle" class="t">{escape(line)}</text>')
        svg.append(f'<path d="M{x[key]} {84 + 15 * len(lines)} V{height - 20}" stroke="#aab" stroke-dasharray="4 4" fill="none"/>')
    for i, (src, dst, label, dashed) in enumerate(messages):
        y = top + i * row
        style = ' stroke-dasharray="6 4"' if dashed else ""
        svg.append(f'<path d="M{x[src]} {y} H{x[dst]}" class="l"{style}/>')
        svg.append(f'<text x="{(x[src] + x[dst]) / 2}" y="{y - 6}" text-anchor="middle" class="t">{i + 1}. {escape(label)}</text>')
    svg.append("</svg>")
    return "\n".join(svg)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs"))
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    blocks = parse_blocks(ROOT / "infra")
    (out / "infrastructure.svg").write_text(infrastructure_svg(blocks), encoding="utf-8")
    (out / "agent.svg").write_text(agent_svg(blocks), encoding="utf-8")
    (out / "sequence.svg").write_text(sequence_svg(blocks), encoding="utf-8")
    (out / "sequence-mcp.svg").write_text(mcp_sequence_svg(blocks), encoding="utf-8")
    print(f"Wrote infrastructure.svg, agent.svg, sequence.svg and sequence-mcp.svg to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
