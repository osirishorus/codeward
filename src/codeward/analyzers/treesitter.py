"""Tree-sitter based analyzers for non-Python languages.

The default install includes tree-sitter and supported grammars. If a parser
cannot be imported, the regex-based fallback in index.py:analyze_generic stays
in effect.

Languages with first-class extraction: Go, Rust, TypeScript, JavaScript, Java,
Ruby, PHP, C#, C, C++, Kotlin, Swift, Scala, Bash, Lua, Elixir. Each emits
Symbols matching the same shape as the Python analyzer: name, kind, file,
line, end_line, signature.

Each grammar is imported lazily — if a wheel is missing for the platform,
that single language degrades to regex while every other language keeps
working.
"""
from __future__ import annotations

from pathlib import Path

try:
    from tree_sitter import Language, Parser
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False


_PARSERS_CACHE: dict[str, "Parser"] = {}
# Tracks languages we've already failed to load so we don't keep retrying the
# import on every file. Maps lang → True (failed) or absent.
_PARSER_LOAD_FAILED: dict[str, bool] = {}


def _load_language(lang: str):
    """Import the relevant tree-sitter package on demand and return its language
    object, or None if the package isn't installed. Per-language failure is
    cached so we don't pay the ImportError cost on every file."""
    if _PARSER_LOAD_FAILED.get(lang):
        return None
    try:
        if lang == "go":
            import tree_sitter_go as m
            return m.language()
        if lang == "rust":
            import tree_sitter_rust as m
            return m.language()
        if lang == "typescript":
            import tree_sitter_typescript as m
            return m.language_typescript()
        if lang == "tsx":
            import tree_sitter_typescript as m
            return m.language_tsx()
        if lang == "javascript":
            import tree_sitter_javascript as m
            return m.language()
        if lang == "java":
            import tree_sitter_java as m
            return m.language()
        if lang == "ruby":
            import tree_sitter_ruby as m
            return m.language()
        if lang == "php":
            import tree_sitter_php as m
            return m.language_php()
        if lang == "csharp":
            import tree_sitter_c_sharp as m
            return m.language()
        if lang == "c":
            import tree_sitter_c as m
            return m.language()
        if lang == "cpp":
            import tree_sitter_cpp as m
            return m.language()
        if lang == "kotlin":
            import tree_sitter_kotlin as m
            return m.language()
        if lang == "swift":
            import tree_sitter_swift as m
            return m.language()
        if lang == "scala":
            import tree_sitter_scala as m
            return m.language()
        if lang == "bash":
            import tree_sitter_bash as m
            return m.language()
        if lang == "lua":
            import tree_sitter_lua as m
            return m.language()
        if lang == "elixir":
            import tree_sitter_elixir as m
            return m.language()
    except Exception:
        _PARSER_LOAD_FAILED[lang] = True
    return None


def _get_parser(lang: str):
    """Lazily build and cache parsers. Returns None if the language isn't supported
    or the grammar wheel isn't installed."""
    if not HAS_TREE_SITTER:
        return None
    if lang in _PARSERS_CACHE:
        return _PARSERS_CACHE[lang]
    obj = _load_language(lang)
    if obj is None:
        return None
    try:
        parser = Parser(Language(obj))
    except Exception:
        _PARSER_LOAD_FAILED[lang] = True
        return None
    _PARSERS_CACHE[lang] = parser
    return parser


def language_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".go": "go",
        ".rs": "rust",
        ".ts": "typescript", ".tsx": "tsx",
        ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".java": "java",
        ".rb": "ruby",
        ".php": "php",
        ".cs": "csharp",
        ".c": "c", ".h": "c",
        ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
        ".kt": "kotlin", ".kts": "kotlin",
        ".swift": "swift",
        ".scala": "scala", ".sc": "scala",
        ".sh": "bash", ".bash": "bash",
        ".lua": "lua",
        ".ex": "elixir", ".exs": "elixir",
    }.get(suffix)


def parse_for_path(path: str, text: str):
    lang = language_for_path(path)
    if lang is None:
        return None
    parser = _get_parser(lang)
    if parser is None:
        return None
    try:
        src = text.encode("utf-8")
        return parser.parse(src).root_node, src
    except Exception:
        return None


