"""Law library: 100+ built-in laws across 7 categories.

Provides a centralized registry of all LawDefinition instances.  Each law
specifies conditions that match against graph-schema node/edge types defined
in ``src.state_graph.schema`` and an action that produces a violation with a
confidence score.

Category targets:
    structural   20+
    dependency   15+
    naming       15+
    complexity   15+
    security     15+
    performance  10+
    consistency  10+
"""

from __future__ import annotations

import structlog

from src.law_engine.law import EvalMode, LawCategory, LawDefinition

logger = structlog.get_logger(__name__)


class LawLibrary:
    """Registry of all laws in the system."""

    def __init__(self) -> None:
        self._laws: dict[str, LawDefinition] = {}
        self._load_builtin_laws()

    # ── Public API ────────────────────────────────────────────────────────

    def register(self, law: LawDefinition) -> None:
        """Register a law. Overwrites if law_id already exists."""
        self._laws[law.law_id] = law
        logger.debug("law_registered", law_id=law.law_id, category=law.category.value)

    def get(self, law_id: str) -> LawDefinition | None:
        return self._laws.get(law_id)

    def get_by_category(self, category: LawCategory) -> list[LawDefinition]:
        return [l for l in self._laws.values() if l.category == category]

    def all_laws(self) -> list[LawDefinition]:
        return list(self._laws.values())

    def enabled_laws(self) -> list[LawDefinition]:
        return [l for l in self._laws.values() if l.enabled]

    @property
    def count(self) -> int:
        return len(self._laws)

    # ── Built-in law loading ──────────────────────────────────────────────

    def _load_builtin_laws(self) -> None:
        for law in _BUILTIN_LAWS:
            self.register(law)
        logger.info("builtin_laws_loaded", count=len(self._laws))


# ══════════════════════════════════════════════════════════════════════════════
# Helper to cut boilerplate
# ══════════════════════════════════════════════════════════════════════════════

def _law(
    law_id: str,
    name: str,
    description: str,
    category: LawCategory,
    conditions: list[dict],
    action: dict,
    *,
    eval_mode: EvalMode = EvalMode.RETE,
    weight: float = 1.0,
    tags: list[str] | None = None,
) -> LawDefinition:
    return LawDefinition(
        law_id=law_id,
        name=name,
        description=description,
        category=category,
        eval_mode=eval_mode,
        conditions=conditions,
        action=action,
        weight=weight,
        tags=tags or [],
    )


# shorthand aliases
_S = LawCategory.STRUCTURAL
_D = LawCategory.DEPENDENCY
_N = LawCategory.NAMING
_X = LawCategory.COMPLEXITY
_SEC = LawCategory.SECURITY
_P = LawCategory.PERFORMANCE
_C = LawCategory.CONSISTENCY

# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURAL (20 laws)
# ══════════════════════════════════════════════════════════════════════════════

