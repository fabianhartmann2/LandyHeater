import ast
import inspect
import json
import re
import textwrap
import unittest
from pathlib import Path

import app.rest_application as rest_module
from app.rest_application import RestApplication
from services.diagnostics_hub import (
    MAX_CAPTURE_PAGE_SIZE,
    MAX_EVENT_PAGE_SIZE,
    MAX_PROTOCOL_PAGE_SIZE,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCUMENT_PATH = PROJECT_ROOT / "web" / "openapi.json"
HTTP_METHODS = frozenset((
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "head",
    "options",
    "trace",
))

EXPECTED_OPERATIONS = {
    "/api/v1/security-context": {"get"},
    "/api/v1/control-context": {"get"},
    "/api/v1/status": {"get"},
    "/api/v1/diagnostics": {"get"},
    "/api/v1/events": {"get"},
    "/api/v1/protocol-log": {"get"},
    "/api/v1/capture": {"get", "post", "delete"},
    "/api/v1/capture/export": {"get"},
    "/api/v1/time/browser-sync": {"put"},
    "/api/v1/heater/start": {"post"},
    "/api/v1/heater/quick-start": {"post"},
    "/api/v1/heater/stop": {"post"},
    "/api/v1/heater/session": {"patch"},
    "/api/v1/settings": {"get", "patch"},
    "/api/v1/setup": {"get", "put"},
    "/api/v1/timers": {"get", "post"},
    "/api/v1/timers/~id/{timer_id_hex}": {"get", "put", "delete"},
    "/api/v1/timers/{timer_id}": {"get", "put", "delete"},
}

CONTROL_MUTATIONS = frozenset((
    ("/api/v1/heater/start", "post"),
    ("/api/v1/heater/quick-start", "post"),
    ("/api/v1/heater/stop", "post"),
    ("/api/v1/heater/session", "patch"),
))

GENERATION_FENCED_MUTATIONS = frozenset((
    ("/api/v1/heater/start", "post"),
    ("/api/v1/heater/quick-start", "post"),
    ("/api/v1/heater/session", "patch"),
    ("/api/v1/settings", "patch"),
    ("/api/v1/setup", "put"),
    ("/api/v1/timers", "post"),
    ("/api/v1/timers/~id/{timer_id_hex}", "put"),
    ("/api/v1/timers/~id/{timer_id_hex}", "delete"),
    ("/api/v1/timers/{timer_id}", "put"),
    ("/api/v1/timers/{timer_id}", "delete"),
))

REQUEST_FIELDS = {
    ("/api/v1/capture", "post"): rest_module._CAPTURE_FIELDS,
    ("/api/v1/time/browser-sync", "put"): rest_module._TIME_SYNC_FIELDS,
    ("/api/v1/heater/start", "post"): rest_module._START_FIELDS,
    ("/api/v1/heater/quick-start", "post"):
        rest_module._QUICK_START_FIELDS,
    ("/api/v1/heater/session", "patch"):
        rest_module._SESSION_UPDATE_FIELDS,
    ("/api/v1/settings", "patch"): rest_module._SETTINGS_FIELDS,
    ("/api/v1/setup", "put"): rest_module._SETUP_FIELDS,
    ("/api/v1/timers", "post"): rest_module._TIMER_FIELDS,
    ("/api/v1/timers/~id/{timer_id_hex}", "put"):
        rest_module._TIMER_FIELDS,
    ("/api/v1/timers/{timer_id}", "put"):
        rest_module._TIMER_FIELDS,
}


def _load_document():
    return json.loads(DOCUMENT_PATH.read_text(encoding="utf-8"))


def _walk(value):
    yield value
    if type(value) is dict:
        for item in value.values():
            yield from _walk(item)
    elif type(value) is list:
        for item in value:
            yield from _walk(item)


def _resolve(document, value):
    seen = set()
    while type(value) is dict and set(value) == {"$ref"}:
        reference = value["$ref"]
        if reference in seen:
            raise AssertionError("cyclic OpenAPI reference: {}".format(
                reference
            ))
        seen.add(reference)
        if not reference.startswith("#/"):
            raise AssertionError("external OpenAPI reference: {}".format(
                reference
            ))
        current = document
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if type(current) is not dict or part not in current:
                raise AssertionError("unresolved OpenAPI reference: {}".format(
                    reference
                ))
            current = current[part]
        value = current
    return value


def _route_from_condition(condition):
    if (
        not isinstance(condition, ast.Compare)
        or len(condition.ops) != 1
        or not isinstance(condition.ops[0], ast.Eq)
        or not isinstance(condition.left, ast.Name)
        or condition.left.id != "path"
        or len(condition.comparators) != 1
    ):
        return None
    candidate = condition.comparators[0]
    if (
        not isinstance(candidate, ast.BinOp)
        or not isinstance(candidate.op, ast.Add)
        or not isinstance(candidate.left, ast.Name)
        or candidate.left.id != "API_PREFIX"
        or not isinstance(candidate.right, ast.Constant)
        or type(candidate.right.value) is not str
    ):
        return None
    return rest_module.API_PREFIX + candidate.right.value


def _methods_in(statements):
    result = set()
    module = ast.Module(body=statements, type_ignores=[])
    for node in ast.walk(module):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "method"
            and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and type(node.comparators[0].value) is str
        ):
            result.add(node.comparators[0].value.lower())
    return result