def analyze_treesitter(info, text: str) -> bool:
    """Populate `info.symbols` (and end_line / signature) using tree-sitter.
    Returns True if extraction ran; False to signal the caller should fall back
    to regex-based analysis. Imports are still handled by the regex path —
    tree-sitter just gives us better symbol shapes."""
    if not HAS_TREE_SITTER:
        return False
    lang = language_for_path(info.path)
    if lang is None:
        return False
    parser = _get_parser(lang)
    if parser is None:
        return False
    try:
        src = text.encode("utf-8")
        tree = parser.parse(src)
    except Exception:
        return False

    from ..index import Symbol  # local import to avoid cycle

    if lang == "go":
        _extract_go(tree.root_node, src, info, Symbol)
    elif lang == "rust":
        _extract_rust(tree.root_node, src, info, Symbol)
    elif lang in ("typescript", "tsx", "javascript"):
        _extract_jsts(tree.root_node, src, info, Symbol, ts_mode=(lang in ("typescript", "tsx")))
    elif lang == "java":
        _extract_java(tree.root_node, src, info, Symbol)
    elif lang == "ruby":
        _extract_ruby(tree.root_node, src, info, Symbol)
    elif lang == "php":
        _extract_php(tree.root_node, src, info, Symbol)
    elif lang == "csharp":
        _extract_csharp(tree.root_node, src, info, Symbol)
    elif lang in ("c", "cpp"):
        _extract_c_family(tree.root_node, src, info, Symbol, cpp=(lang == "cpp"))
    elif lang == "kotlin":
        _extract_kotlin(tree.root_node, src, info, Symbol)
    elif lang == "swift":
        _extract_swift(tree.root_node, src, info, Symbol)
    elif lang == "scala":
        _extract_scala(tree.root_node, src, info, Symbol)
    elif lang == "bash":
        _extract_bash(tree.root_node, src, info, Symbol)
    elif lang == "lua":
        _extract_lua(tree.root_node, src, info, Symbol)
    elif lang == "elixir":
        _extract_elixir(tree.root_node, src, info, Symbol)
    else:
        return False
    _link_methods_to_classes(info)
    return True


def _link_methods_to_classes(info) -> None:
    """For languages where methods are not nested in the class declaration
    (Go's `func (s *Server) Foo()`, Rust's separate `impl` blocks, Ruby's
    re-opening), attach method names to the parent class's `methods` list so
    `cmd_read` can render them grouped under the class."""
    classes = {s.name: s for s in info.symbols if s.kind in ("class", "interface", "enum", "module")}
    for s in info.symbols:
        if s.kind != "method" or "." not in s.name:
            continue
        cls_name = s.name.split(".", 1)[0]
        if cls_name in classes:
            short = s.name.split(".", 1)[1]
            if short not in classes[cls_name].methods:
                classes[cls_name].methods.append(short)


