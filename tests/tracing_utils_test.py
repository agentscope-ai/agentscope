# -*- coding: utf-8 -*-
"""Unit tests for the tracing utils module."""
from unittest import TestCase
import datetime
import enum
import json
from dataclasses import dataclass

from pydantic import BaseModel

from agentscope.message import Msg, TextBlock
from agentscope.tracing._utils import _to_serializable, _serialize_to_str


class ExampleEnum(enum.Enum):
    """Example enum for serialization."""

    VALUE1 = "value1"
    VALUE2 = 42


@dataclass
class ExampleDataClass:
    """Example dataclass for serialization."""

    name: str
    age: int


class ExamplePydanticModel(BaseModel):
    """Example Pydantic model for serialization."""

    name: str
    age: int


class UtilsTest(TestCase):
    """Test cases for the utils module."""

    def test_to_serializable_primitive_types(self) -> None:
        """Test _to_serializable with primitive types."""
        # Test string
        self.assertEqual(_to_serializable("hello"), "hello")

        # Test integer
        self.assertEqual(_to_serializable(42), 42)

        # Test boolean
        self.assertEqual(_to_serializable(True), True)
        self.assertEqual(_to_serializable(False), False)

        # Test float
        self.assertEqual(_to_serializable(3.14), 3.14)

        # Test None
        self.assertIsNone(_to_serializable(None))

    def test_to_serializable_collections(self) -> None:
        """Test _to_serializable with collections."""
        # Test list
        result = _to_serializable([1, 2, 3])
        self.assertEqual(result, [1, 2, 3])

        # Test tuple
        result = _to_serializable((1, 2, 3))
        self.assertEqual(result, [1, 2, 3])

        # Test set
        result = _to_serializable({1, 2, 3})
        self.assertIsInstance(result, list)
        self.assertEqual(set(result), {1, 2, 3})

        # Test frozenset
        result = _to_serializable(frozenset([1, 2, 3]))
        self.assertIsInstance(result, list)
        self.assertEqual(set(result), {1, 2, 3})

    def test_to_serializable_dict(self) -> None:
        """Test _to_serializable with dictionaries."""
        # Test simple dict
        result = _to_serializable({"key": "value", "num": 42})
        self.assertEqual(result, {"key": "value", "num": 42})

        # Test nested dict
        result = _to_serializable({"nested": {"key": "value"}})
        self.assertEqual(result, {"nested": {"key": "value"}})

        # Test dict with non-string keys
        result = _to_serializable({1: "one", 2: "two"})
        self.assertEqual(result, {"1": "one", "2": "two"})

    def test_to_serializable_msg(self) -> None:
        """Test _to_serializable with Msg objects."""
        msg = Msg("user", [TextBlock(type="text", text="Hello")], "user")
        result = _to_serializable(msg)
        self.assertIsInstance(result, str)
        self.assertIn("Msg", result)

    def test_to_serializable_pydantic_model(self) -> None:
        """Test _to_serializable with Pydantic models."""
        model = ExamplePydanticModel(name="test", age=42)
        result = _to_serializable(model)
        self.assertIsInstance(result, str)
        self.assertIn("ExamplePydanticModel", result)

        # Test Pydantic class
        result = _to_serializable(ExamplePydanticModel)
        self.assertIsInstance(result, str)

    def test_to_serializable_dataclass(self) -> None:
        """Test _to_serializable with dataclasses."""
        obj = ExampleDataClass(name="test", age=42)
        result = _to_serializable(obj)
        self.assertIsInstance(result, str)
        self.assertIn("ExampleDataClass", result)

    def test_to_serializable_datetime(self) -> None:
        """Test _to_serializable with datetime objects."""
        # Test date
        date_obj = datetime.date(2024, 1, 1)
        result = _to_serializable(date_obj)
        self.assertEqual(result, "2024-01-01")

        # Test datetime
        dt_obj = datetime.datetime(2024, 1, 1, 12, 30, 45)
        result = _to_serializable(dt_obj)
        self.assertEqual(result, "2024-01-01T12:30:45")

        # Test time
        time_obj = datetime.time(12, 30, 45)
        result = _to_serializable(time_obj)
        self.assertEqual(result, "12:30:45")

    def test_to_serializable_timedelta(self) -> None:
        """Test _to_serializable with timedelta objects."""
        delta = datetime.timedelta(days=1, hours=2, minutes=30)
        result = _to_serializable(delta)
        self.assertIsInstance(result, (int, float))
        self.assertGreater(result, 0)

    def test_to_serializable_enum(self) -> None:
        """Test _to_serializable with enum objects."""
        result = _to_serializable(ExampleEnum.VALUE1)
        self.assertEqual(result, "value1")

        result = _to_serializable(ExampleEnum.VALUE2)
        self.assertEqual(result, 42)

    def test_to_serializable_unknown_type(self) -> None:
        """Test _to_serializable with unknown types."""

        class CustomClass:
            """Custom class for testing."""

            def __init__(self) -> None:
                self.value = "test"

        obj = CustomClass()
        result = _to_serializable(obj)
        self.assertIsInstance(result, str)

    def test_to_serializable_nested_structures(self) -> None:
        """Test _to_serializable with nested structures."""
        data = {
            "list": [1, 2, {"nested": "value"}],
            "tuple": (1, 2, 3),
            "set": {1, 2, 3},
        }
        result = _to_serializable(data)
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result["list"], list)
        self.assertIsInstance(result["tuple"], list)
        self.assertIsInstance(result["set"], list)

    def test_serialize_to_str_simple(self) -> None:
        """Test _serialize_to_str with simple types."""
        # Test string
        result = _serialize_to_str("hello")
        self.assertEqual(result, '"hello"')

        # Test integer
        result = _serialize_to_str(42)
        self.assertEqual(result, "42")

        # Test boolean
        result = _serialize_to_str(True)
        self.assertEqual(result, "true")

        # Test None
        result = _serialize_to_str(None)
        self.assertEqual(result, "null")

    def test_serialize_to_str_list(self) -> None:
        """Test _serialize_to_str with lists."""
        numbers = [1, 2, 3]
        result = _serialize_to_str(numbers)
        self.assertEqual(result, json.dumps(numbers))

        strings = ["a", "b", "c"]
        result = _serialize_to_str(strings)
        self.assertEqual(result, json.dumps(strings))

    def test_serialize_to_str_dict(self) -> None:
        """Test _serialize_to_str with dictionaries."""
        result = _serialize_to_str({"key": "value", "num": 42})
        self.assertIn("key", result)
        self.assertIn("value", result)
        self.assertIn("num", result)
        self.assertIn("42", result)

    def test_serialize_to_str_non_serializable(self) -> None:
        """Test _serialize_to_str with non-serializable objects."""
        msg = Msg("user", [TextBlock(type="text", text="Hello")], "user")
        result = _serialize_to_str(msg)
        self.assertIsInstance(result, str)
        self.assertIn("Msg", result)

    def test_serialize_to_str_unicode(self) -> None:
        """Test _serialize_to_str with unicode characters."""
        result = _serialize_to_str("hi")
        self.assertIn("hi", result)

    def test_serialize_to_str_complex_nested(self) -> None:
        """Test _serialize_to_str with complex nested structures."""
        data = {
            "list": [1, 2, {"nested": "value"}],
            "datetime": datetime.datetime(2024, 1, 1),
            "enum": ExampleEnum.VALUE1,
        }
        result = _serialize_to_str(data)
        self.assertIsInstance(result, str)
        self.assertIn("list", result)
