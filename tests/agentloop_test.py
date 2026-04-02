# -*- coding: utf-8 -*-
"""Unit tests for AgentLoop benchmark and evaluator storage."""
import json
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

# Ensure local source is preferred over any installed version so that
# newly added modules (not yet published) are importable.
_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC_DIR))

from agentscope.evaluate._agentloop_config import AgentLoopConfig
from agentscope.evaluate._agentloop_benchmark._agentloop_benchmark import (
    AgentLoopBenchmark,
)
from agentscope.evaluate._evaluator_storage._agentloop_evaluator_storage import (
    AgentLoopEvaluatorStorage,
    EXPERIMENT_LOGSTORE,
)
from agentscope.evaluate._task import Task
from agentscope.evaluate._solution import SolutionOutput


# ============================================================
# Helper factories
# ============================================================

def _make_config(
    region_id: str = "cn-hangzhou",
    project: str = "test_project",
    ground_truth_field: str = "",
    query: str = "",
) -> AgentLoopConfig:
    return AgentLoopConfig(
        workspace="test_workspace",
        dataset="test_dataset",
        region_id=region_id,
        project=project,
        query=query,
        ground_truth_field=ground_truth_field,
        access_key_id="test_key_id",
        access_key_secret="test_key_secret",
    )


# ============================================================
# AgentLoopConfig tests
# ============================================================

class TestAgentLoopConfig(unittest.TestCase):
    """Tests for AgentLoopConfig dataclass."""

    def test_basic_initialization(self) -> None:
        config = _make_config()
        self.assertEqual(config.workspace, "test_workspace")
        self.assertEqual(config.dataset, "test_dataset")
        self.assertEqual(config.region_id, "cn-hangzhou")
        self.assertEqual(config.project, "test_project")
        self.assertEqual(config.access_key_id, "test_key_id")
        self.assertEqual(config.access_key_secret, "test_key_secret")

    def test_default_query_is_empty(self) -> None:
        config = _make_config(query="")
        self.assertEqual(config.query, "")

    def test_default_max_rows(self) -> None:
        config = _make_config()
        self.assertEqual(config.max_rows, 1000)

    def test_custom_max_rows(self) -> None:
        config = AgentLoopConfig(
            workspace="ws",
            dataset="ds",
            region_id="cn-hangzhou",
            max_rows=500,
            access_key_id="k",
            access_key_secret="s",
        )
        self.assertEqual(config.max_rows, 500)

    def test_invalid_max_rows_raises(self) -> None:
        with self.assertRaises(ValueError):
            AgentLoopConfig(
                workspace="ws",
                dataset="ds",
                region_id="cn-hangzhou",
                max_rows=0,
                access_key_id="k",
                access_key_secret="s",
            )

    def test_negative_max_rows_raises(self) -> None:
        with self.assertRaises(ValueError):
            AgentLoopConfig(
                workspace="ws",
                dataset="ds",
                region_id="cn-hangzhou",
                max_rows=-1,
                access_key_id="k",
                access_key_secret="s",
            )

    def test_custom_query_preserved(self) -> None:
        custom_query = "* | select col1 from test_dataset limit 10"
        config = _make_config(query=custom_query)
        self.assertEqual(config.query, custom_query)

    def test_credentials_loaded_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ALIBABA_CLOUD_ACCESS_KEY_ID": "env_key",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "env_secret",
            },
        ):
            config = AgentLoopConfig(
                workspace="ws",
                dataset="ds",
                region_id="cn-hangzhou",
            )
        self.assertEqual(config.access_key_id, "env_key")
        self.assertEqual(config.access_key_secret, "env_secret")

    def test_explicit_credentials_override_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ALIBABA_CLOUD_ACCESS_KEY_ID": "env_key",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "env_secret",
            },
        ):
            config = AgentLoopConfig(
                workspace="ws",
                dataset="ds",
                region_id="cn-hangzhou",
                access_key_id="explicit_key",
                access_key_secret="explicit_secret",
            )
        self.assertEqual(config.access_key_id, "explicit_key")
        self.assertEqual(config.access_key_secret, "explicit_secret")

    def test_validate_credentials_success(self) -> None:
        config = _make_config()
        config.validate_credentials()  # should not raise

    def test_validate_credentials_missing_key_id(self) -> None:
        config = _make_config()
        config.access_key_id = ""
        with self.assertRaises(ValueError) as ctx:
            config.validate_credentials()
        self.assertIn("AccessKey", str(ctx.exception))

    def test_validate_credentials_missing_key_secret(self) -> None:
        config = _make_config()
        config.access_key_secret = ""
        with self.assertRaises(ValueError):
            config.validate_credentials()

    def test_validate_credentials_both_missing(self) -> None:
        config = AgentLoopConfig(
            workspace="ws",
            dataset="ds",
            region_id="cn-hangzhou",
        )
        # Ensure env vars are absent
        with patch.dict(os.environ, {}, clear=True):
            config.access_key_id = ""
            config.access_key_secret = ""
            with self.assertRaises(ValueError):
                config.validate_credentials()

    def test_cms_endpoint(self) -> None:
        config = _make_config(region_id="cn-hangzhou")
        self.assertEqual(config.cms_endpoint, "cms.cn-hangzhou.aliyuncs.com")

    def test_sls_endpoint(self) -> None:
        config = _make_config(region_id="cn-hangzhou")
        self.assertEqual(config.sls_endpoint, "cn-hangzhou.log.aliyuncs.com")

    def test_endpoints_with_different_region(self) -> None:
        config = _make_config(region_id="cn-shanghai")
        self.assertEqual(config.cms_endpoint, "cms.cn-shanghai.aliyuncs.com")
        self.assertEqual(config.sls_endpoint, "cn-shanghai.log.aliyuncs.com")

    def test_default_project_is_empty_string(self) -> None:
        config = AgentLoopConfig(
            workspace="ws",
            dataset="ds",
            region_id="cn-hangzhou",
            access_key_id="k",
            access_key_secret="s",
        )
        self.assertEqual(config.project, "")

    def test_default_ground_truth_field_is_empty(self) -> None:
        config = _make_config()
        self.assertEqual(config.ground_truth_field, "")