def _txt(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _signature_lines(node, src: bytes) -> str:
    """Return the first line(s) of the node up to the body, condensed."""
    body_starts = node.start_byte
    for c in node.children:
        if c.type in ("block", "compound_statement", "function_body", "class_body",
                      "interface_body", "method_body", "constructor_body", "do_block",
                      "field_declaration_list", "declaration_list", "statement_block"):
            body_starts = c.start_byte
            break
    sig = src[node.start_byte:body_starts].decode("utf-8", errors="replace")
    return " ".join(sig.split()).rstrip("{").strip()


def _line_range(node) -> tuple[int, int]:
    return (node.start_point[0] + 1, node.end_point[0] + 1)


def _named_child(node, type_name: str):
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _field(node, field_name: str):
    return node.child_by_field_name(field_name)


def _extract_go(root, src: bytes, info, Symbol) -> None:
    for n in root.children:
        if n.type == "function_declaration":
            name_node = _field(n, "name")
            if name_node:
                line, end = _line_range(n)
                info.symbols.append(Symbol(_txt(name_node, src), "function", info.path, line, [], _signature_lines(n, src), end))
        elif n.type == "method_declaration":
            name_node = _field(n, "name")
            recv = _field(n, "receiver")
            recv_name = ""
            if recv:
                # receiver is `(s *Server)` — find the type identifier
                for c in recv.children:
                    for t in c.children if c.children else [c]:
                        if t.type in ("type_identifier", "pointer_type"):
                            if t.type == "pointer_type":
                                inner = _named_child(t, "type_identifier")
                                if inner:
                                    recv_name = _txt(inner, src)
                            else:
                                recv_name = _txt(t, src)
                            break
                    if recv_name:
                        break
            if name_node:
                method_name = _txt(name_node, src)
                full = f"{recv_name}.{method_name}" if recv_name else method_name
                line, end = _line_range(n)
                info.symbols.append(Symbol(full, "method", info.path, line, [], _signature_lines(n, src), end))
        elif n.type == "type_declaration":
            for spec in n.children:
                if spec.type != "type_spec":
                    continue
                name_node = _field(spec, "name")
                type_node = _field(spec, "type")
                if not name_node:
                    continue
                kind = "class"
                if type_node and type_node.type == "interface_type":
                    kind = "interface"
                elif type_node and type_node.type == "struct_type":
                    kind = "class"
                else:
                    kind = "type"
                line, end = _line_range(n)
                info.symbols.append(Symbol(_txt(name_node, src), kind, info.path, line, [], _signature_lines(spec, src), end))


def _extract_rust(root, src: bytes, info, Symbol) -> None:
    for n in root.children:
        if n.type == "function_item":
            name_node = _field(n, "name")
            if name_node:
                line, end = _line_range(n)
                info.symbols.append(Symbol(_txt(name_node, src), "function", info.path, line, [], _signature_lines(n, src), end))
        elif n.type == "struct_item":
            name_node = _field(n, "name")
            if name_node:
                line, end = _line_range(n)
                info.symbols.append(Symbol(_txt(name_node, src), "class", info.path, line, [], _signature_lines(n, src), end))
        elif n.type == "enum_item":
            name_node = _field(n, "name")
            if name_node:
                line, end = _line_range(n)
                info.symbols.append(Symbol(_txt(name_node, src), "enum", info.path, line, [], _signature_lines(n, src), end))
        elif n.type == "trait_item":
            name_node = _field(n, "name")
            if name_node:
                line, end = _line_range(n)
                info.symbols.append(Symbol(_txt(name_node, src), "interface", info.path, line, [], _signature_lines(n, src), end))
        elif n.type == "impl_item":
            type_node = _field(n, "type")
            if not type_node:
                continue
            type_name = _txt(type_node, src)
            body = _named_child(n, "declaration_list")
            if not body:
                continue
            for fn in body.children:
                if fn.type == "function_item":
                    fn_name = _field(fn, "name")
                    if fn_name:
                        line, end = _line_range(fn)
                        info.symbols.append(Symbol(f"{type_name}.{_txt(fn_name, src)}", "method", info.path, line, [], _signature_lines(fn, src), end))


def _extract_jsts(root, src: bytes, info, Symbol, ts_mode: bool) -> None:
    def emit_class(class_node, name_node):
        if not name_node:
            return
        cls_name = _txt(name_node, src)
        line, end = _line_range(class_node)
        method_names: list[str] = []
        body = _named_child(class_node, "class_body")
        if body:
            for member in body.children:
                if member.type in ("method_definition",):
                    mname = _field(member, "name")
                    if mname:
                        m_text = _txt(mname, src)
                        method_names.append(m_text)
                        ml, me = _line_range(member)
                        info.symbols.append(Symbol(f"{cls_name}.{m_text}", "method", info.path, ml, [], _signature_lines(member, src), me))
        info.symbols.append(Symbol(cls_name, "class", info.path, line, method_names, _signature_lines(class_node, src), end))

    def emit_function(fn_node, name_node):
        if not name_node:
            return
        line, end = _line_range(fn_node)
        info.symbols.append(Symbol(_txt(name_node, src), "function", info.path, line, [], _signature_lines(fn_node, src), end))

    def visit(node):
        for c in node.children:
            if c.type == "export_statement":
                visit(c)
                continue
            if c.type == "class_declaration":
                emit_class(c, _field(c, "name"))
            elif c.type == "function_declaration":
                emit_function(c, _field(c, "name"))
            elif c.type == "interface_declaration" and ts_mode:
                name = _field(c, "name")
                if name:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(_txt(name, src), "interface", info.path, line, [], _signature_lines(c, src), end))
            elif c.type == "type_alias_declaration" and ts_mode:
                name = _field(c, "name")
                if name:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(_txt(name, src), "type", info.path, line, [], _signature_lines(c, src), end))
            elif c.type == "lexical_declaration":
                # const Foo = (x) => ... or const Foo = function(...)
                for d in c.children:
                    if d.type != "variable_declarator":
                        continue
                    name = _field(d, "name")
                    value = _field(d, "value")
                    if name and value and value.type in ("arrow_function", "function_expression", "function"):
                        line, end = _line_range(c)
                        info.symbols.append(Symbol(_txt(name, src), "function", info.path, line, [], _signature_lines(d, src), end))

    visit(root)


