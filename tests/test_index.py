import sqlite3

from codeward.index import RepoIndex


def test_import_extraction_resolves_non_python_languages(tmp_path):
    files = {
        "src/main/java/com/example/App.java": "import com.example.Service;\nclass App {}\n",
        "src/main/java/com/example/Service.java": "class Service {}\n",
        "src/App.kt": "import com.example.Tool\nfun main() {}\n",
        "src/com/example/Tool.kt": "class Tool\n",
        "src/App.scala": "import com.example.{Thing, Other}\nobject App {}\n",
        "src/com/example/Thing.scala": "class Thing\n",
        "src/com/example/Other.scala": "class Other\n",
        "src/Program.cs": "using Company.Product.Core;\nclass Program {}\n",
        "src/Company/Product/Core.cs": "class Core {}\n",
        "src/Main.swift": "import LocalModule\nfunc main() {}\n",
        "src/LocalModule.swift": "struct LocalModule {}\n",
        "src/App/Controller.php": "<?php use App\\Service\\UserService; require_once 'helpers.php';\n",
        "src/App/Service/UserService.php": "<?php class UserService {}\n",
        "src/App/helpers.php": "<?php function helper() {}\n",
        "lib/app.rb": "require_relative 'helpers'\nrequire 'my_app/user'\n",
        "lib/helpers.rb": "def helper; end\n",
        "lib/my_app/user.rb": "class User; end\n",
        "src/main.c": '#include "local.h"\nint main(void) { return 0; }\n',
        "src/local.h": "int local(void);\n",
        "lib/my_app/accounts.ex": "defmodule MyApp.Accounts do\n  alias MyApp.Accounts.User\nend\n",
        "lib/my_app/accounts/user.ex": "defmodule MyApp.Accounts.User do\nend\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    idx = RepoIndex(tmp_path, use_cache=False)

    assert "src/main/java/com/example/Service.java" in idx.files["src/main/java/com/example/App.java"].resolved_deps
    assert "src/com/example/Tool.kt" in idx.files["src/App.kt"].resolved_deps
    assert {"src/com/example/Thing.scala", "src/com/example/Other.scala"} <= set(idx.files["src/App.scala"].resolved_deps)
    assert "src/Company/Product/Core.cs" in idx.files["src/Program.cs"].resolved_deps
    assert "src/LocalModule.swift" in idx.files["src/Main.swift"].resolved_deps
    assert {"src/App/Service/UserService.php", "src/App/helpers.php"} <= set(idx.files["src/App/Controller.php"].resolved_deps)
    assert {"lib/helpers.rb", "lib/my_app/user.rb"} <= set(idx.files["lib/app.rb"].resolved_deps)
    assert "src/local.h" in idx.files["src/main.c"].resolved_deps
    assert "lib/my_app/accounts/user.ex" in idx.files["lib/my_app/accounts.ex"].resolved_deps


def test_references_to_orders_high_confidence_before_path_sort(tmp_path):
    (tmp_path / "a.c").write_text("int main(void) { return target(); }\n")
    (tmp_path / "z.py").write_text("def target():\n    return 1\n\ndef caller():\n    return target()\n")

    idx = RepoIndex(tmp_path, use_cache=False)
    refs = idx.references_to("target")

    assert refs
    assert refs[0].file == "z.py"
    assert refs[0].confidence == "high"


def test_qualified_python_references_demote_same_name_collisions(tmp_path):
    (tmp_path / "app.py").write_text(
        "class Target:\n"
        "    def save(self):\n"
        "        return self.save()\n"
        "\n"
        "class Other:\n"
        "    def save(self):\n"
        "        return self.save()\n"
        "\n"
        "def run():\n"
        "    Target().save()\n"
        "    Other().save()\n"
    )

    idx = RepoIndex(tmp_path, use_cache=False)
    refs = idx.references_to("Target.save")
    high_text = [r.text for r in refs if r.confidence == "high"]
    low_text = [r.text for r in refs if r.confidence == "low"]

    assert "return self.save()" in high_text
    assert "Target().save()" in high_text
    assert "Other().save()" in low_text


def test_regex_references_ignore_comments_and_strings(tmp_path):
    (tmp_path / "src.c").write_text(
        'const char *s = "target() in a string";\n'
        "// target() in a comment\n"
        "int main(void) { return target(); }\n"
    )

    idx = RepoIndex(tmp_path, use_cache=False)
    refs = idx.references_to("target")

    assert [r.line for r in refs] == [3]


def test_write_sqlite_replaces_db_while_existing_reader_is_open(tmp_path):
    (tmp_path / "a.py").write_text("def first():\n    return 1\n")
    idx = RepoIndex(tmp_path, use_cache=False)
    db = idx.write_sqlite()
    reader = sqlite3.connect(db)
    try:
        reader.execute("begin")
        assert reader.execute("select count(*) from files").fetchone()[0] == 1
        (tmp_path / "b.py").write_text("def second():\n    return 2\n")
        idx = RepoIndex(tmp_path, use_cache=False)

        idx.write_sqlite(db)
    finally:
        reader.close()

    con = sqlite3.connect(db)
    try:
        paths = {row[0] for row in con.execute("select path from files")}
    finally:
        con.close()
    assert {"a.py", "b.py"} <= paths
