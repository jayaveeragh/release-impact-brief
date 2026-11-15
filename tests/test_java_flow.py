from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import extract_all
from release_impact_brief.adapters.java import JavaAdapter

SOURCE = """\
package shop;

class OrderController {
    @PostMapping("/orders")
    public String create(@Valid Order order, BindingResult result, @RequestParam int page) {
        String code = order.getCode();
        if (code == null) {
            code = "";
        }
        else {
            code = code.strip();
        }
        Page<Order> found = search(page, code);
        if (page < 1 || page > Math.max(found.getTotalPages(), 1)) {
            if (!code.isEmpty()) {
                log(code);
            }
            return "redirect:/orders";
        }
        if (StringUtils.hasText(order.getName()) && order.isNew()) {
            result.rejectValue("name", "duplicate");
        }
        String label = page > 2 ? "many" : "few";
        switch (order.getStatus()) {
            case OPEN -> notify(order);
            case CLOSED -> { return "closed"; }
            default -> throw new IllegalStateException();
        }
        try {
            save(order);
        }
        catch (DataException ex) {
            if (!isDuplicate(ex)) {
                throw ex;
            }
            return "form";
        }
        items.forEach(item -> item.check(page));
        return "done";
    }

    public int getRate() { return rate; }
}
"""


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("shop")
    (root / "OrderController.java").write_text(SOURCE)
    facts = extract_all(root, [JavaAdapter()])
    method = next(n for n in facts.nodes if n.name == "create")
    assert not any(n.name == "getRate" and "flow" in n.attributes for n in facts.nodes)
    return dict(method.attributes["flow"])


def test_branch_points_in_source_order_with_nesting(flow: dict[str, Any]) -> None:
    kinds = [(b["kind"], b["line"], b["parent"]) for b in flow["branches"]]
    assert kinds == [
        ("if", 7, None),
        ("if", 14, None),
        ("if", 15, 1),  # nested in the if on line 14
        ("if", 20, None),
        ("ternary", 23, None),
        ("switch", 24, None),
        ("catch", 32, None),
        ("if", 33, 6),
    ]


def test_conditions_record_what_they_read(flow: dict[str, Any]) -> None:
    by_line = {b["line"]: b for b in flow["branches"]}
    assert by_line[7]["reads"] == ["code"]
    assert by_line[14]["reads"] == ["page", "found.getTotalPages()"]  # Math is a class
    assert by_line[20]["reads"] == ["order.getName()", "order.isNew()"]
    assert by_line[24]["reads"] == ["order.getStatus()"]
    assert by_line[32]["reads"] == [] and by_line[32]["exception"] == "DataException"
    assert by_line[33]["reads"] == ["ex"]
    assert by_line[14]["condition"] == "page < 1 || page > Math.max(found.getTotalPages(), 1)"


def test_exits_returns_throws_and_cases(flow: dict[str, Any]) -> None:
    by_line = {b["line"]: b for b in flow["branches"]}
    assert by_line[7]["exits"] == []
    assert by_line[14]["exits"] == [18]
    assert by_line[32]["exits"] == [34, 36]  # every exit inside the catch, at any depth
    assert [(c["label"], c["exits"]) for c in by_line[24]["cases"]] == [
        ("OPEN", []), ("CLOSED", [26]), ("default", [27])]  # fmt: skip
    assert flow["returns"] == [18, 26, 36, 39]
    assert flow["throws"] == [27, 34]


def test_locals_calls_and_rejections(flow: dict[str, Any]) -> None:
    assert [(a["name"], a["line"], a["reads"]) for a in flow["locals"]] == [
        ("code", 6, ["order.getCode()"]),
        ("code", 8, []),
        ("code", 11, ["code.strip()"]),
        ("found", 13, ["page", "code"]),
        ("label", 23, ["page"]),
    ]
    calls = {(c["line"], c["callee"]): c["reads"] for c in flow["calls"]}
    assert calls[(13, "search")] == ["page", "code"]
    assert calls[(25, "notify")] == ["order"]
    assert calls[(30, "save")] == ["order"]
    assert flow["rejects"] == [{"line": 21, "target": "result", "branch": 3}]


def test_lambda_bodies_are_not_read_as_conditions(flow: dict[str, Any]) -> None:
    # `item -> item.check(page)` uses page, but inside a lambda we don't model
    assert not any("item" in r for b in flow["branches"] for r in b["reads"])


def test_methods_without_branches_or_mappings_store_no_flow(tmp_path: Path) -> None:
    (tmp_path / "A.java").write_text("class A { int get() { return 1; } }\n")
    facts = extract_all(tmp_path, [JavaAdapter()])
    assert all("flow" not in n.attributes for n in facts.nodes)
