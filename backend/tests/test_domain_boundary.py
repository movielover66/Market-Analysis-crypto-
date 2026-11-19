"""
CI Import-Boundary Test (Rule 1 of Section 7 & Section 8):
`backend/app/domain/` must NEVER import from `api`, `repositories`, `providers`, `services`, `ai`, `fastapi`, `httpx`, or `supabase`.
"""
import ast
import unittest
from pathlib import Path

FORBIDDEN_PREFIXES = (
    "app.api",
    "app.repositories",
    "app.providers",
    "app.services",
    "app.ai",
    "fastapi",
    "starlette",
    "httpx",
    "supabase",
    "yfinance",
    "SmartApi",
)


class TestDomainImportBoundary(unittest.TestCase):
    def test_domain_has_zero_forbidden_imports(self) -> None:
        domain_dir = Path(__file__).resolve().parents[1] / "app" / "domain"
        self.assertTrue(domain_dir.exists(), "app/domain directory must exist")

        py_files = list(domain_dir.rglob("*.py"))
        self.assertGreaterEqual(len(py_files), 1, "domain package must contain __init__.py files")

        violations = []
        for path in py_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(FORBIDDEN_PREFIXES):
                            violations.append(f"{path.name}:{node.lineno} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod.startswith(FORBIDDEN_PREFIXES) or (node.level >= 2 and mod.startswith(("api", "repositories", "providers", "services", "ai"))):
                        violations.append(f"{path.name}:{node.lineno} from {mod} import ...")

        self.assertEqual(violations, [], f"Domain import boundary violated: {violations}")


if __name__ == "__main__":
    unittest.main()
