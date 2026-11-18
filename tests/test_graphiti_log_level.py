import logging

import pytest

CHILDREN = ("graphiti_core", "graphiti_core.graphiti", "graphiti_core.llm_client.openai_generic_client", "graphiti_core.driver.falkordb_driver")


@pytest.fixture
def root_at_info(monkeypatch):
    monkeypatch.setattr(logging.getLogger(), "level", logging.INFO)
    logging.Logger.manager._clear_cache()
    yield
    logging.Logger.manager._clear_cache()


def test_graphiti_loggers_are_held_at_warning(root_at_info):
    import agentboard_graph
    import corner_graph
    import graph_batch
    import graphiti_core
    assert corner_graph and graph_batch and agentboard_graph and graphiti_core
    for name in CHILDREN:
        lg = logging.getLogger(name)
        assert lg.getEffectiveLevel() == logging.WARNING, name
        assert not lg.isEnabledFor(logging.INFO), name
        assert lg.isEnabledFor(logging.WARNING), name
    print("PASS: graphiti_core and its children log only warnings and worse, even with the root logger at INFO")


def test_the_check_bites_for_a_logger_that_is_not_held(root_at_info):
    import corner_graph
    assert corner_graph
    other = logging.getLogger("some_other_library.module")
    assert other.isEnabledFor(logging.INFO)
    print("PASS: a logger outside graphiti_core still logs info, so the level check can fail")