def _is_timer_item_condition(condition):
    for node in ast.walk(condition):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "timer_prefix"
        ):
            return True
    return False


def _runtime_operations():
    source = textwrap.dedent(inspect.getsource(RestApplication._route))
    tree = ast.parse(source)
    function = tree.body[0]
    result = {}
    for statement in function.body:
        if not isinstance(statement, ast.If):
            continue
        route = _route_from_condition(statement.test)
        if route is None and _is_timer_item_condition(statement.test):
            # One runtime branch accepts both the canonical hex resource and
            # its deprecated single-segment compatibility alias.
            methods = _methods_in(statement.body)
            result["/api/v1/timers/~id/{timer_id_hex}"] = methods
            result["/api/v1/timers/{timer_id}"] = set(methods)
            continue
        if route is not None:
            if route in result:
                raise AssertionError("duplicate runtime route: " + route)
            result[route] = _methods_in(statement.body)
    return result


def _document_paths(document):
    servers = document.get("servers")
    if type(servers) is not list or len(servers) != 1:
        raise AssertionError("OpenAPI must have exactly one local server")
    base = servers[0].get("url")
    if type(base) is not str or not base.startswith("/") or base.startswith("//"):
        raise AssertionError("OpenAPI server must be root-relative")
    base = base.rstrip("/")
    result = {}
    for path, item in document.get("paths", {}).items():
        full_path = base + path
        if not full_path.startswith("/"):
            full_path = "/" + full_path
        methods = {name for name in item if name in HTTP_METHODS}
        result[full_path] = methods
    return result


def _operation_index(document):
    paths = _document_paths(document)
    base = document["servers"][0]["url"].rstrip("/")
    result = {}
    for path, path_item in document["paths"].items():
        full_path = base + path
        if not full_path.startswith("/"):
            full_path = "/" + full_path
        for method in paths[full_path]:
            result[(full_path, method)] = (path_item, path_item[method])
    return result


def _parameters(document, path_item, operation):
    result = []
    for value in path_item.get("parameters", []) + operation.get(
        "parameters", []
    ):
        result.append(_resolve(document, value))
    return result


def _request_schema(document, operation):
    request_body = operation.get("requestBody")
    if request_body is None:
        return None
    request_body = _resolve(document, request_body)
    content = request_body.get("content", {})
    return _resolve(document, content["application/json"]["schema"])


