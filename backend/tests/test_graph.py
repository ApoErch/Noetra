from indexer.graph import resolve_js_import

# A TypeScript source tree: everything is .ts on disk, and nothing is .js.
TS_TREE = {
    "src/core/parse.ts",
    "src/core/util.ts",
    "src/core/errors.ts",
    "src/core/nested/index.ts",
    "src/legacy/helper.js",
}


def test_resolve_js_import_strips_a_typescript_output_extension() -> None:
    """`import "./util.js"` from a .ts file resolves to util.ts — TypeScript NodeNext imports the emitted path."""
    assert resolve_js_import("src/core/parse.ts", "./util.js", TS_TREE) == "src/core/util.ts"


def test_resolve_js_import_still_resolves_an_extensionless_specifier() -> None:
    """The ordinary bundler-style specifier keeps working."""
    assert resolve_js_import("src/core/parse.ts", "./errors", TS_TREE) == "src/core/errors.ts"


def test_resolve_js_import_prefers_a_real_js_file_over_stripping() -> None:
    """When the .js path actually exists on disk it wins — stripping is only a fallback."""
    assert resolve_js_import("src/core/parse.ts", "../legacy/helper.js", TS_TREE) == "src/legacy/helper.js"


def test_resolve_js_import_strips_before_falling_back_to_a_directory_index() -> None:
    """A stripped stem also gets the index-file treatment."""
    assert resolve_js_import("src/core/parse.ts", "./nested/index.js", TS_TREE) == "src/core/nested/index.ts"
    assert resolve_js_import("src/core/parse.ts", "./nested", TS_TREE) == "src/core/nested/index.ts"


def test_resolve_js_import_ignores_bare_specifiers() -> None:
    """A package name is not in this repo, so it resolves to nothing."""
    assert resolve_js_import("src/core/parse.ts", "react", TS_TREE) is None
    assert resolve_js_import("src/core/parse.ts", "@zod/core", TS_TREE) is None