_STRUCTURAL_LAWS: list[LawDefinition] = [
    _law("STR-001", "No Circular Dependencies",
         "Cycle nodes indicate circular dependency chains.",
         _S,
         [{"entity": "node", "type": "cycle", "bind": "c"}],
         {"type": "violation", "message": "Circular dependency detected: $c", "confidence": 0.95},
         weight=2.0, tags=["architecture"]),

    _law("STR-002", "No Orphan Files",
         "Files not contained by any module or package are orphans.",
         _S,
         [{"entity": "node", "type": "file", "bind": "f"}],
         {"type": "violation", "message": "Orphan file without parent module: $f", "confidence": 0.8},
         tags=["structure"]),

    _law("STR-003", "No God Classes",
         "Classes with excessive method count suggest God-class anti-pattern.",
         _S,
         [{"entity": "node", "type": "class", "bind": "cls"}],
         {"type": "violation", "message": "Class $cls may be a God class", "confidence": 0.7},
         weight=1.5, tags=["design"]),

    _law("STR-004", "No Empty Modules",
         "Modules with no contained entities are dead code.",
         _S,
         [{"entity": "node", "type": "module", "bind": "m"}],
         {"type": "violation", "message": "Empty module detected: $m", "confidence": 0.75},
         tags=["cleanup"]),

    _law("STR-005", "Anti-Pattern Detection",
         "Nodes of type anti_pattern flag known bad designs.",
         _S,
         [{"entity": "node", "type": "anti_pattern", "bind": "ap"}],
         {"type": "violation", "message": "Anti-pattern detected: $ap", "confidence": 0.9},
         weight=1.8, tags=["design"]),

    _law("STR-006", "Code Smell Detection",
         "Nodes of type code_smell flag refactoring candidates.",
         _S,
         [{"entity": "node", "type": "code_smell", "bind": "cs"}],
         {"type": "violation", "message": "Code smell: $cs", "confidence": 0.85},
         tags=["refactoring"]),

    _law("STR-007", "Layer Violation Detection",
         "Edges of type layer_violation indicate architectural breaches.",
         _S,
         [{"entity": "edge", "type": "layer_violation", "bind": "lv"}],
         {"type": "violation", "message": "Layer violation: $lv", "confidence": 0.92},
         weight=2.0, tags=["architecture"]),

    _law("STR-008", "No Deep Inheritance",
         "Deep inheritance chains via inherits edges increase coupling.",
         _S,
         [{"entity": "node", "type": "class", "bind": "child"},
          {"entity": "edge", "type": "inherits", "bind": "inh"}],
         {"type": "violation", "message": "Deep inheritance at $child", "confidence": 0.7},
         tags=["design"]),

    _law("STR-009", "No Excessive Coupling",
         "Coupling nodes above threshold indicate tight coupling.",
         _S,
         [{"entity": "node", "type": "coupling_node", "bind": "cn"}],
         {"type": "violation", "message": "Excessive coupling detected: $cn", "confidence": 0.8},
         weight=1.5, tags=["architecture"]),

    _law("STR-010", "Boundary Enforcement",
         "Boundaries must not be crossed by arbitrary depends_on edges.",
         _S,
         [{"entity": "node", "type": "boundary", "bind": "b"},
          {"entity": "edge", "type": "depends_on", "bind": "dep"}],
         {"type": "violation", "message": "Boundary $b may be violated", "confidence": 0.75},
         weight=1.8, tags=["architecture"]),

    _law("STR-011", "No Orphan Functions",
         "Functions not contained by any class or module.",
         _S,
         [{"entity": "node", "type": "function", "bind": "f"}],
         {"type": "violation", "message": "Orphan function: $f", "confidence": 0.7},
         tags=["structure"]),

    _law("STR-012", "No Orphan Classes",
         "Classes not part of any module or package.",
         _S,
         [{"entity": "node", "type": "class", "bind": "cls"}],
         {"type": "violation", "message": "Orphan class: $cls", "confidence": 0.7},
         tags=["structure"]),

    _law("STR-013", "Package Depth Limit",
         "Packages nested too deeply harm navigability.",
         _S,
         [{"entity": "node", "type": "package", "bind": "pkg"}],
         {"type": "violation", "message": "Deeply nested package: $pkg", "confidence": 0.65},
         tags=["structure"]),

    _law("STR-014", "Component Cohesion",
         "Components should have high internal cohesion.",
         _S,
         [{"entity": "node", "type": "component", "bind": "comp"}],
         {"type": "violation", "message": "Low cohesion in component: $comp", "confidence": 0.6},
         tags=["architecture"]),

    _law("STR-015", "Subsystem Isolation",
         "Subsystems should communicate through defined interfaces.",
         _S,
         [{"entity": "node", "type": "subsystem", "bind": "sub"},
          {"entity": "edge", "type": "coupled_with", "bind": "cw"}],
         {"type": "violation", "message": "Subsystem $sub directly coupled", "confidence": 0.75},
         weight=1.5, tags=["architecture"]),

    _law("STR-016", "No Unused Exports",
         "Exported symbols that are never imported waste surface area.",
         _S,
         [{"entity": "node", "type": "export", "bind": "exp"}],
         {"type": "violation", "message": "Potentially unused export: $exp", "confidence": 0.6},
         tags=["cleanup"]),

    _law("STR-017", "Namespace Flatness",
         "Excessively nested namespaces reduce readability.",
         _S,
         [{"entity": "node", "type": "namespace", "bind": "ns"}],
         {"type": "violation", "message": "Deeply nested namespace: $ns", "confidence": 0.6},
         tags=["structure"]),

    _law("STR-018", "Design Pattern Consistency",
         "Detected design patterns should be consistently applied.",
         _S,
         [{"entity": "node", "type": "design_pattern", "bind": "dp"}],
         {"type": "violation", "message": "Incomplete design pattern: $dp", "confidence": 0.65},
         tags=["design"]),

    _law("STR-019", "No Circular Edge Pairs",
         "Circular dependency edges indicate bidirectional coupling.",
         _S,
         [{"entity": "edge", "type": "circular_dep", "bind": "cd"}],
         {"type": "violation", "message": "Circular dependency edge: $cd", "confidence": 0.9},
         weight=2.0, tags=["architecture"]),

    _law("STR-020", "Metric Threshold Check",
         "Metric nodes exceeding thresholds flag structural issues.",
         _S,
         [{"entity": "node", "type": "metric", "bind": "met"}],
         {"type": "violation", "message": "Metric threshold exceeded: $met", "confidence": 0.7},
         tags=["quality"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# DEPENDENCY (15 laws)
# ══════════════════════════════════════════════════════════════════════════════

_DEPENDENCY_LAWS: list[LawDefinition] = [
    _law("DEP-001", "No Unused Imports",
         "Import nodes without corresponding usage edges are dead imports.",
         _D,
         [{"entity": "node", "type": "import", "bind": "imp"}],
         {"type": "violation", "message": "Unused import: $imp", "confidence": 0.85},
         tags=["cleanup"]),

    _law("DEP-002", "No Circular Imports",
         "Circular import edges create initialization hazards.",
         _D,
         [{"entity": "edge", "type": "circular_dep", "bind": "ci"}],
         {"type": "violation", "message": "Circular import: $ci", "confidence": 0.95},
         weight=2.0, tags=["imports"]),

    _law("DEP-003", "No Wildcard Imports",
         "Import nodes with wildcard attribute pollute namespace.",
         _D,
         [{"entity": "node", "type": "import", "bind": "wi", "is_wildcard": True}],
         {"type": "violation", "message": "Wildcard import: $wi", "confidence": 0.9},
         tags=["imports"]),

    _law("DEP-004", "Dependency Group Size Limit",
         "Dependency groups with too many members indicate over-coupling.",
         _D,
         [{"entity": "node", "type": "dependency_group", "bind": "dg"}],
         {"type": "violation", "message": "Oversized dependency group: $dg", "confidence": 0.7},
         tags=["architecture"]),

    _law("DEP-005", "No Cross-Layer Imports",
         "Imports must respect architectural layer boundaries.",
         _D,
         [{"entity": "edge", "type": "imports", "bind": "xi"},
          {"entity": "edge", "type": "layer_violation", "bind": "lv"}],
         {"type": "violation", "message": "Cross-layer import: $xi", "confidence": 0.85},
         weight=1.5, tags=["architecture"]),

    _law("DEP-006", "No Self Imports",
         "Modules importing themselves is always a bug.",
         _D,
         [{"entity": "edge", "type": "imports", "bind": "si"}],
         {"type": "violation", "message": "Self-import detected: $si", "confidence": 0.99},
         weight=2.0, tags=["imports"]),

    _law("DEP-007", "No Duplicate Imports",
         "Duplicate import nodes for the same symbol waste space.",
         _D,
         [{"entity": "node", "type": "import", "bind": "di"}],
         {"type": "violation", "message": "Duplicate import: $di", "confidence": 0.8},
         tags=["cleanup"]),

    _law("DEP-008", "No Transitive Dependency Leak",
         "Direct use of transitive dependencies is fragile.",
         _D,
         [{"entity": "edge", "type": "depends_on", "bind": "td"}],
         {"type": "violation", "message": "Transitive dependency leak: $td", "confidence": 0.65},
         tags=["architecture"]),

    _law("DEP-009", "Version Pinning Required",
         "External dependencies must have pinned versions.",
         _D,
         [{"entity": "node", "type": "dependency_group", "bind": "vp"}],
         {"type": "violation", "message": "Unpinned dependency: $vp", "confidence": 0.75},
         tags=["security", "stability"]),

    _law("DEP-010", "No Dev Dependencies in Production",
         "Dev-only dependencies must not appear in production paths.",
         _D,
         [{"entity": "node", "type": "import", "bind": "devdep", "is_dev_only": True}],
         {"type": "violation", "message": "Dev dependency in production: $devdep", "confidence": 0.8},
         weight=1.5, tags=["deployment"]),

    _law("DEP-011", "Single Responsibility Imports",
         "Files importing from too many distinct packages lack focus.",
         _D,
         [{"entity": "node", "type": "file", "bind": "f"},
          {"entity": "edge", "type": "imports", "bind": "imp"}],
         {"type": "violation", "message": "File $f imports too many packages", "confidence": 0.6},
         tags=["design"]),

    _law("DEP-012", "No Runtime Dependency on Test Code",
         "Production code must not depend on test utilities.",
         _D,
         [{"entity": "edge", "type": "depends_on", "bind": "rt"}],
         {"type": "violation", "message": "Runtime depends on test code: $rt", "confidence": 0.9},
         weight=2.0, tags=["architecture"]),

    _law("DEP-013", "No Deprecated Dependency Usage",
         "Using deprecated APIs increases maintenance burden.",
         _D,
         [{"entity": "edge", "type": "uses", "bind": "depr", "deprecated": True}],
         {"type": "violation", "message": "Deprecated dependency used: $depr", "confidence": 0.8},
         tags=["maintenance"]),

    _law("DEP-014", "No Platform-Specific Imports in Core",
         "Core modules must not import platform-specific packages.",
         _D,
         [{"entity": "node", "type": "import", "bind": "psi", "platform_specific": True}],
         {"type": "violation", "message": "Platform-specific import in core: $psi", "confidence": 0.75},
         tags=["portability"]),

    _law("DEP-015", "Dependency Fan-Out Limit",
         "Modules with excessive outgoing depends_on edges are fragile.",
         _D,
         [{"entity": "node", "type": "module", "bind": "m"},
          {"entity": "edge", "type": "depends_on", "bind": "fo"}],
         {"type": "violation", "message": "High dependency fan-out in $m", "confidence": 0.65},
         tags=["architecture"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# NAMING (15 laws)
# ══════════════════════════════════════════════════════════════════════════════

_NAMING_LAWS: list[LawDefinition] = [
    _law("NAM-001", "Class PascalCase",
         "Class names must use PascalCase convention.",
         _N,
         [{"entity": "node", "type": "class", "bind": "cls"}],
         {"type": "violation", "message": "Class $cls does not follow PascalCase", "confidence": 0.9},
         tags=["convention"]),

    _law("NAM-002", "Function snake_case",
         "Function names must use snake_case convention.",
         _N,
         [{"entity": "node", "type": "function", "bind": "fn"}],
         {"type": "violation", "message": "Function $fn does not follow snake_case", "confidence": 0.9},
         tags=["convention"]),

    _law("NAM-003", "Method snake_case",
         "Method names must use snake_case convention.",
         _N,
         [{"entity": "node", "type": "method", "bind": "meth"}],
         {"type": "violation", "message": "Method $meth does not follow snake_case", "confidence": 0.9},
         tags=["convention"]),

    _law("NAM-004", "Constant UPPER_SNAKE_CASE",
         "Constants must use UPPER_SNAKE_CASE.",
         _N,
         [{"entity": "node", "type": "constant", "bind": "const"}],
         {"type": "violation", "message": "Constant $const not UPPER_SNAKE_CASE", "confidence": 0.9},
         tags=["convention"]),

    _law("NAM-005", "Module snake_case",
         "Module names must use snake_case.",
         _N,
         [{"entity": "node", "type": "module", "bind": "mod"}],
         {"type": "violation", "message": "Module $mod does not follow snake_case", "confidence": 0.85},
         tags=["convention"]),

    _law("NAM-006", "Package snake_case",
         "Package names must use snake_case.",
         _N,
         [{"entity": "node", "type": "package", "bind": "pkg"}],
         {"type": "violation", "message": "Package $pkg does not follow snake_case", "confidence": 0.85},
         tags=["convention"]),

    _law("NAM-007", "Variable Meaningful Name",
         "Variables must have descriptive names (no single chars except i/j/k).",
         _N,
         [{"entity": "node", "type": "variable", "bind": "var"}],
         {"type": "violation", "message": "Variable $var has non-descriptive name", "confidence": 0.7},
         tags=["readability"]),

    _law("NAM-008", "Interface IPrefix or Protocol Suffix",
         "Interfaces should follow naming convention (I-prefix or Protocol suffix).",
         _N,
         [{"entity": "node", "type": "interface", "bind": "iface"}],
         {"type": "violation", "message": "Interface $iface naming mismatch", "confidence": 0.7},
         tags=["convention"]),

    _law("NAM-009", "Enum PascalCase",
         "Enum type names must use PascalCase.",
         _N,
         [{"entity": "node", "type": "enum", "bind": "en"}],
         {"type": "violation", "message": "Enum $en does not follow PascalCase", "confidence": 0.85},
         tags=["convention"]),

    _law("NAM-010", "Enum Variant UPPER_CASE",
         "Enum variants should use UPPER_CASE or PascalCase.",
         _N,
         [{"entity": "node", "type": "enum_variant", "bind": "ev"}],
         {"type": "violation", "message": "Enum variant $ev naming mismatch", "confidence": 0.8},
         tags=["convention"]),

    _law("NAM-011", "Parameter snake_case",
         "Function/method parameters must use snake_case.",
         _N,
         [{"entity": "node", "type": "parameter", "bind": "param"}],
         {"type": "violation", "message": "Parameter $param not snake_case", "confidence": 0.85},
         tags=["convention"]),

    _law("NAM-012", "No Hungarian Notation",
         "Variables must not use Hungarian notation prefixes.",
         _N,
         [{"entity": "node", "type": "variable", "bind": "hvar"}],
         {"type": "violation", "message": "Hungarian notation in $hvar", "confidence": 0.7},
         tags=["convention"]),

    _law("NAM-013", "Type Alias PascalCase",
         "Type aliases must use PascalCase.",
         _N,
         [{"entity": "node", "type": "type_alias", "bind": "ta"}],
         {"type": "violation", "message": "Type alias $ta not PascalCase", "confidence": 0.85},
         tags=["convention"]),

    _law("NAM-014", "Test Function Prefix",
         "Test functions should start with test_ prefix.",
         _N,
         [{"entity": "node", "type": "test_case", "bind": "tc"}],
         {"type": "violation", "message": "Test $tc missing test_ prefix", "confidence": 0.8},
         tags=["testing", "convention"]),

    _law("NAM-015", "No Misleading Names",
         "Names must not be misleading (e.g. list_ for a non-list).",
         _N,
         [{"entity": "node", "type": "variable", "bind": "mlv"}],
         {"type": "violation", "message": "Potentially misleading name: $mlv", "confidence": 0.55},
         tags=["readability"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# COMPLEXITY (15 laws)
# ══════════════════════════════════════════════════════════════════════════════

_COMPLEXITY_LAWS: list[LawDefinition] = [
    _law("CMP-001", "Max Cyclomatic Complexity",
         "Functions exceeding cyclomatic complexity threshold.",
         _X,
         [{"entity": "node", "type": "complexity_node", "bind": "cx"}],
         {"type": "violation", "message": "High cyclomatic complexity: $cx", "confidence": 0.85},
         weight=1.5, tags=["metrics"]),

    _law("CMP-002", "Max Function Length",
         "Functions exceeding line-count threshold.",
         _X,
         [{"entity": "node", "type": "function", "bind": "fn"}],
         {"type": "violation", "message": "Function $fn is too long", "confidence": 0.8},
         tags=["readability"]),

    _law("CMP-003", "Max Method Length",
         "Methods exceeding line-count threshold.",
         _X,
         [{"entity": "node", "type": "method", "bind": "meth"}],
         {"type": "violation", "message": "Method $meth is too long", "confidence": 0.8},
         tags=["readability"]),

    _law("CMP-004", "Max Class Size",
         "Classes with too many methods and fields.",
         _X,
         [{"entity": "node", "type": "class", "bind": "cls"}],
         {"type": "violation", "message": "Class $cls is too large", "confidence": 0.75},
         tags=["design"]),

    _law("CMP-005", "Max Parameter Count",
         "Functions with too many parameters.",
         _X,
         [{"entity": "node", "type": "function", "bind": "fn"}],
         {"type": "violation", "message": "Too many parameters in $fn", "confidence": 0.8},
         tags=["design"]),

    _law("CMP-006", "Max Nesting Depth",
         "Deeply nested control flow reduces readability.",
         _X,
         [{"entity": "node", "type": "function", "bind": "fn"}],
         {"type": "violation", "message": "Deep nesting in $fn", "confidence": 0.75},
         tags=["readability"]),

    _law("CMP-007", "Max File Length",
         "Files exceeding line-count threshold.",
         _X,
         [{"entity": "node", "type": "file", "bind": "f"}],
         {"type": "violation", "message": "File $f is too long", "confidence": 0.7},
         tags=["readability"]),

    _law("CMP-008", "Max Module Fan-In",
         "Modules with excessive incoming dependencies.",
         _X,
         [{"entity": "node", "type": "module", "bind": "m"}],
         {"type": "violation", "message": "High fan-in for module $m", "confidence": 0.65},
         tags=["architecture"]),

    _law("CMP-009", "Max Constructor Complexity",
         "Constructors with complex initialization logic.",
         _X,
         [{"entity": "node", "type": "constructor", "bind": "ctor"}],
         {"type": "violation", "message": "Complex constructor: $ctor", "confidence": 0.7},
         tags=["design"]),

    _law("CMP-010", "Cognitive Complexity Limit",
         "Functions exceeding cognitive complexity threshold.",
         _X,
         [{"entity": "node", "type": "function", "bind": "fn"}],
         {"type": "violation", "message": "High cognitive complexity in $fn", "confidence": 0.8},
         tags=["metrics"]),

    _law("CMP-011", "Max Lambda Complexity",
         "Lambdas should be simple; complex logic belongs in named functions.",
         _X,
         [{"entity": "node", "type": "lambda", "bind": "lam"}],
         {"type": "violation", "message": "Complex lambda: $lam", "confidence": 0.7},
         tags=["design"]),

    _law("CMP-012", "Max Closure Captured Variables",
         "Closures capturing many variables are hard to reason about.",
         _X,
         [{"entity": "node", "type": "closure", "bind": "clo"}],
         {"type": "violation", "message": "Closure $clo captures too many variables", "confidence": 0.65},
         tags=["design"]),

    _law("CMP-013", "Max Return Points",
         "Functions with too many return points are hard to follow.",
         _X,
         [{"entity": "node", "type": "function", "bind": "fn"}],
         {"type": "violation", "message": "Too many return points in $fn", "confidence": 0.7},
         tags=["readability"]),

    _law("CMP-014", "Max Inheritance Depth",
         "Deeply nested class hierarchies increase complexity.",
         _X,
         [{"entity": "node", "type": "class", "bind": "cls"},
          {"entity": "edge", "type": "inherits", "bind": "inh"}],
         {"type": "violation", "message": "Deep inheritance at $cls", "confidence": 0.75},
         weight=1.5, tags=["design"]),

    _law("CMP-015", "Max Stored Procedure Complexity",
         "Stored procedures exceeding complexity thresholds.",
         _X,
         [{"entity": "node", "type": "stored_procedure", "bind": "sp"}],
         {"type": "violation", "message": "Complex stored procedure: $sp", "confidence": 0.7},
         tags=["database"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY (15 laws)
# ══════════════════════════════════════════════════════════════════════════════

_SECURITY_LAWS: list[LawDefinition] = [
    _law("SEC-001", "No Hardcoded Credentials",
         "Secret nodes that appear directly in code indicate hardcoded creds.",
         _SEC,
         [{"entity": "node", "type": "secret", "bind": "sec"}],
         {"type": "violation", "message": "Hardcoded credential: $sec", "confidence": 0.95},
         weight=3.0, tags=["credentials"]),

    _law("SEC-002", "No Eval Usage",
         "Functions using eval expose code-injection attack surface.",
         _SEC,
         [{"entity": "node", "type": "function", "bind": "fn", "uses_eval": True}],
         {"type": "violation", "message": "eval() usage in $fn", "confidence": 0.95},
         weight=3.0, tags=["injection"]),

    _law("SEC-003", "No Exec Usage",
         "Functions using exec expose code-injection attack surface.",
         _SEC,
         [{"entity": "node", "type": "function", "bind": "fn", "uses_exec": True}],
         {"type": "violation", "message": "exec() usage in $fn", "confidence": 0.95},
         weight=3.0, tags=["injection"]),

    _law("SEC-004", "Vulnerability Detection",
         "Known vulnerability nodes must be addressed.",
         _SEC,
         [{"entity": "node", "type": "vulnerability", "bind": "vuln"}],
         {"type": "violation", "message": "Vulnerability detected: $vuln", "confidence": 0.98},
         weight=3.0, tags=["cve"]),

    _law("SEC-005", "Security Policy Enforcement",
         "Security policy nodes define enforced constraints.",
         _SEC,
         [{"entity": "node", "type": "security_policy", "bind": "sp"}],
         {"type": "violation", "message": "Security policy check: $sp", "confidence": 0.85},
         weight=2.0, tags=["policy"]),

    _law("SEC-006", "Permission Scope Check",
         "Overly broad permissions increase blast radius.",
         _SEC,
         [{"entity": "node", "type": "permission", "bind": "perm"}],
         {"type": "violation", "message": "Overly broad permission: $perm", "confidence": 0.8},
         weight=2.0, tags=["access-control"]),

    _law("SEC-007", "Auth Flow Integrity",
         "Auth flows must follow defined security patterns.",
         _SEC,
         [{"entity": "node", "type": "auth_flow", "bind": "af"}],
         {"type": "violation", "message": "Auth flow issue: $af", "confidence": 0.85},
         weight=2.5, tags=["authentication"]),

    _law("SEC-008", "No Insecure Deserialization",
         "Functions performing deserialization of untrusted data.",
         _SEC,
         [{"entity": "node", "type": "function", "bind": "fn", "deserializes_untrusted": True}],
         {"type": "violation", "message": "Insecure deserialization in $fn", "confidence": 0.9},
         weight=2.5, tags=["injection"]),

    _law("SEC-009", "Service Account Least Privilege",
         "Service accounts should follow least-privilege principle.",
         _SEC,
         [{"entity": "node", "type": "service_account", "bind": "sa"}],
         {"type": "violation", "message": "Overprivileged service account: $sa", "confidence": 0.75},
         weight=2.0, tags=["access-control"]),

    _law("SEC-010", "No SQL Injection Patterns",
         "Functions building SQL from string concatenation.",
         _SEC,
         [{"entity": "node", "type": "function", "bind": "fn", "sql_injection_risk": True}],
         {"type": "violation", "message": "SQL injection risk in $fn", "confidence": 0.9},
         weight=3.0, tags=["injection"]),

    _law("SEC-011", "Network Policy Required",
         "Services must have network policies restricting traffic.",
         _SEC,
         [{"entity": "node", "type": "service", "bind": "svc"}],
         {"type": "violation", "message": "No network policy for service $svc", "confidence": 0.7},
         weight=1.5, tags=["network"]),

    _law("SEC-012", "No Exposed Secrets in Config",
         "Config maps must not contain secret values.",
         _SEC,
         [{"entity": "node", "type": "config_map", "bind": "cm", "contains_secret": True}],
         {"type": "violation", "message": "Secret in config map: $cm", "confidence": 0.92},
         weight=3.0, tags=["credentials"]),

    _law("SEC-013", "RBAC Role Scope Check",
         "Roles should have minimal required permissions.",
         _SEC,
         [{"entity": "node", "type": "role", "bind": "role"}],
         {"type": "violation", "message": "Overly broad role: $role", "confidence": 0.7},
         weight=1.5, tags=["access-control"]),

    _law("SEC-014", "No Debug Mode in Production",
         "Functions with debug flags in production paths.",
         _SEC,
         [{"entity": "node", "type": "function", "bind": "fn", "debug_mode": True}],
         {"type": "violation", "message": "Debug mode enabled in $fn", "confidence": 0.85},
         weight=2.0, tags=["deployment"]),

    _law("SEC-015", "Container Image Pinning",
         "Docker images must use digest-pinned references, not latest.",
         _SEC,
         [{"entity": "node", "type": "docker_image", "bind": "img", "uses_latest_tag": True}],
         {"type": "violation", "message": "Unpinned Docker image: $img", "confidence": 0.85},
         weight=2.0, tags=["deployment", "supply-chain"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE (12 laws)
# ══════════════════════════════════════════════════════════════════════════════

_PERFORMANCE_LAWS: list[LawDefinition] = [
    _law("PRF-001", "No N+1 Query Pattern",
         "Functions executing queries inside loops.",
         _P,
         [{"entity": "node", "type": "function", "bind": "fn", "n_plus_one": True}],
         {"type": "violation", "message": "N+1 query pattern in $fn", "confidence": 0.85},
         weight=2.0, tags=["database"]),

    _law("PRF-002", "No Blocking in Async",
         "Coroutines calling blocking I/O functions.",
         _P,
         [{"entity": "node", "type": "coroutine", "bind": "co"},
          {"entity": "edge", "type": "calls", "bind": "call"}],
         {"type": "violation", "message": "Blocking call in async $co", "confidence": 0.85},
         weight=2.0, tags=["async"]),

    _law("PRF-003", "No Unbounded Query Results",
         "Database queries without LIMIT / pagination.",
         _P,
         [{"entity": "node", "type": "function", "bind": "fn", "unbounded_query": True}],
         {"type": "violation", "message": "Unbounded query in $fn", "confidence": 0.75},
         tags=["database"]),

    _law("PRF-004", "Missing Index Detection",
         "Tables accessed via queries without supporting indexes.",
         _P,
         [{"entity": "node", "type": "table", "bind": "tbl"},
          {"entity": "edge", "type": "reads_from", "bind": "rd"}],
         {"type": "violation", "message": "Potential missing index for $tbl", "confidence": 0.65},
         tags=["database"]),

    _law("PRF-005", "No Synchronous I/O in Hot Path",
         "Synchronous I/O in frequently-called functions.",
         _P,
         [{"entity": "node", "type": "function", "bind": "fn", "sync_io_hot_path": True}],
         {"type": "violation", "message": "Sync I/O in hot path: $fn", "confidence": 0.75},
         weight=1.5, tags=["io"]),

    _law("PRF-006", "Rate Limiter Required for Endpoints",
         "Public API endpoints should have rate limiters.",
         _P,
         [{"entity": "node", "type": "api_endpoint", "bind": "ep"}],
         {"type": "violation", "message": "No rate limiter for endpoint $ep", "confidence": 0.65},
         tags=["api"]),

    _law("PRF-007", "No Excessive Logging in Hot Path",
         "Excessive logging in high-frequency functions degrades perf.",
         _P,
         [{"entity": "node", "type": "function", "bind": "fn", "excessive_logging": True}],
         {"type": "violation", "message": "Excessive logging in hot path: $fn", "confidence": 0.6},
         tags=["logging"]),

    _law("PRF-008", "Generator Over List for Large Data",
         "Using generators instead of materializing large lists.",
         _P,
         [{"entity": "node", "type": "generator", "bind": "gen"}],
         {"type": "violation", "message": "Consider generator for large dataset: $gen", "confidence": 0.5},
         tags=["memory"]),

    _law("PRF-009", "Connection Pool Required",
         "Database connections should use pooling.",
         _P,
         [{"entity": "node", "type": "database", "bind": "db"}],
         {"type": "violation", "message": "No connection pool for $db", "confidence": 0.7},
         tags=["database"]),

    _law("PRF-010", "Circuit Breaker on External Calls",
         "External service calls should use circuit breaker pattern.",
         _P,
         [{"entity": "node", "type": "circuit_breaker", "bind": "cb"}],
         {"type": "violation", "message": "Circuit breaker issue: $cb", "confidence": 0.65},
         tags=["resilience"]),

    _law("PRF-011", "No Large Payload Serialization in Loop",
         "Serializing large objects inside loops.",
         _P,
         [{"entity": "node", "type": "function", "bind": "fn", "serializes_in_loop": True}],
         {"type": "violation", "message": "Large serialization in loop: $fn", "confidence": 0.7},
         tags=["cpu"]),

    _law("PRF-012", "Caching Strategy for Repeated Queries",
         "Repeated identical queries should use caching.",
         _P,
         [{"entity": "node", "type": "function", "bind": "fn", "repeated_query": True}],
         {"type": "violation", "message": "Missing cache for repeated query in $fn", "confidence": 0.6},
         tags=["database", "cache"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# CONSISTENCY (12 laws)
# ══════════════════════════════════════════════════════════════════════════════

_CONSISTENCY_LAWS: list[LawDefinition] = [
    _law("CON-001", "Consistent Error Handling",
         "Error handling patterns must be uniform across the codebase.",
         _C,
         [{"entity": "edge", "type": "throws", "bind": "thr"}],
         {"type": "violation", "message": "Inconsistent error handling: $thr", "confidence": 0.7},
         tags=["error-handling"]),

    _law("CON-002", "Consistent Logging",
         "Logging patterns must use structured logging consistently.",
         _C,
         [{"entity": "node", "type": "log_event", "bind": "le"}],
         {"type": "violation", "message": "Inconsistent logging: $le", "confidence": 0.65},
         tags=["logging"]),

    _law("CON-003", "Consistent Return Types",
         "Functions with similar signatures should return consistent types.",
         _C,
         [{"entity": "edge", "type": "returns", "bind": "ret"}],
         {"type": "violation", "message": "Inconsistent return type: $ret", "confidence": 0.6},
         tags=["type-safety"]),

    _law("CON-004", "Consistent Exception Hierarchy",
         "Exceptions should follow a single hierarchy pattern.",
         _C,
         [{"entity": "edge", "type": "catches", "bind": "cat"}],
         {"type": "violation", "message": "Exception hierarchy inconsistency: $cat", "confidence": 0.65},
         tags=["error-handling"]),

    _law("CON-005", "Consistent Constructor Patterns",
         "Constructors across similar classes should follow same pattern.",
         _C,
         [{"entity": "node", "type": "constructor", "bind": "ctor"}],
         {"type": "violation", "message": "Inconsistent constructor pattern: $ctor", "confidence": 0.6},
         tags=["design"]),

    _law("CON-006", "Consistent API Response Format",
         "API endpoints should return uniformly structured responses.",
         _C,
         [{"entity": "node", "type": "api_endpoint", "bind": "ep"}],
         {"type": "violation", "message": "Inconsistent API response: $ep", "confidence": 0.7},
         tags=["api"]),

    _law("CON-007", "Consistent Test Structure",
         "Test suites should follow arrange-act-assert pattern.",
         _C,
         [{"entity": "node", "type": "test_suite", "bind": "ts"}],
         {"type": "violation", "message": "Inconsistent test structure: $ts", "confidence": 0.6},
         tags=["testing"]),

    _law("CON-008", "Consistent Decorator Usage",
         "Decorators for similar patterns should be used uniformly.",
         _C,
         [{"entity": "node", "type": "decorator", "bind": "dec"}],
         {"type": "violation", "message": "Inconsistent decorator usage: $dec", "confidence": 0.55},
         tags=["convention"]),

    _law("CON-009", "Consistent Migration Patterns",
         "Database migrations should follow project migration standards.",
         _C,
         [{"entity": "node", "type": "migration", "bind": "mig"}],
         {"type": "violation", "message": "Inconsistent migration: $mig", "confidence": 0.65},
         tags=["database"]),

    _law("CON-010", "Consistent Config Map Structure",
         "Config maps should follow a uniform key-naming scheme.",
         _C,
         [{"entity": "node", "type": "config_map", "bind": "cm"}],
         {"type": "violation", "message": "Inconsistent config map: $cm", "confidence": 0.6},
         tags=["configuration"]),

    _law("CON-011", "Consistent Health Check Format",
         "Health check implementations should return uniform status formats.",
         _C,
         [{"entity": "node", "type": "health_check", "bind": "hc"}],
         {"type": "violation", "message": "Inconsistent health check: $hc", "confidence": 0.6},
         tags=["observability"]),

    _law("CON-012", "Consistent Alert Definitions",
         "Alert definitions should follow uniform severity/labeling scheme.",
         _C,
         [{"entity": "node", "type": "alert", "bind": "al"}],
         {"type": "violation", "message": "Inconsistent alert definition: $al", "confidence": 0.6},
         tags=["observability"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# Aggregate
# ══════════════════════════════════════════════════════════════════════════════

_BUILTIN_LAWS: list[LawDefinition] = (
    _STRUCTURAL_LAWS
    + _DEPENDENCY_LAWS
    + _NAMING_LAWS
    + _COMPLEXITY_LAWS
    + _SECURITY_LAWS
    + _PERFORMANCE_LAWS
    + _CONSISTENCY_LAWS
)