def _extract_java(root, src: bytes, info, Symbol) -> None:
    def emit_class_like(node, kind: str):
        name = _field(node, "name")
        if not name:
            return
        cls_name = _txt(name, src)
        line, end = _line_range(node)
        body = _field(node, "body")
        method_names: list[str] = []
        if body:
            for member in body.children:
                if member.type in ("method_declaration", "constructor_declaration"):
                    mname = _field(member, "name")
                    if mname:
                        m_text = _txt(mname, src)
                        method_names.append(m_text)
                        ml, me = _line_range(member)
                        info.symbols.append(Symbol(f"{cls_name}.{m_text}", "method", info.path, ml, [], _signature_lines(member, src), me))
        info.symbols.append(Symbol(cls_name, kind, info.path, line, method_names, _signature_lines(node, src), end))

    def visit(node):
        for c in node.children:
            if c.type == "class_declaration":
                emit_class_like(c, "class")
            elif c.type == "interface_declaration":
                emit_class_like(c, "interface")
            elif c.type == "enum_declaration":
                emit_class_like(c, "enum")
            elif c.type in ("package_declaration", "import_declaration"):
                continue

    visit(root)


def _extract_ruby(root, src: bytes, info, Symbol) -> None:
    def name_of(node):
        for c in node.children:
            if c.type in ("constant", "identifier"):
                return _txt(c, src)
        return None

    def visit(node, parent: str | None = None):
        for c in node.children:
            if c.type in ("class", "module"):
                cname = name_of(c) or ""
                full = f"{parent}.{cname}" if parent else cname
                line, end = _line_range(c)
                method_names: list[str] = []
                # Collect methods one level down
                body = _named_child(c, "body_statement")
                if body:
                    for m in body.children:
                        if m.type in ("method", "singleton_method"):
                            mname = name_of(m)
                            if mname:
                                method_names.append(mname)
                                ml, me = _line_range(m)
                                info.symbols.append(Symbol(f"{full}.{mname}", "method", info.path, ml, [], _signature_lines(m, src), me))
                kind = "class" if c.type == "class" else "module"
                info.symbols.append(Symbol(full, kind, info.path, line, method_names, _signature_lines(c, src), end))
                visit(c, full)
            elif c.type == "method" and parent is None:
                mname = name_of(c)
                if mname:
                    ml, me = _line_range(c)
                    info.symbols.append(Symbol(mname, "function", info.path, ml, [], _signature_lines(c, src), me))

    visit(root)


def _extract_php(root, src: bytes, info, Symbol) -> None:
    def visit(node):
        for c in node.children:
            if c.type == "function_definition":
                name = _field(c, "name")
                if name:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(_txt(name, src), "function", info.path, line, [], _signature_lines(c, src), end))
            elif c.type in ("class_declaration", "interface_declaration", "trait_declaration"):
                name = _field(c, "name")
                if not name:
                    continue
                cls_name = _txt(name, src)
                line, end = _line_range(c)
                kind = {"class_declaration": "class", "interface_declaration": "interface", "trait_declaration": "trait"}[c.type]
                method_names: list[str] = []
                body = _field(c, "body")
                if body:
                    for member in body.children:
                        if member.type == "method_declaration":
                            mname = _field(member, "name")
                            if mname:
                                m_text = _txt(mname, src)
                                method_names.append(m_text)
                                ml, me = _line_range(member)
                                info.symbols.append(Symbol(f"{cls_name}.{m_text}", "method", info.path, ml, [], _signature_lines(member, src), me))
                info.symbols.append(Symbol(cls_name, kind, info.path, line, method_names, _signature_lines(c, src), end))
            elif c.type in ("namespace_definition", "namespace_use_declaration", "php_tag"):
                visit(c)

    visit(root)


