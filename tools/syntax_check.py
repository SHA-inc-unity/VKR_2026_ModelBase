import ast, sys

files = [
    "catboost_floader/models/direction.py",
    "catboost_floader/evaluation/backtest.py",
    "catboost_floader/app/main.py",
]
for f in files:
    try:
        s = open(f, "r", encoding="utf-8").read()
        ast.parse(s)
        print(f"OK: {f}")
    except SyntaxError as e:
        print(f"SyntaxError in {f}: {e.msg} at line {e.lineno} col {e.offset}")
        print("Line:", open(f).read().splitlines()[e.lineno-1])
    except Exception as e:
        print(f"Error parsing {f}: {e}")
