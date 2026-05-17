// Real `codeward` output captured against a fresh FastAPI checkout
// (github.com/fastapi/fastapi @ main, May 2026). Re-capture with:
//   cd /tmp/demo-fastapi && codeward map | head -25
//   ... etc

export const mapOutput = `# Codeward semantic summary
Python repo
Root: /tmp/demo-fastapi
Files: 1129 code, 598 tests
Languages: Python=521, Bash=6, JavaScript=4

Important files:
- fastapi/routing.py — Python, 4956 lines, 47 symbols, source
- fastapi/applications.py — Python, 1880 lines, 38 symbols, source
- fastapi/params.py — Python, 754 lines, 21 symbols, source
- fastapi/openapi/models.py — Python, 435 lines, 40 symbols, data/model layer
- fastapi/security/http.py — Python, 417 lines, 17 symbols, source
- fastapi/openapi/docs.py — Python, 389 lines, 4 symbols, source`;

export const routesOutput = `$ codeward routes --filter /items --method GET docs_src/security

# Codeward routes (6)
  GET  /items/           →  read_items      (docs_src/app_testing/tutorial003_py310.py:16)
  GET  /users/me/items/  →  read_own_items  (docs_src/security/tutorial005_py310.py:171)`;

export const preflightOutput = `# Codeward preflight: fastapi/routing.py
  language=Python, lines=4956, symbols=47, blast_radius=HIGH
  dependents (14): docs_src/custom_request_and_route/tutorial001.py,
                   tests/test_custom_route_class.py,
                   tests/test_route_scope.py, …
  likely tests: tests/test_custom_route_class.py,
                tests/test_generate_unique_id_function.py,
                tests/test_route_scope.py
  co-change neighbors: docs/en/docs/js/custom.js, …
  recommended: pytest tests/test_custom_route_class.py; inspect dependents`;

export const symbolOutput = `# Codeward semantic summary
Symbol: APIRoute
Defined: fastapi/routing.py:811  class APIRoute(routing.Route)  [python_ast/high]
Methods:
- __init__
- get_route_handler
- matches
Callers:
- docs_src/custom_request_and_route/tutorial001_py310.py:18: class GzipRoute(APIRoute):
- docs_src/custom_request_and_route/tutorial002_py310.py:8:  class ValidationErrorLoggingRoute(APIRoute):
- docs_src/custom_request_and_route/tutorial003_py310.py:8:  class TimedRoute(APIRoute):
- fastapi/applications.py:765:  Callable[[routing.APIRoute], str],
- fastapi/applications.py:1187: generate_unique_id_function: ...APIRoute...
  ... 6 more`;