def _object_contract(document, schema, active=None):
    if active is None:
        active = set()
    if type(schema) is dict and set(schema) == {"$ref"}:
        reference = schema["$ref"]
        if reference in active:
            return {}, set(), None, None
        active = set(active)
        active.add(reference)
        schema = _resolve(document, schema)
    properties = {}
    required = set()
    additional = schema.get("additionalProperties")
    minimum_properties = schema.get("minProperties")
    for part in schema.get("allOf", []):
        part_properties, part_required, part_additional, part_minimum = (
            _object_contract(document, part, active)
        )
        properties.update(part_properties)
        required.update(part_required)
        if part_additional is False:
            additional = False
        if part_minimum is not None:
            if minimum_properties is None:
                minimum_properties = part_minimum
            else:
                minimum_properties = max(minimum_properties, part_minimum)
    properties.update(schema.get("properties", {}))
    required.update(schema.get("required", []))
    return properties, required, additional, minimum_properties


class TestOpenAPIDocument(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = _load_document()
        cls.operations = _operation_index(cls.document)

    def test_document_is_openapi_3_and_all_references_are_local(self):
        document = self.document
        self.assertEqual(document.get("openapi"), "3.0.3")
        self.assertIs(type(document.get("info")), dict)
        self.assertTrue(document["info"].get("title"))
        self.assertTrue(document["info"].get("version"))
        self.assertNotIn("externalDocs", document)
        self.assertNotIn("webhooks", document)
        for server in document.get("servers", []):
            url = server.get("url")
            self.assertIs(type(url), str)
            self.assertTrue(url.startswith("/"), url)
            self.assertFalse(url.startswith("//"), url)
            self.assertNotIn("://", url)
        for value in _walk(document):
            if type(value) is not dict or "$ref" not in value:
                continue
            self.assertEqual(set(value), {"$ref"})
            self.assertTrue(value["$ref"].startswith("#/"), value["$ref"])
            self.assertIsNotNone(_resolve(document, value))

    def test_paths_and_methods_match_rest_application_exactly(self):
        self.assertEqual(_runtime_operations(), EXPECTED_OPERATIONS)
        self.assertEqual(_document_paths(self.document), EXPECTED_OPERATIONS)
        self.assertEqual(len(self.document["paths"]), 18)
        self.assertEqual(len(self.operations), 27)

    def test_operations_have_valid_unique_metadata_and_json_responses(self):
        operation_ids = set()
        for (path, method), (path_item, operation) in self.operations.items():
            with self.subTest(path=path, method=method):
                self.assertIs(type(operation.get("summary")), str)
                self.assertTrue(operation["summary"].strip())
                self.assertIs(type(operation.get("tags")), list)
                self.assertTrue(operation["tags"])
                operation_id = operation.get("operationId")
                self.assertIs(type(operation_id), str)
                self.assertRegex(operation_id, r"^[A-Za-z][A-Za-z0-9]*$")
                self.assertNotIn(operation_id, operation_ids)
                operation_ids.add(operation_id)
                responses = operation.get("responses")
                self.assertIs(type(responses), dict)
                self.assertTrue(any(code.startswith("2") for code in responses))
                self.assertIn("default", responses)
                for status, raw_response in responses.items():
                    self.assertRegex(status, r"^(?:default|[1-5][0-9]{2})$")
                    response = _resolve(self.document, raw_response)
                    self.assertTrue(response.get("description"))
                    self.assertEqual(
                        set(response.get("content", {})),
                        {"application/json"},
                    )
                    schema = response["content"]["application/json"]["schema"]
                    self.assertIsNotNone(_resolve(self.document, schema))
                parameters = _parameters(
                    self.document, path_item, operation
                )
                identities = [(item["in"], item["name"].lower())
                              for item in parameters]
                self.assertEqual(len(identities), len(set(identities)))
                template_names = set(re.findall(r"{([^{}]+)}", path))
                path_names = {
                    item["name"] for item in parameters
                    if item.get("in") == "path"
                }
                self.assertEqual(path_names, template_names)
                for parameter in parameters:
                    if parameter.get("in") == "path":
                        self.assertIs(parameter.get("required"), True)

    def test_component_schemas_use_the_openapi_3_subset_consistently(self):
        forbidden_31_keywords = frozenset((
            "$schema",
            "const",
            "dependentSchemas",
            "if",
            "prefixItems",
            "then",
            "unevaluatedProperties",
        ))

        def validate(schema, location, active):
            if type(schema) is not dict:
                self.fail("{} must be a schema object".format(location))
            if "$ref" in schema:
                self.assertEqual(set(schema), {"$ref"})
                reference = schema["$ref"]
                if reference in active:
                    return
                validate(
                    _resolve(self.document, schema),
                    reference,
                    active | {reference},
                )
                return
            self.assertFalse(forbidden_31_keywords.intersection(schema), location)
            schema_type = schema.get("type")
            if schema_type is not None:
                self.assertIn(
                    schema_type,
                    ("array", "boolean", "integer", "number", "object", "string"),
                    location,
                )
            if "enum" in schema:
                self.assertIs(type(schema["enum"]), list)
                self.assertTrue(schema["enum"], location)
            for minimum, maximum in (
                ("minimum", "maximum"),
                ("minLength", "maxLength"),
                ("minItems", "maxItems"),
                ("minProperties", "maxProperties"),
            ):
                if minimum in schema and maximum in schema:
                    self.assertLessEqual(
                        schema[minimum], schema[maximum], location
                    )
            if schema_type == "array" or "items" in schema:
                self.assertIn("items", schema, location)
                validate(schema["items"], location + "/items", active)
            properties = schema.get("properties", {})
            self.assertIs(type(properties), dict)
            required = schema.get("required", [])
            self.assertIs(type(required), list)
            self.assertEqual(len(required), len(set(required)), location)
            if schema.get("additionalProperties") is False:
                self.assertTrue(set(required).issubset(properties), location)
            for name, child in properties.items():
                validate(child, location + "/properties/" + name, active)
            additional = schema.get("additionalProperties")
            if additional is not None and type(additional) is not bool:
                validate(additional, location + "/additionalProperties", active)
            for keyword in ("allOf", "anyOf", "oneOf"):
                if keyword not in schema:
                    continue
                self.assertIs(type(schema[keyword]), list)
                self.assertTrue(schema[keyword], location)
                for index, child in enumerate(schema[keyword]):
                    validate(
                        child,
                        "{}/{}[{}]".format(location, keyword, index),
                        active,
                    )
            if "not" in schema:
                validate(schema["not"], location + "/not", active)

        schemas = self.document["components"]["schemas"]
        self.assertIs(type(schemas), dict)
        self.assertTrue(schemas)
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                validate(schema, "#/components/schemas/" + name, set())

    def test_success_and_error_envelopes_include_request_metadata(self):
        for (path, method), (_, operation) in self.operations.items():
            with self.subTest(path=path, method=method):
                for status, raw_response in operation["responses"].items():
                    response = _resolve(self.document, raw_response)
                    schema = response["content"]["application/json"]["schema"]
                    properties, required, _, _ = _object_contract(
                        self.document, schema
                    )
                    if status.startswith("2"):
                        self.assertIn("api_version", properties)
                        self.assertIn("request_id", properties)
                        self.assertTrue(
                            {"api_version", "request_id"}.issubset(required)
                        )
        error = _resolve(
            self.document,
            self.document["components"]["responses"]["Error"],
        )
        error_schema = _resolve(
            self.document,
            error["content"]["application/json"]["schema"],
        )
        self.assertEqual(error_schema.get("required"), ["api_version", "error"])
        nested = _resolve(
            self.document, error_schema["properties"]["error"]
        )
        self.assertTrue(
            {"code", "message", "request_id"}.issubset(
                nested.get("required", [])
            )
        )

    def test_request_bodies_match_the_runtime_exact_field_contracts(self):
        for key, (_, operation) in self.operations.items():
            with self.subTest(path=key[0], method=key[1]):
                schema = _request_schema(self.document, operation)
                if key not in REQUEST_FIELDS:
                    self.assertIsNone(schema)
                    continue
                self.assertIsNotNone(schema)
                request_body = _resolve(
                    self.document, operation["requestBody"]
                )
                self.assertIs(request_body.get("required"), True)
                self.assertEqual(
                    set(request_body.get("content", {})),
                    {"application/json"},
                )
                properties, required, additional, minimum = _object_contract(
                    self.document, schema
                )
                expected = set(REQUEST_FIELDS[key])
                optional = (
                    {"power_level"}
                    if key == ("/api/v1/heater/session", "patch") else set()
                )
                self.assertEqual(set(properties), expected | optional)
                self.assertIs(additional, False)
                if key == ("/api/v1/settings", "patch"):
                    self.assertEqual(required, set())
                    self.assertEqual(minimum, 1)
                else:
                    self.assertEqual(required, expected)

    def test_power_update_request_and_outcome_are_documented(self):
        operation = self.operations[("/api/v1/heater/session", "patch")][1]
        request = operation["requestBody"]["content"]["application/json"]
        self.assertEqual(request["examples"]["power"]["value"], {
            "expected_request_revision": 0,
            "target_temperature": None,
            "extend_minutes": 0,
            "power_level": 4,
        })
        schema = _resolve(self.document, request["schema"])
        self.assertEqual(schema["properties"]["power_level"]["minimum"], 1)
        self.assertEqual(schema["properties"]["power_level"]["maximum"], 9)
        self.assertNotIn("power_level", schema["required"])
        self.assertIn("202", operation["responses"])
        schemas = self.document["components"]["schemas"]
        self.assertIn("power_update", schemas["HeaterStatus"]["required"])
        self.assertEqual(
            schemas["PowerUpdateStatus"]["properties"]["state"]["enum"],
            ["pending", "applied", "failed"],
        )

    def test_ingress_csrf_origin_and_generation_fences_match_runtime(self):
        schemes = self.document["components"]["securitySchemes"]
        self.assertEqual(set(schemes), {"apCsrf", "controlCsrf"})
        for name in ("apCsrf", "controlCsrf"):
            scheme = schemes[name]
            self.assertEqual(scheme.get("type"), "apiKey")
            self.assertEqual(scheme.get("in"), "header")
            self.assertEqual(scheme.get("name"), "X-Landy-CSRF")
        self.assertIn("/api/v1/security-context", schemes["apCsrf"]["description"])
        self.assertIn(
            "/api/v1/control-context", schemes["controlCsrf"]["description"]
        )
        info_description = self.document["info"].get("description", "")
        self.assertIn("Host", info_description)
        self.assertIn("Origin", info_description)

        if_match = self.document["components"]["parameters"]["IfMatch"]
        self.assertEqual(if_match.get("name"), "If-Match")
        self.assertEqual(if_match.get("in"), "header")
        self.assertIs(if_match.get("required"), True)
        origin = self.document["components"]["parameters"]["Origin"]
        self.assertEqual(origin.get("name"), "Origin")
        self.assertEqual(origin.get("in"), "header")
        self.assertIs(origin.get("required"), True)

        all_mutations = {
            key for key in self.operations if key[1] != "get"
        }
        for key, (path_item, operation) in self.operations.items():
            path, method = key
            with self.subTest(path=path, method=method):
                if key in all_mutations:
                    expected_scheme = (
                        "controlCsrf" if key in CONTROL_MUTATIONS else "apCsrf"
                    )
                    self.assertEqual(
                        operation.get("security"), [{expected_scheme: []}]
                    )
                    expected_ingress = (
                        "ap-and-station"
                        if key in CONTROL_MUTATIONS else "ap-only"
                    )
                else:
                    self.assertNotIn("security", operation)
                    expected_ingress = (
                        "ap-only"
                        if path == "/api/v1/security-context"
                        else "ap-and-station"
                    )
                self.assertEqual(
                    operation.get("x-landy-ingress"), expected_ingress
                )
                raw_refs = {
                    item.get("$ref")
                    for item in path_item.get("parameters", [])
                    + operation.get("parameters", [])
                    if type(item) is dict and "$ref" in item
                }
                if key in all_mutations:
                    self.assertIn(
                        "#/components/parameters/Origin", raw_refs
                    )
                self.assertEqual(
                    "#/components/parameters/IfMatch" in raw_refs,
                    key in GENERATION_FENCED_MUTATIONS,
                )

        stop = self.operations[("/api/v1/heater/stop", "post")][1]
        self.assertIs(stop.get("x-landy-safety-path"), True)
        self.assertNotIn("requestBody", stop)
        self.assertNotIn(
            "#/components/parameters/IfMatch",
            {item.get("$ref") for item in stop.get("parameters", [])},
        )

    def test_query_parameters_match_runtime_names_defaults_and_bounds(self):
        maximum_id = rest_module.MAX_REQUEST_ID
        expected = {
            ("/api/v1/diagnostics", "get"): {
                "event_after": (0, maximum_id, None),
                "protocol_after": (0, maximum_id, None),
            },
            ("/api/v1/events", "get"): {
                "after": (0, maximum_id, 0),
                "limit": (1, MAX_EVENT_PAGE_SIZE, MAX_EVENT_PAGE_SIZE),
            },
            ("/api/v1/protocol-log", "get"): {
                "after": (0, maximum_id, 0),
                "limit": (
                    1,
                    MAX_PROTOCOL_PAGE_SIZE,
                    MAX_PROTOCOL_PAGE_SIZE,
                ),
            },
            ("/api/v1/capture/export", "get"): {
                "offset": (0, maximum_id, 0),
                "limit": (
                    1,
                    MAX_CAPTURE_PAGE_SIZE,
                    MAX_CAPTURE_PAGE_SIZE,
                ),
            },
            ("/api/v1/timers", "get"): {
                "offset": (0, 32, 0),
                "limit": (
                    1,
                    rest_module.MAX_TIMER_PAGE_SIZE,
                    rest_module.MAX_TIMER_PAGE_SIZE,
                ),
            },
        }
        for key, (path_item, operation) in self.operations.items():
            query = {
                item["name"]: item
                for item in _parameters(
                    self.document, path_item, operation
                )
                if item.get("in") == "query"
            }
            with self.subTest(path=key[0], method=key[1]):
                self.assertEqual(set(query), set(expected.get(key, {})))
                for name, bounds in expected.get(key, {}).items():
                    parameter = query[name]
                    self.assertIs(parameter.get("required"), False)
                    schema = _resolve(
                        self.document, parameter.get("schema")
                    )
                    self.assertEqual(schema.get("type"), "integer")
                    self.assertEqual(schema.get("minimum"), bounds[0])
                    self.assertEqual(schema.get("maximum"), bounds[1])
                    self.assertEqual(schema.get("default"), bounds[2])
        diagnostics = self.operations[("/api/v1/diagnostics", "get")]
        paired = {
            item["name"]: item.get("description", "")
            for item in _parameters(
                self.document, diagnostics[0], diagnostics[1]
            )
        }
        self.assertIn("protocol_after", paired["event_after"])
        self.assertIn("event_after", paired["protocol_after"])

    def test_timer_item_paths_keep_hex_canonical_and_mark_alias_deprecated(self):
        canonical = "/api/v1/timers/~id/{timer_id_hex}"
        alias = "/api/v1/timers/{timer_id}"
        self.assertIn(canonical, _document_paths(self.document))
        self.assertIn(alias, _document_paths(self.document))
        path_item = self.operations[(canonical, "get")][0]
        parameters = {
            item["name"]: item
            for item in _parameters(
                self.document,
                path_item,
                self.operations[(canonical, "get")][1],
            )
        }
        parameter = parameters["timer_id_hex"]
        self.assertEqual(parameter.get("in"), "path")
        self.assertIs(parameter.get("required"), True)
        schema = _resolve(self.document, parameter["schema"])
        self.assertEqual(schema.get("type"), "string")
        self.assertEqual(schema.get("minLength"), 2)
        self.assertEqual(schema.get("maxLength"), 128)
        pattern = re.compile(schema["pattern"])
        self.assertIsNotNone(pattern.fullmatch("6e69676874"))
        for invalid in ("", "a", "xyz", "6e6"):
            self.assertIsNone(pattern.fullmatch(invalid), invalid)
        for method in ("get", "put", "delete"):
            self.assertIs(
                self.operations[(alias, method)][1].get("deprecated"), True
            )
            self.assertIsNot(
                self.operations[(canonical, method)][1].get("deprecated"),
                True,
            )


if __name__ == "__main__":
    unittest.main()
