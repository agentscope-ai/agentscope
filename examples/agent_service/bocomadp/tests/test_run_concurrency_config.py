# -*- coding: utf-8 -*-
"""Tests for RunConcurrencyConfig defaults and validation."""
from bocomadp.config.app_config import AppConfig, RunConcurrencyConfig


def test_run_concurrency_defaults() -> None:
    cfg = RunConcurrencyConfig()
    assert cfg.enabled is True
    assert cfg.max_running == 10
    assert cfg.max_running_per_user == 3
    assert cfg.grace_secs == 6.0


def test_zero_means_unlimited() -> None:
    assert RunConcurrencyConfig(max_running=0).max_running == 0
    assert RunConcurrencyConfig(max_running_per_user=0).max_running_per_user == 0


def test_app_config_mounts_run_concurrency() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.run_concurrency, RunConcurrencyConfig)
