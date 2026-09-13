"""安全计算器。

用 AST 解析而不是 eval：表达式是模型生成的，不受信，eval 会直接执行任意代码。
这里只放行算术运算和白名单数学函数，其他节点一律拒绝。
"""

import ast
import math
import operator

CALCULATOR_DESC = (
    "计算数学表达式，返回精确结果。"
    "支持 + - * / // % ** 和括号，以及 sqrt/log/exp/sin/cos/tan/abs/round/min/max 等函数，"
    "常量 pi 和 e。凡是涉及数字计算都用它，不要自己心算。"
    "示例：'(17*23)+sqrt(2)'、'log(100, 10)'、'sin(pi/6)'。"
)

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "degrees": math.degrees,
    "radians": math.radians,
}

_CONSTS = {"pi": math.pi, "e": math.e}

# 挡住 9**9**9 这类会算到天荒地老的表达式
_MAX_POW = 1000


def _evaluate(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"不支持的字面量: {node.value!r}")

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        left, right = _evaluate(node.left), _evaluate(node.right)
        if op is operator.pow and isinstance(right, (int, float)) and abs(right) > _MAX_POW:
            raise ValueError(f"指数 {right} 过大")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_evaluate(node.operand))

    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"未知名称: {node.id}（只支持 pi 和 e）")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            allowed = ", ".join(sorted(_FUNCS))
            raise ValueError(f"不支持的函数，可用：{allowed}")
        if node.keywords:
            raise ValueError("不支持关键字参数")
        return _FUNCS[node.func.id](*[_evaluate(a) for a in node.args])

    raise ValueError(f"不支持的语法: {type(node).__name__}")


def calculate(expression: str) -> str:
    """计算数学表达式并返回结果。"""
    expression = (expression or "").strip()
    if not expression:
        raise ValueError("表达式为空")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法错误: {exc.msg}") from exc

    value = _evaluate(tree)

    # 让 391.0 显示成 391
    if isinstance(value, float) and value.is_integer():
        return f"{expression} = {int(value)}"
    return f"{expression} = {value}"