def _extract_c_family(root, src: bytes, info, Symbol, *, cpp: bool) -> None:
    """C / C++. Handles top-level function definitions, struct/union/class
    (C++), enum, and typedef. C++ member functions inside a class body get
    Class.method names; out-of-class definitions (`Class::method`) also get
    Class.method names so they group together."""
    def visit(node) -> None:
        for c in node.children:
            t = c.type
            if t == "function_definition":
                declarator = _field(c, "declarator")
                name = _function_declarator_name(declarator, src) if declarator else None
                if name:
                    line, end = _line_range(c)
                    # name may be 'Class::method' for out-of-class defs.
                    if cpp and "::" in name:
                        cls, _, meth = name.rpartition("::")
                        full = f"{cls}.{meth}"
                        info.symbols.append(Symbol(full, "method", info.path, line, [], _signature_lines(c, src), end))
                    else:
                        info.symbols.append(Symbol(name, "function", info.path, line, [], _signature_lines(c, src), end))
            elif t in ("struct_specifier", "union_specifier", "class_specifier"):
                name_node = _field(c, "name")
                if name_node:
                    cls_name = _txt(name_node, src)
                    line, end = _line_range(c)
                    method_names: list[str] = []
                    body = _field(c, "body")
                    if body:
                        for member in body.children:
                            if member.type == "function_definition":
                                declarator = _field(member, "declarator")
                                mname = _function_declarator_name(declarator, src) if declarator else None
                                if mname:
                                    short = mname.split("::")[-1]
                                    method_names.append(short)
                                    ml, me = _line_range(member)
                                    info.symbols.append(Symbol(f"{cls_name}.{short}", "method", info.path, ml, [], _signature_lines(member, src), me))
                    kind = {
                        "class_specifier": "class",
                        "struct_specifier": "struct",
                        "union_specifier": "union",
                    }.get(t, "class")
                    info.symbols.append(Symbol(cls_name, kind, info.path, line, method_names, _signature_lines(c, src), end))
            elif t == "enum_specifier":
                name_node = _field(c, "name")
                if name_node:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(_txt(name_node, src), "enum", info.path, line, [], _signature_lines(c, src), end))
            elif t in ("namespace_definition", "linkage_specification", "preproc_if", "preproc_ifdef"):
                visit(c)

    visit(root)


def _function_declarator_name(declarator, src: bytes) -> str | None:
    """Walk a (possibly pointer-wrapped) C/C++ declarator to its inner name.
    `int *foo()` → 'foo'; `Class::method()` → 'Class::method'; `*foo()` → 'foo'."""
    if declarator is None:
        return None
    if declarator.type == "function_declarator":
        inner = _field(declarator, "declarator")
        if inner is None:
            return None
        if inner.type in ("identifier", "field_identifier", "type_identifier"):
            return _txt(inner, src)
        if inner.type == "qualified_identifier":
            return _txt(inner, src)
        if inner.type in ("pointer_declarator", "reference_declarator"):
            return _function_declarator_name(inner, src)
        return _txt(inner, src)
    if declarator.type in ("pointer_declarator", "reference_declarator"):
        return _function_declarator_name(_field(declarator, "declarator"), src)
    return None


def _extract_kotlin(root, src: bytes, info, Symbol) -> None:
    """Kotlin: function_declaration, class_declaration, object_declaration.
    Methods inside a class body are emitted as Class.method.

    Newer tree-sitter-kotlin (1.x) emits bare `identifier` rather than
    `simple_identifier` for declaration names — we accept either.
    """
    def first_name(node):
        # Walk immediate children and return the first identifier-like node.
        for c in node.children:
            if c.type in ("identifier", "simple_identifier", "type_identifier"):
                return c
        return None

    def visit(node, parent: str | None = None) -> None:
        for c in node.children:
            t = c.type
            if t in ("class_declaration", "object_declaration", "interface_declaration"):
                name_node = first_name(c)
                if name_node is None:
                    continue
                cls_name = _txt(name_node, src)
                line, end = _line_range(c)
                method_names: list[str] = []
                body = _named_child(c, "class_body") or _named_child(c, "enum_class_body")
                if body:
                    for member in body.children:
                        if member.type == "function_declaration":
                            mname_node = first_name(member)
                            if mname_node:
                                m_text = _txt(mname_node, src)
                                method_names.append(m_text)
                                ml, me = _line_range(member)
                                info.symbols.append(Symbol(f"{cls_name}.{m_text}", "method", info.path, ml, [], _signature_lines(member, src), me))
                kind = "interface" if t == "interface_declaration" else ("object" if t == "object_declaration" else "class")
                info.symbols.append(Symbol(cls_name, kind, info.path, line, method_names, _signature_lines(c, src), end))
            elif t == "function_declaration" and parent is None:
                name_node = first_name(c)
                if name_node:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(_txt(name_node, src), "function", info.path, line, [], _signature_lines(c, src), end))

    visit(root)


