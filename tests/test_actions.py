"""Action inputs and registration: what the tree editor sees."""

import pytest
from novaflow.types.core.action import NodeSchema
from pydantic import ValidationError

from src.actions.internal.policy_execution import PolicyEpisodeOutput
from src.actions.single_arm_policy import RunSingleArmPolicyInput

MG = {"controller": "ur5e", "motion_group": 0}


def test_single_arm_defaults_point_at_the_in_cluster_dummy():
    cfg = RunSingleArmPolicyInput.model_validate({"motion_group": MG})

    assert cfg.policy_url == "ws://app-dummy-policy:8080"
    assert (cfg.prompt, cfg.lead_ms, cfg.delay_until_braking_ms, cfg.timeout_s) == (
        "",
        0.0,
        200.0,
        5.0,
    )


def test_single_arm_needs_a_motion_group():
    with pytest.raises(ValidationError, match="motion_group"):
        RunSingleArmPolicyInput.model_validate({})


@pytest.mark.parametrize(
    "field,value", [("lead_ms", -1), ("delay_until_braking_ms", -1), ("timeout_s", 0)]
)
def test_tuning_inputs_are_bounded(field, value):
    with pytest.raises(ValidationError, match=field):
        RunSingleArmPolicyInput.model_validate({"motion_group": MG, field: value})


def test_schema_builds_against_the_novaflow_type_registry():
    """The exact path ``service.run()`` registers with: a field type the control
    plane would reject fails here instead of at deploy time."""
    NodeSchema.from_action_meta(
        "RunSingleArmPolicy",
        input_model=RunSingleArmPolicyInput,
        output_model=PolicyEpisodeOutput,
    )


@pytest.mark.parametrize(
    "env,url",
    [
        ("api-gateway:8080", "http://api-gateway:8080"),  # what the operator injects
        ("http://192.0.2.10/", "http://192.0.2.10"),
    ],
)
def test_nova_api_gets_a_scheme(monkeypatch, env, url):
    from src.actions.internal.policy_execution import _nova_api

    monkeypatch.setenv("NOVA_API", env)
    assert _nova_api() == url
