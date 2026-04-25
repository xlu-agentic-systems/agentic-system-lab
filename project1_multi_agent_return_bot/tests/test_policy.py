from project1_multi_agent_return_bot.app.catalog import Catalog
from project1_multi_agent_return_bot.app.policy import check_return_policy


def test_eligible_item_inside_return_window() -> None:
    catalog = Catalog()

    result = check_return_policy(catalog, "order-1001", "item-1")

    assert result.eligible is True
    assert result.reason_codes == []
    assert str(result.amount) == "129.99"


def test_expired_return_window_is_not_eligible() -> None:
    catalog = Catalog()

    result = check_return_policy(catalog, "order-1002", "item-3")

    assert result.eligible is False
    assert "return_window_expired" in result.reason_codes


def test_final_sale_item_is_not_refundable() -> None:
    catalog = Catalog()

    result = check_return_policy(catalog, "order-1001", "item-2")

    assert result.eligible is False
    assert "item_not_refundable" in result.reason_codes
    assert "policy_disallows_refund" in result.reason_codes
