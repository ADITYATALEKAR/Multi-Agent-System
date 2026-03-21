"""State Graph Schema: 88+ node types, 35+ edge types, SchemaRegistry.

Defines the canonical graph schema for the Blueprint system world model.
All node and edge types used across all three graph tiers.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field


# ── Node Types (88+) ────────────────────────────────────────────────────────


class NodeCategory(str, enum.Enum):
    CODE = "code"
    STRUCTURE = "structure"
    INFRASTRUCTURE = "infrastructure"
    DATA = "data"
    RUNTIME = "runtime"
    CONFIGURATION = "configuration"
    DOCUMENTATION = "documentation"
    SECURITY = "security"
    TESTING = "testing"
    DEPLOYMENT = "deployment"


class NodeType(str, enum.Enum):
    # ── Code (Tier 1) — 28 types ─────────────────────────────────────────
    MODULE = "module"
    PACKAGE = "package"
    FILE = "file"
    CLASS = "class"
    INTERFACE = "interface"
    TRAIT = "trait"
    STRUCT = "struct"
    ENUM = "enum"
    ENUM_VARIANT = "enum_variant"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    DESTRUCTOR = "destructor"
    PROPERTY = "property"
    FIELD = "field"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"
    GENERIC_PARAM = "generic_param"
    DECORATOR = "decorator"
    ANNOTATION = "annotation"
    LAMBDA = "lambda"
    CLOSURE = "closure"
    COROUTINE = "coroutine"
    GENERATOR = "generator"
    IMPORT = "import"
    EXPORT = "export"

    # ── Structural (Tier 2) — 14 types ───────────────────────────────────
    NAMESPACE = "namespace"
    LAYER = "layer"
    COMPONENT = "component"
    SUBSYSTEM = "subsystem"
    BOUNDARY = "boundary"
    DEPENDENCY_GROUP = "dependency_group"
    CYCLE = "cycle"
    PATTERN = "pattern"
    DESIGN_PATTERN = "design_pattern"
    ANTI_PATTERN = "anti_pattern"
    CODE_SMELL = "code_smell"
    METRIC = "metric"
    COMPLEXITY_NODE = "complexity_node"
    COUPLING_NODE = "coupling_node"

    # ── Infrastructure (Tier 3) — 30 types ───────────────────────────────
    SERVICE = "service"
    MICROSERVICE = "microservice"
    CONTAINER = "container"
    POD = "pod"
    DEPLOYMENT = "deployment"
    REPLICA_SET = "replica_set"
    STATEFUL_SET = "stateful_set"
    DAEMON_SET = "daemon_set"
    JOB = "job"
    CRON_JOB = "cron_job"
    CONFIG_MAP = "config_map"
    SECRET = "secret"
    VOLUME = "volume"
    PERSISTENT_VOLUME_CLAIM = "persistent_volume_claim"
    INGRESS = "ingress"
    NETWORK_POLICY = "network_policy"
    SERVICE_ACCOUNT = "service_account"
    ROLE = "role"
    TERRAFORM_RESOURCE = "terraform_resource"
    TERRAFORM_MODULE = "terraform_module"
    ANSIBLE_PLAYBOOK = "ansible_playbook"
    ANSIBLE_ROLE = "ansible_role"
    ANSIBLE_TASK = "ansible_task"
    CI_PIPELINE = "ci_pipeline"
    CI_STAGE = "ci_stage"
    CI_JOB = "ci_job"
    DOCKER_IMAGE = "docker_image"
    DOCKER_LAYER = "docker_layer"
    HELM_CHART = "helm_chart"
    HELM_VALUE = "helm_value"

    # ── Data / API (Tier 4) — 20 types ───────────────────────────────────
    DATABASE = "database"
    TABLE = "table"
    COLUMN = "column"
    INDEX = "index"
    VIEW = "view"
    STORED_PROCEDURE = "stored_procedure"
    TRIGGER = "trigger"
    MIGRATION = "migration"
    API_ENDPOINT = "api_endpoint"
    API_ROUTE = "api_route"
    GRAPHQL_TYPE = "graphql_type"
    GRAPHQL_FIELD = "graphql_field"
    GRAPHQL_QUERY = "graphql_query"
    GRAPHQL_MUTATION = "graphql_mutation"
    PROTOBUF_MESSAGE = "protobuf_message"
    PROTOBUF_FIELD = "protobuf_field"
    PROTOBUF_SERVICE = "protobuf_service"
    PROTOBUF_RPC = "protobuf_rpc"
    OPENAPI_SCHEMA = "openapi_schema"
    OPENAPI_PATH = "openapi_path"

    # ── Runtime (Tier 5) — 13 types ──────────────────────────────────────
    PROCESS = "process"
    THREAD = "thread"
    REQUEST = "request"
    SPAN = "span"
    LOG_EVENT = "log_event"
    ERROR_EVENT = "error_event"
    METRIC_SERIES = "metric_series"
    ALERT = "alert"
    INCIDENT = "incident"
    TRACE = "trace"
    CIRCUIT_BREAKER = "circuit_breaker"
    RATE_LIMITER = "rate_limiter"
    HEALTH_CHECK = "health_check"

    # ── Testing — 4 types ────────────────────────────────────────────────
    TEST_SUITE = "test_suite"
    TEST_CASE = "test_case"
    TEST_FIXTURE = "test_fixture"
    MOCK = "mock"

    # ── Documentation — 3 types ──────────────────────────────────────────
    DOC_PAGE = "doc_page"
    COMMENT = "comment"
    TODO_ITEM = "todo_item"

    # ── Security — 4 types ───────────────────────────────────────────────
    VULNERABILITY = "vulnerability"
    SECURITY_POLICY = "security_policy"
    PERMISSION = "permission"
    AUTH_FLOW = "auth_flow"


# ── Edge Types (44) ─────────────────────────────────────────────────────────


class EdgeType(str, enum.Enum):
    # ── Code relationships ───────────────────────────────────────────────
    IMPORTS = "imports"
    EXPORTS = "exports"
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    OVERRIDES = "overrides"
    USES = "uses"
    DEFINES = "defines"
    DECLARES = "declares"
    CONTAINS = "contains"
    REFERENCES = "references"
    DEPENDS_ON = "depends_on"
    INSTANTIATES = "instantiates"
    RETURNS = "returns"
    THROWS = "throws"
    CATCHES = "catches"
    DECORATES = "decorates"
    ANNOTATES = "annotates"
    TYPE_OF = "type_of"

    # ── Structural relationships ─────────────────────────────────────────
    BELONGS_TO = "belongs_to"
    PART_OF = "part_of"
    COUPLED_WITH = "coupled_with"
    CIRCULAR_DEP = "circular_dep"
    LAYER_VIOLATION = "layer_violation"

    # ── Infrastructure relationships ─────────────────────────────────────
    DEPLOYS = "deploys"
    ROUTES_TO = "routes_to"
    MOUNTS = "mounts"
    EXPOSES = "exposes"
    CONNECTS_TO = "connects_to"
    MANAGES = "manages"
    PROVISIONS = "provisions"
    BUILDS = "builds"

    # ── Data relationships ───────────────────────────────────────────────
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    MIGRATES = "migrates"
    FOREIGN_KEY = "foreign_key"
    SERVES = "serves"

    # ── Runtime / Causal ─────────────────────────────────────────────────
    TRIGGERS = "triggers"
    CAUSED_BY = "caused_by"
    PROPAGATES_TO = "propagates_to"
    MONITORS = "monitors"
    ALERTS_ON = "alerts_on"
    TESTS = "tests"

    # ── Temporal (OSG) ───────────────────────────────────────────────────
    TEMPORAL_BEFORE = "temporal_before"
    TEMPORAL_AFTER = "temporal_after"
    CONCURRENT_WITH = "concurrent_with"


# ── Attribute Definitions ────────────────────────────────────────────────────


class AttributeDefinition(BaseModel):
    """Definition of an attribute on a node or edge."""

    name: str
    attr_type: str  # "str", "int", "float", "bool", "list", "dict", "uuid", "datetime"
    required: bool = False
    default: Any = None
    description: str = ""


class NodeTypeDefinition(BaseModel):
    """Full definition of a node type with its allowed attributes."""

    node_type: NodeType
    category: NodeCategory
    attributes: list[AttributeDefinition] = Field(default_factory=list)
    description: str = ""


class EdgeTypeDefinition(BaseModel):
    """Full definition of an edge type with constraints."""

    edge_type: EdgeType
    allowed_source_types: list[NodeType] = Field(default_factory=list)
    allowed_target_types: list[NodeType] = Field(default_factory=list)
    attributes: list[AttributeDefinition] = Field(default_factory=list)
    description: str = ""


# ── Common Attributes ────────────────────────────────────────────────────────

COMMON_NODE_ATTRS = [
    AttributeDefinition(name="name", attr_type="str", required=True, description="Display name"),
    AttributeDefinition(name="qualified_name", attr_type="str", description="Fully qualified name"),
    AttributeDefinition(name="file_path", attr_type="str", description="Source file path"),
    AttributeDefinition(name="start_line", attr_type="int", description="Start line in source"),
    AttributeDefinition(name="end_line", attr_type="int", description="End line in source"),
    AttributeDefinition(name="language", attr_type="str", description="Programming language"),
    AttributeDefinition(name="fingerprint", attr_type="str", description="Content fingerprint"),
    AttributeDefinition(name="version", attr_type="str", description="Schema/analyzer version"),
]

COMMON_EDGE_ATTRS = [
    AttributeDefinition(name="confidence", attr_type="float", default=1.0, description="Confidence"),
    AttributeDefinition(name="source_analyzer", attr_type="str", description="Producing analyzer"),
]


# ── Schema Registry ──────────────────────────────────────────────────────────


_CATEGORY_MAP: dict[str, NodeCategory] = {}

_CODE_TYPES = {
    "module", "package", "file", "class", "interface", "trait", "struct",
    "enum", "enum_variant", "function", "method", "constructor", "destructor",
    "property", "field", "parameter", "variable", "constant", "type_alias",
    "generic_param", "decorator", "annotation", "lambda", "closure",
    "coroutine", "generator", "import", "export",
}
_STRUCTURE_TYPES = {
    "namespace", "layer", "component", "subsystem", "boundary",
    "dependency_group", "cycle", "pattern", "design_pattern", "anti_pattern",
    "code_smell", "metric", "complexity_node", "coupling_node",
}
_INFRA_TYPES = {
    "service", "microservice", "container", "pod", "deployment",
    "replica_set", "stateful_set", "daemon_set", "job", "cron_job",
    "config_map", "secret", "volume", "persistent_volume_claim",
    "ingress", "network_policy", "service_account", "role",
    "terraform_resource", "terraform_module", "ansible_playbook",
    "ansible_role", "ansible_task", "ci_pipeline", "ci_stage", "ci_job",
    "docker_image", "docker_layer", "helm_chart", "helm_value",
}
_DATA_TYPES = {
    "database", "table", "column", "index", "view", "stored_procedure",
    "trigger", "migration", "api_endpoint", "api_route",
    "graphql_type", "graphql_field", "graphql_query", "graphql_mutation",
    "protobuf_message", "protobuf_field", "protobuf_service", "protobuf_rpc",
    "openapi_schema", "openapi_path",
}
_RUNTIME_TYPES = {
    "process", "thread", "request", "span", "log_event", "error_event",
    "metric_series", "alert", "incident", "trace", "circuit_breaker",
    "rate_limiter", "health_check",
}
_TEST_TYPES = {"test_suite", "test_case", "test_fixture", "mock"}
_DOC_TYPES = {"doc_page", "comment", "todo_item"}
_SECURITY_TYPES = {"vulnerability", "security_policy", "permission", "auth_flow"}


def _infer_category(nt: NodeType) -> NodeCategory:
    val = nt.value
    if val in _CODE_TYPES:
        return NodeCategory.CODE
    elif val in _STRUCTURE_TYPES:
        return NodeCategory.STRUCTURE
    elif val in _INFRA_TYPES:
        return NodeCategory.INFRASTRUCTURE
    elif val in _DATA_TYPES:
        return NodeCategory.DATA
    elif val in _RUNTIME_TYPES:
        return NodeCategory.RUNTIME
    elif val in _TEST_TYPES:
        return NodeCategory.TESTING
    elif val in _DOC_TYPES:
        return NodeCategory.DOCUMENTATION
    elif val in _SECURITY_TYPES:
        return NodeCategory.SECURITY
    return NodeCategory.CODE


class SchemaRegistry:
    """Registry for all graph schema definitions.

    Provides validation and lookup for node/edge types and their attributes.
    Thread-safe after initialization (read-only after __init__).
    """

    def __init__(self) -> None:
        self._node_types: dict[NodeType, NodeTypeDefinition] = {}
        self._edge_types: dict[EdgeType, EdgeTypeDefinition] = {}
        self._initialize_defaults()

    def _initialize_defaults(self) -> None:
        for nt in NodeType:
            cat = _infer_category(nt)
            self._node_types[nt] = NodeTypeDefinition(
                node_type=nt,
                category=cat,
                attributes=list(COMMON_NODE_ATTRS),
                description=f"{nt.value} node type",
            )
        for et in EdgeType:
            self._edge_types[et] = EdgeTypeDefinition(
                edge_type=et,
                attributes=list(COMMON_EDGE_ATTRS),
                description=f"{et.value} edge type",
            )

    def get_node_type(self, node_type: NodeType) -> NodeTypeDefinition:
        return self._node_types[node_type]

    def get_edge_type(self, edge_type: EdgeType) -> EdgeTypeDefinition:
        return self._edge_types[edge_type]

    def validate_node_type(self, type_str: str) -> bool:
        try:
            NodeType(type_str)
            return True
        except ValueError:
            return False

    def validate_edge_type(self, type_str: str) -> bool:
        try:
            EdgeType(type_str)
            return True
        except ValueError:
            return False

    def all_node_types(self) -> list[NodeType]:
        return list(self._node_types.keys())

    def all_edge_types(self) -> list[EdgeType]:
        return list(self._edge_types.keys())

    def node_types_by_category(self, category: NodeCategory) -> list[NodeType]:
        return [
            nt for nt, defn in self._node_types.items()
            if defn.category == category
        ]

    @property
    def node_type_count(self) -> int:
        return len(self._node_types)

    @property
    def edge_type_count(self) -> int:
        return len(self._edge_types)


# Module-level singleton
SCHEMA_REGISTRY = SchemaRegistry()
