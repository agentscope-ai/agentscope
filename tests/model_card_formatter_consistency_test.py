# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Consistency between model cards and the formatters that serve them.

A card's ``input_types`` is the capability the platform advertises, and it is
also what ``Read`` is wired to (``Read(model_input_types=card.input_types)``).
The formatters and the agent, however, gate attachments on
``formatter.supported_input_media_types``. When a card advertises a media type
its formatter cannot emit, the attachment is dropped before it reaches the
model: the agent degrades it to a workspace reminder, and a direct model call
loses it with nothing but a log warning. The two lists therefore have to agree.
"""

from fnmatch import fnmatch
import unittest

from agentscope.credential import CredentialFactory

# Media types that never carry an attachment, so they are always rendered as
# text and are not gated by the formatter's media support.
_TEXTUAL_MEDIA_TYPES = ("text/plain", "application/x-thinking")


class TestModelCardFormatterConsistency(unittest.TestCase):
    """Every media type a model card advertises must be formattable."""

    def test_card_media_types_are_supported_by_their_formatter(self) -> None:
        """Check all registered providers.

        The credential type resolves both the model class and, through it, the
        formatter that serves its cards, so the check follows the same path the
        service takes when it lists models.
        """
        checked_providers = 0

        for credential_cls in CredentialFactory._classes:
            model_cls = credential_cls.get_chat_model_class()
            cards = model_cls.list_models()

            try:
                # Every registered credential accepts the extra ``api_key``
                # field, and no model builds a client before ``warm_up()``.
                model = model_cls(
                    credential=credential_cls(api_key="test"),
                    model=cards[0].name,
                )
            except ImportError:
                # Provider SDKs are optional extras; CI installs ``[full]``.
                continue

            supported = model.formatter.supported_input_media_types
            for card in cards:
                for media_type in card.input_types:
                    if media_type in _TEXTUAL_MEDIA_TYPES:
                        continue
                    with self.subTest(
                        provider=credential_cls.__name__,
                        model=card.name,
                        media_type=media_type,
                    ):
                        self.assertTrue(
                            any(fnmatch(media_type, p) for p in supported),
                            f"Model card '{card.name}' advertises "
                            f"'{media_type}' but "
                            f"{type(model.formatter).__name__} supports "
                            f"{supported}. Attachments of that type are "
                            f"dropped before reaching the model.",
                        )
            checked_providers += 1

        self.assertGreater(
            checked_providers,
            0,
            "No provider could be constructed; the check verified nothing.",
        )


if __name__ == "__main__":
    unittest.main()
