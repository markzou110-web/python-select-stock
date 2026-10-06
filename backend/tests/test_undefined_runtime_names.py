"""Prevent missing imports/context globals without importing application services."""
import builtins
from pathlib import Path
import symtable


def test_backend_production_modules_have_no_undefined_global_references():
    backend = Path(__file__).resolve().parents[1]
    failures = []
    for folder in (backend / "core", backend / "routers"):
        for path in sorted(folder.glob("*.py")):
            table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
            known = set(table.get_identifiers()) | set(dir(builtins)) | {
                "__file__", "__name__", "__package__", "__doc__", "__spec__",
                "__loader__", "__builtins__", "__class__",
            }
            # Only assigned/imported module names exist at runtime; a reference alone
            # does not define a global (e.g. a dropped import after extraction).
            known -= {
                symbol.get_name() for symbol in table.get_symbols()
                if not symbol.is_assigned() and not symbol.is_imported()
                and symbol.get_name() not in dir(builtins)
                and not symbol.get_name().startswith("__")
            }
            pending = [table]
            while pending:
                scope = pending.pop()
                for symbol in scope.get_symbols():
                    if symbol.is_referenced() and symbol.is_global() and symbol.get_name() not in known:
                        failures.append(f"{path.relative_to(backend)}:{scope.get_lineno()} {symbol.get_name()}")
                pending.extend(scope.get_children())
    assert not failures, "Undefined runtime names:\n" + "\n".join(failures)