# ============================================================
# AgentLoopBenchmark tests
# ============================================================

class TestAgentLoopBenchmark(unittest.TestCase):
    """Tests for AgentLoopBenchmark class."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_benchmark(
        self,
        raw_data: list[dict],
        ground_truth_field: str = "",
        name: str = "AgentLoopBenchmark",
        description: str = "Benchmark loaded from AgentLoop.",
    ) -> AgentLoopBenchmark:
        """Create a benchmark with _load_data_from_cms patched."""
        with patch.object(
            AgentLoopBenchmark,
            "_load_data_from_cms",
            return_value=raw_data,
        ):
            return AgentLoopBenchmark(
                config=_make_config(ground_truth_field=ground_truth_field),
                name=name,
                description=description,
            )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def test_default_name_and_description(self) -> None:
        benchmark = self._make_benchmark([])
        self.assertEqual(benchmark.name, "AgentLoopBenchmark")
        self.assertEqual(benchmark.description, "Benchmark loaded from AgentLoop.")

    def test_custom_name_and_description(self) -> None:
        benchmark = self._make_benchmark(
            [],
            name="MyBench",
            description="Custom desc",
        )
        self.assertEqual(benchmark.name, "MyBench")
        self.assertEqual(benchmark.description, "Custom desc")

    def test_config_stored(self) -> None:
        config = _make_config()
        with patch.object(AgentLoopBenchmark, "_load_data_from_cms", return_value=[]):
            benchmark = AgentLoopBenchmark(config=config)
        self.assertIs(benchmark.config, config)

    # ------------------------------------------------------------------
    # Task conversion
    # ------------------------------------------------------------------

    def test_task_input_equals_log_record(self) -> None:
        record = {"question": "What is 1+1?", "category": "math"}
        benchmark = self._make_benchmark([record])
        self.assertEqual(benchmark[0].input, record)

    def test_task_ground_truth_empty_when_no_field_configured(self) -> None:
        record = {"question": "Q", "answer": "A"}
        benchmark = self._make_benchmark([record], ground_truth_field="")
        self.assertEqual(benchmark[0].ground_truth, "")

    def test_task_ground_truth_from_configured_field(self) -> None:
        record = {"question": "Q", "answer": "42"}
        benchmark = self._make_benchmark([record], ground_truth_field="answer")
        self.assertEqual(benchmark[0].ground_truth, "42")

    def test_task_ground_truth_missing_field_falls_back_to_empty(self) -> None:
        record = {"question": "Q"}
        benchmark = self._make_benchmark([record], ground_truth_field="answer")
        self.assertEqual(benchmark[0].ground_truth, "")

    def test_task_ids_are_unique(self) -> None:
        records = [{"q": str(i)} for i in range(10)]
        benchmark = self._make_benchmark(records)
        ids = [task.id for task in benchmark]
        self.assertEqual(len(ids), len(set(ids)), "Task IDs are not unique")

    def test_task_ids_are_stable_across_iterations(self) -> None:
        records = [{"q": "1"}, {"q": "2"}]
        benchmark = self._make_benchmark(records)
        first_pass = [task.id for task in benchmark]
        second_pass = [task.id for task in benchmark]
        self.assertEqual(first_pass, second_pass)

    def test_task_has_empty_metrics(self) -> None:
        benchmark = self._make_benchmark([{"q": "Q"}])
        self.assertEqual(benchmark[0].metrics, [])

    def test_task_has_empty_metadata(self) -> None:
        benchmark = self._make_benchmark([{"q": "Q"}])
        self.assertEqual(benchmark[0].metadata, {})

    # ------------------------------------------------------------------
    # __len__ / __iter__ / __getitem__
    # ------------------------------------------------------------------

    def test_len_empty(self) -> None:
        benchmark = self._make_benchmark([])
        self.assertEqual(len(benchmark), 0)

    def test_len_non_empty(self) -> None:
        benchmark = self._make_benchmark([{"q": str(i)} for i in range(5)])
        self.assertEqual(len(benchmark), 5)

    def test_iter_yields_task_objects(self) -> None:
        benchmark = self._make_benchmark([{"q": "1"}, {"q": "2"}])
        for task in benchmark:
            self.assertIsInstance(task, Task)

    def test_iter_yields_all_tasks(self) -> None:
        records = [{"q": str(i)} for i in range(3)]
        benchmark = self._make_benchmark(records)
        tasks = list(benchmark)
        self.assertEqual(len(tasks), 3)
        for task, record in zip(tasks, records):
            self.assertEqual(task.input, record)

    def test_getitem_first(self) -> None:
        records = [{"q": "first"}, {"q": "second"}]
        benchmark = self._make_benchmark(records)
        self.assertEqual(benchmark[0].input, {"q": "first"})

    def test_getitem_last(self) -> None:
        records = [{"q": "a"}, {"q": "b"}, {"q": "c"}]
        benchmark = self._make_benchmark(records)
        self.assertEqual(benchmark[2].input, {"q": "c"})

    def test_getitem_out_of_range(self) -> None:
        benchmark = self._make_benchmark([{"q": "only"}])
        with self.assertRaises(IndexError):
            _ = benchmark[5]

    # ------------------------------------------------------------------
    # _load_data_from_cms response parsing
    # ------------------------------------------------------------------

    def _make_mock_execute_query(self, data: object) -> MagicMock:
        """Build a fake execute_query callable returning the given data."""
        mock_resp = MagicMock()
        mock_resp.body.data = data
        mock_client = MagicMock()
        mock_client.execute_query.return_value = mock_resp
        return mock_client

    def _call_load_data(self, data: object) -> list[dict]:
        """Call _load_data_from_cms in single-query mode with a mocked client.

        A custom query is set on the config so that _load_data_from_cms takes
        the single-call path, which exercises _parse_response_data directly.
        """
        mock_client = self._make_mock_execute_query(data)
        mock_cms_models = MagicMock()
        mock_cms_models.ExecuteQueryRequest.return_value = MagicMock()
        mock_cms_pkg = MagicMock()
        mock_cms_pkg.models = mock_cms_models

        with patch.dict(
            "sys.modules",
            {"alibabacloud_cms20240330": mock_cms_pkg},
        ):
            with patch.object(
                AgentLoopBenchmark,
                "_load_data_from_cms",
                return_value=[],
            ):
                benchmark = AgentLoopBenchmark(config=_make_config())

            # Force single-call mode by providing a custom query
            benchmark.config.query = "* | select * from test_dataset"
            benchmark._get_client = MagicMock(return_value=mock_client)  # type: ignore[method-assign]
            return AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]

    def _make_paginated_benchmark(self) -> AgentLoopBenchmark:
        """Create a benchmark with no custom query (paginated mode)."""
        with patch.object(AgentLoopBenchmark, "_load_data_from_cms", return_value=[]):
            return AgentLoopBenchmark(config=_make_config())

    def test_load_data_list_response(self) -> None:
        records = [{"a": 1}, {"a": 2}]
        result = self._call_load_data(records)
        self.assertEqual(result, records)

    def test_load_data_dict_with_rows(self) -> None:
        rows = [{"r": 1}, {"r": 2}]
        result = self._call_load_data({"rows": rows})
        self.assertEqual(result, rows)

    def test_load_data_dict_with_records(self) -> None:
        records = [{"rec": 1}]
        result = self._call_load_data({"records": records})
        self.assertEqual(result, records)

    def test_load_data_dict_without_known_keys(self) -> None:
        data = {"unknown_key": "value"}
        result = self._call_load_data(data)
        self.assertEqual(result, [data])

    def test_load_data_none_body(self) -> None:
        mock_resp = MagicMock()
        mock_resp.body = None
        mock_client = MagicMock()
        mock_client.execute_query.return_value = mock_resp

        mock_cms_pkg = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_cms20240330": mock_cms_pkg}):
            with patch.object(
                AgentLoopBenchmark,
                "_load_data_from_cms",
                return_value=[],
            ):
                benchmark = AgentLoopBenchmark(config=_make_config())

            benchmark._get_client = MagicMock(return_value=mock_client)  # type: ignore[method-assign]
            result = AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]
        self.assertEqual(result, [])

    def test_load_data_none_data(self) -> None:
        result = self._call_load_data(None)
        self.assertEqual(result, [])

    # ------------------------------------------------------------------
    # Paginated loading (_load_data_from_cms with no custom query)
    # ------------------------------------------------------------------

    def test_paginated_stops_on_empty_response(self) -> None:
        """When a page returns [], pagination terminates immediately."""
        benchmark = self._make_paginated_benchmark()
        with patch.object(
            benchmark,
            "_execute_query_request",
            return_value=[],
        ):
            result = AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]
        # Benchmark needs SDK import mocked
        mock_cms_pkg = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_cms20240330": mock_cms_pkg}):
            with patch.object(benchmark, "_get_client", return_value=MagicMock()):
                with patch.object(benchmark, "_execute_query_request", return_value=[]):
                    result = AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]
        self.assertEqual(result, [])

    def test_paginated_stops_on_error(self) -> None:
        """When a page raises RuntimeError, pagination stops and returns
        whatever was collected so far."""
        benchmark = self._make_paginated_benchmark()
        first_page = [{"q": "1"}, {"q": "2"}]
        call_count = 0

        def side_effect(*_args: object, **_kwargs: object) -> list[dict]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_page
            raise RuntimeError("simulated API error")

        mock_cms_pkg = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_cms20240330": mock_cms_pkg}):
            with patch.object(benchmark, "_get_client", return_value=MagicMock()):
                with patch.object(
                    benchmark,
                    "_execute_query_request",
                    side_effect=side_effect,
                ):
                    result = AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]

        self.assertEqual(result, first_page)
        self.assertEqual(call_count, 2)

    def test_paginated_respects_max_rows(self) -> None:
        """Pagination stops once max_rows records have been collected."""
        benchmark = self._make_paginated_benchmark()
        benchmark.config.max_rows = 5
        page = [{"i": i} for i in range(3)]  # 3 records per page

        call_count = 0

        def side_effect(*_args: object, **_kwargs: object) -> list[dict]:
            nonlocal call_count
            call_count += 1
            return page

        mock_cms_pkg = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_cms20240330": mock_cms_pkg}):
            with patch.object(benchmark, "_get_client", return_value=MagicMock()):
                with patch.object(
                    benchmark,
                    "_execute_query_request",
                    side_effect=side_effect,
                ):
                    result = AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]

        # Page 1: LIMIT 5 OFFSET 0 → mock returns 3; page 2: LIMIT 2 OFFSET 3
        # → mock returns 3 but result is truncated to max_rows=5
        self.assertEqual(len(result), 5)
        self.assertEqual(call_count, 2)

    def test_paginated_multiple_pages_collected(self) -> None:
        """All records from multiple pages are concatenated."""
        benchmark = self._make_paginated_benchmark()
        benchmark.config.max_rows = 10
        pages = [
            [{"n": 0}, {"n": 1}, {"n": 2}],
            [{"n": 3}, {"n": 4}, {"n": 5}],
            [],  # empty → stop
        ]
        call_count = 0

        def side_effect(*_args: object, **_kwargs: object) -> list[dict]:
            nonlocal call_count
            result_page = pages[call_count]
            call_count += 1
            return result_page

        mock_cms_pkg = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_cms20240330": mock_cms_pkg}):
            with patch.object(benchmark, "_get_client", return_value=MagicMock()):
                with patch.object(
                    benchmark,
                    "_execute_query_request",
                    side_effect=side_effect,
                ):
                    result = AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]

        self.assertEqual(len(result), 6)
        self.assertEqual(result, pages[0] + pages[1])
        self.assertEqual(call_count, 3)

    def test_paginated_uses_correct_limit_offset_in_query(self) -> None:
        """Verify the page_query contains correct LIMIT and OFFSET values."""
        benchmark = self._make_paginated_benchmark()
        benchmark.config.max_rows = 3

        captured_queries: list[str] = []
        pages = [[{"n": 0}, {"n": 1}], [{"n": 2}], []]

        call_count = 0

        def side_effect(
            _client: object,
            _models: object,
            query: str,
        ) -> list[dict]:
            nonlocal call_count
            captured_queries.append(query)
            page = pages[call_count]
            call_count += 1
            return page

        mock_cms_pkg = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_cms20240330": mock_cms_pkg}):
            with patch.object(benchmark, "_get_client", return_value=MagicMock()):
                with patch.object(
                    benchmark,
                    "_execute_query_request",
                    side_effect=side_effect,
                ):
                    AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]

        # First page: LIMIT 3 OFFSET 0; second: LIMIT 1 OFFSET 2
        self.assertIn("LIMIT 3", captured_queries[0])
        self.assertIn("OFFSET 0", captured_queries[0])
        self.assertIn("LIMIT 1", captured_queries[1])
        self.assertIn("OFFSET 2", captured_queries[1])

    def test_custom_query_uses_single_call(self) -> None:
        """When config.query is set, _execute_query_request is called exactly once."""
        benchmark = self._make_paginated_benchmark()
        benchmark.config.query = "* | select * from test_dataset"
        records = [{"a": 1}]

        mock_cms_pkg = MagicMock()
        with patch.dict("sys.modules", {"alibabacloud_cms20240330": mock_cms_pkg}):
            with patch.object(benchmark, "_get_client", return_value=MagicMock()):
                with patch.object(
                    benchmark,
                    "_execute_query_request",
                    return_value=records,
                ) as mock_exec:
                    result = AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]

        mock_exec.assert_called_once()
        self.assertEqual(result, records)

    # ------------------------------------------------------------------
    # ImportError handling
    # ------------------------------------------------------------------

    def test_get_client_import_error(self) -> None:
        with patch.object(AgentLoopBenchmark, "_load_data_from_cms", return_value=[]):
            benchmark = AgentLoopBenchmark(config=_make_config())

        with patch.dict(
            "sys.modules",
            {
                "alibabacloud_cms20240330": None,
                "alibabacloud_cms20240330.client": None,
                "alibabacloud_tea_openapi": None,
                "alibabacloud_tea_openapi.models": None,
            },
        ):
            with self.assertRaises((ImportError, AttributeError)):
                benchmark._get_client()

    def test_load_data_from_cms_import_error(self) -> None:
        with patch.object(AgentLoopBenchmark, "_load_data_from_cms", return_value=[]):
            benchmark = AgentLoopBenchmark(config=_make_config())

        with patch.dict(
            "sys.modules",
            {"alibabacloud_cms20240330": None},
        ):
            with self.assertRaises((ImportError, AttributeError)):
                AgentLoopBenchmark._load_data_from_cms(benchmark)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # validate_credentials propagation
    # ------------------------------------------------------------------

    def test_invalid_credentials_raise_on_init(self) -> None:
        bad_config = AgentLoopConfig(
            workspace="ws",
            dataset="ds",
            region_id="cn-hangzhou",
        )
        bad_config.access_key_id = ""
        bad_config.access_key_secret = ""

        with patch.object(AgentLoopBenchmark, "_load_data_from_cms", return_value=[]):
            with self.assertRaises(ValueError):
                AgentLoopBenchmark(config=bad_config)


# ============================================================
# AgentLoopEvaluatorStorage tests
# ============================================================

class TestAgentLoopEvaluatorStorage(unittest.TestCase):
    """Tests for AgentLoopEvaluatorStorage class."""

    def setUp(self) -> None:
        self.save_dir = tempfile.mkdtemp()
        self.config = _make_config(project="my_sls_project")

    def tearDown(self) -> None:
        shutil.rmtree(self.save_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_storage(
        self,
        experiment_id: str = "exp-001",
        experiment_name: str = "test_exp",
        experiment_type: str = "agent",
        experiment_metadata: dict | None = None,
        experiment_config: dict | None = None,
    ) -> AgentLoopEvaluatorStorage:
        return AgentLoopEvaluatorStorage(
            save_dir=self.save_dir,
            config=self.config,
            experiment_id=experiment_id,
            experiment_name=experiment_name,
            experiment_type=experiment_type,
            experiment_metadata=experiment_metadata,
            experiment_config=experiment_config,
        )

    def _make_solution_output(
        self,
        success: bool = True,
        output: object = "42",
    ) -> SolutionOutput:
        return SolutionOutput(
            success=success,
            output=output,
            trajectory=[],
            meta={"note": "test"},
        )

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def test_experiment_id_stored(self) -> None:
        storage = self._make_storage(experiment_id="my-exp-id")
        self.assertEqual(storage.experiment_id, "my-exp-id")

    def test_experiment_name_stored(self) -> None:
        storage = self._make_storage(experiment_name="MyExperiment")
        self.assertEqual(storage.experiment_name, "MyExperiment")

    def test_experiment_type_stored(self) -> None:
        storage = self._make_storage(experiment_type="model")
        self.assertEqual(storage.experiment_type, "model")

    def test_logstore_is_experiment_detail(self) -> None:
        storage = self._make_storage()
        self.assertEqual(storage.logstore, EXPERIMENT_LOGSTORE)

    def test_default_experiment_id_generated_when_none(self) -> None:
        storage = AgentLoopEvaluatorStorage(
            save_dir=self.save_dir,
            config=self.config,
        )
        self.assertIsNotNone(storage.experiment_id)
        self.assertGreater(len(storage.experiment_id), 0)

    def test_default_experiment_name_generated_when_none(self) -> None:
        storage = AgentLoopEvaluatorStorage(
            save_dir=self.save_dir,
            config=self.config,
            experiment_id="abc12345",
        )
        self.assertIn("experiment_", storage.experiment_name)

    def test_default_metadata_is_local_run(self) -> None:
        storage = AgentLoopEvaluatorStorage(
            save_dir=self.save_dir,
            config=self.config,
        )
        self.assertEqual(storage.experiment_metadata, {"run_env": "local_run"})

    def test_custom_metadata_stored(self) -> None:
        storage = self._make_storage(experiment_metadata={"run_env": "ci"})
        self.assertEqual(storage.experiment_metadata, {"run_env": "ci"})

    def test_default_experiment_config_is_empty_dict(self) -> None:
        storage = AgentLoopEvaluatorStorage(
            save_dir=self.save_dir,
            config=self.config,
        )
        self.assertEqual(storage.experiment_config, {})

    def test_custom_experiment_config_stored(self) -> None:
        cfg = {"model": "gpt-4", "temperature": 0.7}
        storage = self._make_storage(experiment_config=cfg)
        self.assertEqual(storage.experiment_config, cfg)

    def test_task_meta_cache_initialized_empty(self) -> None:
        storage = self._make_storage()
        self.assertEqual(storage._task_meta_cache, {})

    def test_project_from_config(self) -> None:
        storage = self._make_storage()
        self.assertEqual(storage.config.project, "my_sls_project")

    def test_project_queried_when_not_provided(self) -> None:
        config = AgentLoopConfig(
            workspace="ws",
            dataset="ds",
            region_id="cn-hangzhou",
            project="",
            access_key_id="k",
            access_key_secret="s",
        )
        with patch.object(
            AgentLoopEvaluatorStorage,
            "_get_workspace_project",
            return_value="queried_project",
        ) as mock_get:
            storage = AgentLoopEvaluatorStorage(
                save_dir=self.save_dir,
                config=config,
            )
            mock_get.assert_called_once()
        self.assertEqual(storage.config.project, "queried_project")

    def test_invalid_credentials_raise_on_init(self) -> None:
        bad_config = AgentLoopConfig(
            workspace="ws",
            dataset="ds",
            region_id="cn-hangzhou",
        )
        bad_config.access_key_id = ""
        bad_config.access_key_secret = ""
        with self.assertRaises(ValueError):
            AgentLoopEvaluatorStorage(save_dir=self.save_dir, config=bad_config)

    # ------------------------------------------------------------------
    # _build_base_log_contents
    # ------------------------------------------------------------------

    def test_build_base_log_contents_core_fields(self) -> None:
        storage = self._make_storage(
            experiment_id="eid",
            experiment_name="ename",
            experiment_type="agent",
        )
        contents = storage._build_base_log_contents()
        content_dict = dict(contents)

        self.assertEqual(content_dict["experiment_id"], "eid")
        self.assertEqual(content_dict["experiment_name"], "ename")
        self.assertEqual(content_dict["experiment_type"], "agent")
        self.assertIn("experiment_start_time", content_dict)
        self.assertIn("experiment_metadata", content_dict)
        self.assertIn("experiment_config", content_dict)

    def test_build_base_log_contents_without_task_and_repeat(self) -> None:
        storage = self._make_storage()
        contents = storage._build_base_log_contents()
        keys = [k for k, _ in contents]
        self.assertNotIn("task_id", keys)
        self.assertNotIn("repeat_id", keys)

    def test_build_base_log_contents_with_task_id(self) -> None:
        storage = self._make_storage()
        contents = storage._build_base_log_contents(task_id="task-123")
        content_dict = dict(contents)
        self.assertEqual(content_dict["task_id"], "task-123")
        self.assertNotIn("repeat_id", content_dict)

    def test_build_base_log_contents_with_both_ids(self) -> None:
        storage = self._make_storage()
        contents = storage._build_base_log_contents(
            task_id="task-123",
            repeat_id="repeat-0",
        )
        content_dict = dict(contents)
        self.assertEqual(content_dict["task_id"], "task-123")
        self.assertEqual(content_dict["repeat_id"], "repeat-0")

    def test_build_base_log_contents_metadata_is_json_string(self) -> None:
        metadata = {"run_env": "test", "version": "1.0"}
        storage = self._make_storage(experiment_metadata=metadata)
        contents = storage._build_base_log_contents()
        content_dict = dict(contents)
        self.assertEqual(
            json.loads(content_dict["experiment_metadata"]),
            metadata,
        )

    def test_build_base_log_contents_config_is_json_string(self) -> None:
        exp_config = {"model": "claude", "temp": 0.5}
        storage = self._make_storage(experiment_config=exp_config)
        contents = storage._build_base_log_contents()
        content_dict = dict(contents)
        self.assertEqual(
            json.loads(content_dict["experiment_config"]),
            exp_config,
        )

    def test_build_base_log_contents_start_time_is_string(self) -> None:
        storage = self._make_storage()
        contents = storage._build_base_log_contents()
        content_dict = dict(contents)
        # All SLS values must be strings
        self.assertIsInstance(content_dict["experiment_start_time"], str)

    # ------------------------------------------------------------------
    # save_task_meta
    # ------------------------------------------------------------------

    def test_save_task_meta_writes_file(self) -> None:
        storage = self._make_storage()
        task_id = "task-001"
        meta = {"input": {"question": "Q1"}, "label": "label1"}
        storage.save_task_meta(task_id=task_id, meta_info=meta)

        expected_file = os.path.join(
            self.save_dir,
            task_id,
            "task_meta.json",
        )
        self.assertTrue(os.path.exists(expected_file))
        with open(expected_file, encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, meta)

    def test_save_task_meta_caches_in_memory(self) -> None:
        storage = self._make_storage()
        task_id = "task-002"
        meta = {"input": {"question": "Q2"}}
        storage.save_task_meta(task_id=task_id, meta_info=meta)

        self.assertIn(task_id, storage._task_meta_cache)
        self.assertEqual(storage._task_meta_cache[task_id], meta)

    def test_save_task_meta_cache_isolated_per_task(self) -> None:
        storage = self._make_storage()
        storage.save_task_meta("task-a", {"x": 1})
        storage.save_task_meta("task-b", {"x": 2})
        self.assertEqual(storage._task_meta_cache["task-a"], {"x": 1})
        self.assertEqual(storage._task_meta_cache["task-b"], {"x": 2})

    # ------------------------------------------------------------------
    # save_solution_result
    # ------------------------------------------------------------------

    def test_save_solution_result_writes_local_file(self) -> None:
        storage = self._make_storage()
        task_id = "task-sol-001"
        repeat_id = "0"
        output = self._make_solution_output()

        with patch.object(storage, "_put_log"):
            storage.save_solution_result(
                task_id=task_id,
                repeat_id=repeat_id,
                output=output,
            )

        solution_file = os.path.join(
            self.save_dir,
            task_id,
            repeat_id,
            "solution.json",
        )
        self.assertTrue(os.path.exists(solution_file))

    def test_save_solution_result_calls_put_log(self) -> None:
        storage = self._make_storage()
        task_id = "task-sol-002"
        repeat_id = "0"
        output = self._make_solution_output()

        with patch.object(storage, "_put_log") as mock_put:
            storage.save_solution_result(
                task_id=task_id,
                repeat_id=repeat_id,
                output=output,
            )
            mock_put.assert_called_once()

    def test_save_solution_result_put_log_contains_correct_fields(self) -> None:
        storage = self._make_storage(experiment_id="exp-xyz")
        task_id = "task-sol-003"
        repeat_id = "1"
        output = self._make_solution_output(success=True, output="hello")

        captured: list[list[tuple[str, str]]] = []

        def capture_put_log(contents: list[tuple[str, str]]) -> None:
            captured.append(contents)

        with patch.object(storage, "_put_log", side_effect=capture_put_log):
            storage.save_solution_result(
                task_id=task_id,
                repeat_id=repeat_id,
                output=output,
            )

        self.assertEqual(len(captured), 1)
        content_dict = dict(captured[0])

        self.assertEqual(content_dict["experiment_id"], "exp-xyz")
        self.assertEqual(content_dict["task_id"], task_id)
        self.assertEqual(content_dict["repeat_id"], repeat_id)
        self.assertIn("experiment_output", content_dict)
        self.assertIn("data_config", content_dict)
        self.assertIn("experiment_data", content_dict)

    def test_save_solution_result_experiment_output_content(self) -> None:
        storage = self._make_storage()
        task_id = "task-sol-004"
        repeat_id = "0"
        output = self._make_solution_output(success=False, output="error_output")

        captured: list[list[tuple[str, str]]] = []
        with patch.object(storage, "_put_log", side_effect=captured.append):
            storage.save_solution_result(
                task_id=task_id,
                repeat_id=repeat_id,
                output=output,
            )

        content_dict = dict(captured[0])
        experiment_output = json.loads(content_dict["experiment_output"])
        self.assertFalse(experiment_output["success"])
        self.assertEqual(experiment_output["output"], "error_output")

    def test_save_solution_result_uses_cached_task_meta(self) -> None:
        storage = self._make_storage()
        task_id = "task-sol-005"
        repeat_id = "0"
        meta = {"input": {"question": "What is AI?", "id": "q-999"}}
        output = self._make_solution_output()

        storage.save_task_meta(task_id=task_id, meta_info=meta)

        captured: list[list[tuple[str, str]]] = []
        with patch.object(storage, "_put_log", side_effect=captured.append):
            storage.save_solution_result(
                task_id=task_id,
                repeat_id=repeat_id,
                output=output,
            )

        content_dict = dict(captured[0])
        experiment_data = json.loads(content_dict["experiment_data"])
        self.assertEqual(experiment_data, meta["input"])

        data_config = json.loads(content_dict["data_config"])
        self.assertEqual(data_config["dataset_item_id"], "q-999")

    def test_save_solution_result_data_config_structure(self) -> None:
        storage = self._make_storage()
        task_id = "task-sol-006"
        repeat_id = "0"
        output = self._make_solution_output()

        captured: list[list[tuple[str, str]]] = []
        with patch.object(storage, "_put_log", side_effect=captured.append):
            storage.save_solution_result(
                task_id=task_id,
                repeat_id=repeat_id,
                output=output,
            )

        content_dict = dict(captured[0])
        data_config = json.loads(content_dict["data_config"])
        self.assertEqual(data_config["data_type"], "dataset")
        self.assertEqual(data_config["project"], self.config.project)
        self.assertEqual(data_config["dataset_id"], self.config.dataset)

    def test_save_solution_result_dataset_item_id_falls_back_to_task_id(self) -> None:
        storage = self._make_storage()
        task_id = "task-fallback"
        repeat_id = "0"
        output = self._make_solution_output()
        # no task meta cached → dataset_item_id should fall back to task_id

        captured: list[list[tuple[str, str]]] = []
        with patch.object(storage, "_put_log", side_effect=captured.append):
            storage.save_solution_result(
                task_id=task_id,
                repeat_id=repeat_id,
                output=output,
            )

        content_dict = dict(captured[0])
        data_config = json.loads(content_dict["data_config"])
        self.assertEqual(data_config["dataset_item_id"], task_id)

    # ------------------------------------------------------------------
    # _put_log ImportError
    # ------------------------------------------------------------------

    def test_put_log_raises_import_error_when_sdk_missing(self) -> None:
        storage = self._make_storage()
        with patch.dict("sys.modules", {"aliyun": None, "aliyun.log": None}):
            with self.assertRaises((ImportError, AttributeError)):
                storage._put_log([("key", "value")])

    # ------------------------------------------------------------------
    # _get_sls_client ImportError
    # ------------------------------------------------------------------

    def test_get_sls_client_raises_import_error_when_sdk_missing(self) -> None:
        storage = self._make_storage()
        with patch.dict("sys.modules", {"aliyun": None, "aliyun.log": None}):
            with self.assertRaises((ImportError, AttributeError)):
                storage._get_sls_client()

    # ------------------------------------------------------------------
    # _get_cms_client ImportError
    # ------------------------------------------------------------------

    def test_get_cms_client_raises_import_error_when_sdk_missing(self) -> None:
        storage = self._make_storage()
        with patch.dict(
            "sys.modules",
            {
                "alibabacloud_cms20240330": None,
                "alibabacloud_cms20240330.client": None,
                "alibabacloud_tea_openapi": None,
                "alibabacloud_tea_openapi.models": None,
            },
        ):
            with self.assertRaises((ImportError, AttributeError)):
                storage._get_cms_client()

    # ------------------------------------------------------------------
    # _get_workspace_project
    # ------------------------------------------------------------------

    def test_get_workspace_project_success(self) -> None:
        storage = self._make_storage()

        mock_resp = MagicMock()
        mock_resp.body.sls_project = "found_project"

        mock_client = MagicMock()
        mock_client.get_workspace_with_options.return_value = mock_resp

        mock_util_pkg = MagicMock()

        with patch.dict("sys.modules", {"alibabacloud_tea_util": mock_util_pkg}):
            with patch.object(storage, "_get_cms_client", return_value=mock_client):
                result = storage._get_workspace_project()

        self.assertEqual(result, "found_project")

    def test_get_workspace_project_no_sls_project_raises(self) -> None:
        storage = self._make_storage()

        mock_resp = MagicMock()
        mock_resp.body.sls_project = None

        mock_client = MagicMock()
        mock_client.get_workspace_with_options.return_value = mock_resp

        mock_util_pkg = MagicMock()

        with patch.dict("sys.modules", {"alibabacloud_tea_util": mock_util_pkg}):
            with patch.object(storage, "_get_cms_client", return_value=mock_client):
                with self.assertRaises(ValueError) as ctx:
                    storage._get_workspace_project()
        self.assertIn("SLS project", str(ctx.exception))

    def test_get_workspace_project_import_error(self) -> None:
        storage = self._make_storage()
        with patch.dict(
            "sys.modules",
            {"alibabacloud_tea_util": None, "alibabacloud_tea_util.models": None},
        ):
            with self.assertRaises((ImportError, AttributeError)):
                storage._get_workspace_project()


if __name__ == "__main__":
    unittest.main()
