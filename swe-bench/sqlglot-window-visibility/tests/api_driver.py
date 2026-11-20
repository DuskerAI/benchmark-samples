"""Untrusted-side JSON transport; no hidden inputs, expectations, or test inventory."""
import contextlib
import copy
import json
import sys

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else '/app')
with contextlib.redirect_stdout(sys.stderr):
    from sqlglot import parse_one
    from sqlglot.executor import execute
    from sqlglot.executor.python import PythonExecutor
    from sqlglot.executor.table import Table, ensure_tables
    from sqlglot.optimizer import optimize
    from sqlglot.planner import Plan

for line in sys.stdin:
    request = json.loads(line)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            sql, schema, tables = request['sql'], request['schema'], request['tables']
            before = copy.deepcopy(tables)
            tree = parse_one(sql)
            tree_before = repr(tree)
            if request['api'] in {'plan', 'custom-env', 'custom-aggregate'}:
                expression = optimize(tree, schema=schema, leave_tables_isolated=True)
                plan = Plan(expression)
                plan_before = repr(plan.expression)
                native_tables = {name: Table(list(schema[name]), [tuple(row.get(c) for c in schema[name]) for row in rows])
                                 for name, rows in tables.items()}
                table_before = {name: (value.columns, copy.deepcopy(value.rows)) for name, value in native_tables.items()}
                env = {'ABS': lambda value: None if value is None else abs(value) + 17} if request['api'] == 'custom-env' else None
                if request['api'] == 'custom-aggregate':
                    def replacement_sum(values):
                        values = [v for v in values if v is not None]
                        return sum(values) + 101 if values else None
                    env = {'SUM': replacement_sum}
                executor = PythonExecutor(tables=ensure_tables(native_tables), env=env)
                first = executor.execute(plan)
                saved = (tuple(first.columns), copy.deepcopy(first.rows))
                second = executor.execute(plan)
                unchanged = all((value.columns, value.rows) == table_before[name] for name, value in native_tables.items())
                unchanged = unchanged and plan_before == repr(plan.expression)
            else:
                first = execute(tree, schema=schema, tables=tables)
                saved = (tuple(first.columns), copy.deepcopy(first.rows))
                second = execute(tree, schema=schema, tables=tables)
                unchanged = True
            value = {'columns': saved[0], 'rows': saved[1],
                     'repeat_equal': saved == (tuple(second.columns), second.rows),
                     'inputs_unchanged': unchanged and tables == before and repr(tree) == tree_before}
        result = {'id': request['id'], 'status': 'ok', 'value': value}
    except Exception as error:
        result = {'id': request['id'], 'status': type(error).__name__, 'message': str(error)}
    print(json.dumps(result), flush=True)