def _extract_swift(root, src: bytes, info, Symbol) -> None:
    """Swift: class_declaration, struct_declaration, enum_declaration,
    protocol_declaration, function_declaration. Members defined inside a
    type body get Type.member names."""
    def member_name(member):
        # Swift methods carry the identifier as a 'name' field on some tree-sitter
        # builds and as a 'simple_identifier' child on others.
        name_node = _field(member, "name") or _named_child(member, "simple_identifier")
        return _txt(name_node, src) if name_node else None

    def visit(node) -> None:
        for c in node.children:
            t = c.type
            if t in ("class_declaration", "struct_declaration", "enum_declaration", "protocol_declaration", "extension_declaration"):
                name_node = _field(c, "name") or _named_child(c, "type_identifier") or _named_child(c, "user_type")
                if name_node is None:
                    continue
                cls_name = _txt(name_node, src)
                line, end = _line_range(c)
                method_names: list[str] = []
                # Class body type differs across swift grammar versions.
                body = (
                    _field(c, "body")
                    or _named_child(c, "class_body")
                    or _named_child(c, "struct_body")
                    or _named_child(c, "enum_class_body")
                )
                if body:
                    for member in body.children:
                        if member.type in ("function_declaration", "init_declaration", "deinit_declaration"):
                            mname = member_name(member) or ("init" if member.type == "init_declaration" else None)
                            if mname:
                                method_names.append(mname)
                                ml, me = _line_range(member)
                                info.symbols.append(Symbol(f"{cls_name}.{mname}", "method", info.path, ml, [], _signature_lines(member, src), me))
                kind = {
                    "protocol_declaration": "interface",
                    "enum_declaration": "enum",
                    "struct_declaration": "class",
                    "extension_declaration": "extension",
                }.get(t, "class")
                info.symbols.append(Symbol(cls_name, kind, info.path, line, method_names, _signature_lines(c, src), end))
            elif t == "function_declaration":
                name = member_name(c)
                if name:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(name, "function", info.path, line, [], _signature_lines(c, src), end))

    visit(root)


def _extract_scala(root, src: bytes, info, Symbol) -> None:
    """Scala: class_definition, object_definition, trait_definition, function_definition."""
    def visit(node) -> None:
        for c in node.children:
            t = c.type
            if t in ("class_definition", "object_definition", "trait_definition"):
                name_node = _field(c, "name")
                if name_node is None:
                    continue
                cls_name = _txt(name_node, src)
                line, end = _line_range(c)
                method_names: list[str] = []
                body = _field(c, "body") or _named_child(c, "template_body")
                if body:
                    for member in body.children:
                        if member.type in ("function_definition", "function_declaration"):
                            mname_node = _field(member, "name")
                            if mname_node:
                                m_text = _txt(mname_node, src)
                                method_names.append(m_text)
                                ml, me = _line_range(member)
                                info.symbols.append(Symbol(f"{cls_name}.{m_text}", "method", info.path, ml, [], _signature_lines(member, src), me))
                kind = {"object_definition": "object", "trait_definition": "interface"}.get(t, "class")
                info.symbols.append(Symbol(cls_name, kind, info.path, line, method_names, _signature_lines(c, src), end))
            elif t in ("function_definition", "function_declaration"):
                name_node = _field(c, "name")
                if name_node:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(_txt(name_node, src), "function", info.path, line, [], _signature_lines(c, src), end))
            elif t in ("package_clause", "package_object"):
                visit(c)

    visit(root)


def _extract_bash(root, src: bytes, info, Symbol) -> None:
    """Bash: function_definition. Single namespace; no classes."""
    def visit(node) -> None:
        for c in node.children:
            if c.type == "function_definition":
                name_node = _field(c, "name") or _named_child(c, "word")
                if name_node:
                    line, end = _line_range(c)
                    info.symbols.append(Symbol(_txt(name_node, src), "function", info.path, line, [], _signature_lines(c, src), end))
            elif c.type == "compound_statement":
                visit(c)

    visit(root)


def _extract_lua(root, src: bytes, info, Symbol) -> None:
    """Lua: function_declaration (top-level), function_definition (assigned).
    Also handles `function M.foo()` / `function M:foo()` colon syntax — these
    are emitted as `function` (not `method`) because Lua has no true classes;
    `M` is a table. `cmd_read` would otherwise hide every dotted-name function
    behind a non-existent class."""
    def visit(node) -> None:
        for c in node.children:
            if c.type == "function_declaration":
                # name may be 'identifier' or 'dot_index_expression' / 'method_index_expression'
                name_node = _field(c, "name")
                if name_node is None:
                    continue
                raw_name = _txt(name_node, src).replace(":", ".")
                line, end = _line_range(c)
                info.symbols.append(Symbol(raw_name, "function", info.path, line, [], _signature_lines(c, src), end))
            elif c.type == "variable_declaration" or c.type == "assignment_statement":
                # local foo = function(...) ... end  →  capture name + range
                value = _field(c, "value")
                if value and value.type == "function_definition":
                    target = _field(c, "name")
                    if target:
                        name = _txt(target, src)
                        line, end = _line_range(c)
                        info.symbols.append(Symbol(name, "function", info.path, line, [], _signature_lines(c, src), end))
            elif c.type in ("if_statement", "do_statement"):
                visit(c)

    visit(root)


def _extract_elixir(root, src: bytes, info, Symbol) -> None:
    """Elixir: module definitions (`defmodule Foo do ... end`) and function
    clauses (`def foo(...)` / `defp foo(...)`).

    Tree-sitter-elixir represents these as `call` nodes with a target identifier,
    not dedicated `module_definition` / `function_definition` types. We walk
    calls and recognize the relevant atoms by name.
    """
    def call_target(node) -> str | None:
        target = _field(node, "target")
        if target is None:
            return None
        return _txt(target, src)

    def first_arg_atom(node) -> str | None:
        args = _field(node, "arguments")
        if args is None:
            for child in node.children:
                if child.type == "arguments":
                    args = child
                    break
        if args is None:
            return None
        for arg in args.children:
            if arg.type == "alias":
                return _txt(arg, src)
            if arg.type == "identifier":
                return _txt(arg, src)
            if arg.type == "call":
                inner = _field(arg, "target")
                if inner is not None:
                    return _txt(inner, src)
        return None

    def visit(node, parent: str | None) -> None:
        for c in node.children:
            if c.type == "call":
                target = call_target(c)
                if target == "defmodule":
                    mod_name = first_arg_atom(c)
                    if mod_name:
                        line, end = _line_range(c)
                        info.symbols.append(Symbol(mod_name, "module", info.path, line, [], _signature_lines(c, src), end))
                        visit(c, mod_name)
                    continue
                if target in ("def", "defp", "defmacro", "defmacrop"):
                    fn_name = first_arg_atom(c)
                    if fn_name:
                        line, end = _line_range(c)
                        full = f"{parent}.{fn_name}" if parent else fn_name
                        kind = "method" if parent else "function"
                        info.symbols.append(Symbol(full, kind, info.path, line, [], _signature_lines(c, src), end))
                    continue
            visit(c, parent)

    visit(root, None)


def _extract_csharp(root, src: bytes, info, Symbol) -> None:
    def emit_type(node, kind: str):
        name = _field(node, "name")
        if not name:
            return
        cls_name = _txt(name, src)
        line, end = _line_range(node)
        body = _field(node, "body")
        method_names: list[str] = []
        if body:
            for member in body.children:
                if member.type in ("method_declaration", "constructor_declaration"):
                    mname = _field(member, "name")
                    if mname:
                        m_text = _txt(mname, src)
                        method_names.append(m_text)
                        ml, me = _line_range(member)
                        info.symbols.append(Symbol(f"{cls_name}.{m_text}", "method", info.path, ml, [], _signature_lines(member, src), me))
        info.symbols.append(Symbol(cls_name, kind, info.path, line, method_names, _signature_lines(node, src), end))

    def visit(node):
        for c in node.children:
            if c.type == "namespace_declaration" or c.type == "file_scoped_namespace_declaration":
                visit(c)
            elif c.type == "class_declaration":
                emit_type(c, "class")
            elif c.type == "interface_declaration":
                emit_type(c, "interface")
            elif c.type == "struct_declaration":
                emit_type(c, "class")
            elif c.type == "enum_declaration":
                emit_type(c, "enum")

    visit(root)
